# -*- coding: utf-8 -*-
# 阅读导航：相似案例检索
# 本文件作用：硬过滤 → 向量与BM25召回 → RRF融合 → 重排 → 原文依据核对 → 返回有支持的案例。
# 建议读法：search_similar是主入口；trace记录过程，signal区分无候选、范围外、依据不足和处理失败。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 能力边界：相似案例只能支持台账正文实际记载的内容；没有处置记录时不能推断“以前怎么处理”。

"""停电台账问数：相似事件检索（混合检索 + 重排）

在 outage_events 集合上，用 bge-m3 向量 + BM25 混合召回，再 bge-reranker 精排，
返回与用户描述最相似的历史停电事件（带事件级结构化属性）。

与问数内核（ask.py，走 SQL）互补：
  - ask.py  解决「数值统计」：某线路停了几次、某供服中心缺供电量多少
  - similar.py 解决「语义相似」：有没有类似的停电事件、这种情况以前怎么处理的

设计要点：
  - 检索单位=事件（语料构建时已按事件去重合并备注，避免重复记录占满召回）
  - 返回带中文语义的事件属性（FIELD_MAP 英转中），支撑「文本证据 + 结构化佐证」联合回答
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入math：数值合法性等数学操作。
import math
# 学习注释：导入time：耗时测量。
import time
# 学习注释：从concurrent.futures引入ThreadPoolExecutor，用于把同步工作交给专用线程执行。
from concurrent.futures import ThreadPoolExecutor

# 学习注释：从pymilvus引入AnnSearchRequest, Function, FunctionType, MilvusClient, RRFRanker，用于与Milvus向量库通信、定义检索与字段结构。
from pymilvus import AnnSearchRequest, Function, FunctionType, MilvusClient, RRFRanker

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings
# 学习注释：从app.core导入embeddings, rerank, versioning, scope, relevance，复用对应模块的实现。
from app.core import embeddings, rerank, versioning, scope, relevance
# 学习注释：从app.core导入evidence并简称evidence_mod，复用对应模块的实现。
from app.core import evidence as evidence_mod

# 学习注释：'outage_events'；保存到COLLECTION。
COLLECTION = "outage_events"
# 学习注释：当前Embedding向量有1024维；查询与建库的维度和模型必须一致。
DIM = 1024

# 复用 milvus_client 的「专用单线程」套路：pymilvus 同步客户端跨线程共享会间歇空返回
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="milvus-event")
# 学习注释：None（尚无值）；保存到_CLIENT。
_CLIENT: MilvusClient | None = None

# 英文字段名 -> 中文业务名（见 vectorize_events.py 的 FIELD_MAP，保持一致）
FIELD_MAP = {
    "event_id": "事件序号",
    "outage_type": "停电性质",
    "outage_type_detail": "停电性质明细",
    "type_conflict": "停电性质冲突",
    "voltage_level": "电压等级",
    "supply_center": "供服中心",
    "supply_center_detail": "供服中心明细",
    "center_conflict": "供服中心冲突",
    "line_names": "线路名称",
    "line_count": "线路数",
    "user_count": "涉及用户数",
    "start_time": "起始时间",
    "end_time": "终止时间",
    "household_hours": "时户数合计",
    "energy_shortage": "缺供电量合计",
}
# 学习注释：计算 list(FIELD_MAP.keys()) + ['text']；保存到_OUTPUT。
_OUTPUT = list(FIELD_MAP.keys()) + ["text"]  # text=事件文本，标注/溯源要展示


# 学习注释：把同步Milvus工作放到专用线程，避免阻塞异步事件循环。
async def _acall(fn, *args, **kwargs):
    # 学习注释：取得负责调度当前异步任务的事件循环。
    loop = asyncio.get_running_loop()
    # 学习注释：把同步Milvus函数放到专用线程，避免它占住HTTP请求所在的事件循环。
    return await loop.run_in_executor(_EXECUTOR, lambda: fn(*args, **kwargs))


# 学习注释：懒加载并复用Milvus客户端；它的创建和使用由专用线程承接。
def _get_client() -> MilvusClient:
    # 学习注释：声明下方修改的是模块级共享变量_CLIENT，而非函数局部同名变量。
    global _CLIENT
    # 学习注释：只有_CLIENT尚未设置（None）才进入这一分支。
    if _CLIENT is None:
        # 学习注释：创建连接指定Milvus地址的客户端；保存到_CLIENT。
        _CLIENT = MilvusClient(uri=settings.milvus_uri)
    # 学习注释：返回_CLIENT，结束本次函数调用。
    return _CLIENT


# 学习注释：对相同范围执行Dense和BM25检索，用RRF按排名融合，再取候选。
def _hybrid_search_sync(client, vec, text, top_k, recall=50, expr=None, collection=COLLECTION):
    """dense + BM25 混合召回（RRF 融合），返回原始 hit。

    expr: 可选的范围约束表达式（线路/时间过滤），在召回阶段硬过滤。
    """
    # 学习注释：设置向量召回：用COSINE比较问题向量与事件向量，且先应用同一过滤expr。
    dense_req = AnnSearchRequest(data=[vec], anns_field="dense",
                                 param={"metric_type": "COSINE"}, limit=recall, expr=expr)
    # 学习注释：设置BM25关键词召回：按问题文字匹配sparse字段，也使用相同过滤范围。
    sparse_req = AnnSearchRequest(data=[text], anns_field="sparse",
                                  param={"metric_type": "BM25"}, limit=recall, expr=expr)
    # 学习注释：执行两路检索并用RRF按排名融合；这里产生候选，不代表候选已确认相关。
    res = client.hybrid_search(collection, reqs=[dense_req, sparse_req],
                               ranker=RRFRanker(), limit=top_k, output_fields=_OUTPUT)
    # 学习注释：本次只提交一个查询，Milvus外层结果按查询分组，因此取第一组候选。
    return res[0]


# 学习注释：把Milvus英文字段转换为业务中文字段，同时保留正文和召回分。
def _to_hit(h: dict) -> dict:
    """把 Milvus hit 转成带中文语义的事件字典。"""
    # 学习注释：h中键/位置为'entity'的值；保存到e。
    e = h["entity"]
    # 学习注释：空字典；保存到out。
    out = {}
    # 学习注释：按映射把Milvus的英文字段名转成业务中文字段，避免两端字段含义错位。
    for en, cn in FIELD_MAP.items():
        # 学习注释：从e读取键en，缺少时取None；保存到out[cn]。
        out[cn] = e.get(en)
    # 学习注释：保留融合召回分，仅用于追踪；它不是最终相关概率。
    out["_score"] = float(h["distance"])
    # 学习注释：保留原始事件正文，后续引用校验和来源展示都依赖这段文本。
    out["事件文本"] = e.get("text", "")  # 事件备注原文，供标注/溯源展示
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：定义RetrievalError异常类型，供调用者区分本模块的处理失败。
class RetrievalError(RuntimeError):
    # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
    pass


# 学习注释：把允许的事件ID编码为Milvus过滤表达式；JSON编码处理字符串转义。
def _expr_for_ids(ids: list[str]) -> str:
    # 学习注释：用JSON编码事件ID列表生成Milvus过滤条件，不直接拼接未转义的用户字符串。
    return "event_id in " + json.dumps(ids, ensure_ascii=False)


# 学习注释：把合法的业务条件转换成Milvus事件ID过滤条件。
def _build_filter_expr(filters: dict | None) -> str | None:
    # 学习注释：scope.prepare_scope的执行结果；保存到校验后的范围信息（prepared）。
    prepared = scope.prepare_scope(filters)
    # 学习注释：只有prepared['coverage_status']等于'out'才进入这一分支。
    if prepared["coverage_status"] == "out":
        # 学习注释：返回_expr_for_ids的执行结果，结束本次函数调用。
        return _expr_for_ids([])
    # 学习注释：prepared中键/位置为'effective_filters'的值；保存到实际生效的筛选条件（effective）。
    effective = prepared["effective_filters"]
    # 学习注释：只有不满足（检查是否至少有一项条件成立）才进入这一分支。
    if not any(k in effective for k in ("line", "supply_center", "time_range")):
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：返回_expr_for_ids的执行结果，结束本次函数调用。
    return _expr_for_ids(scope.eligible_event_ids(effective))


# 学习注释：执行完整检索并记录轨迹；不放宽用户范围，不把服务错误当无结果。
@versioning.pinned_dataset
async def search_similar(query: str, top_k: int = 5, filters: dict | None = None,
                         with_signal: bool = False) -> list[dict] | dict:
    """一次调用同时记录重排前候选、最终结果和耗时；范围错误不放宽检索。

    默认 record_start 与 SQL 事件计数一致。仅显式 event_overlap 才按事件包络重叠。
    with_signal=True 返回包含 signal 和 trace 的结果，处理失败标记 processing_error；
    旧 list 接口在处理失败时抛异常，不能把失败当空结果。
    """
    # 学习注释：只有不满足（检查用户查询文本（query）是否属于str类型），或者不满足（清理query首尾或指定一侧的字符）才进入这一分支。
    if not isinstance(query, str) or not query.strip():
        # 学习注释：以异常终止这一条路径：'检索描述不能为空'；上层不能把它当作正常成功。
        raise ValueError("检索描述不能为空")
    # 学习注释：严格要求整数且在1～50之间；bool也是int的子类，所以这里不用isinstance放行True。
    if type(top_k) is not int or not 1 <= top_k <= 50:
        # 学习注释：以异常终止这一条路径：'top_k 必须是 1 到 50 的整数'；上层不能把它当作正常成功。
        raise ValueError("top_k 必须是 1 到 50 的整数")
    # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本轮计时起点（started）。
    started = time.perf_counter()
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：scope.prepare_scope的执行结果；保存到校验后的范围信息（prepared）。
    prepared = scope.prepare_scope(filters, snapshot)
    # 学习注释：记录同一次检索的版本、候选、规则、模型和各阶段耗时，供验收复现。
    trace = {
        "request_id": versioning.current_request_id(), "dataset_version": snapshot.version,
        "collection": snapshot.collection, "top_k": top_k,
        "candidates": [], "returned_ids": [], "timings_ms": {},
        "relevance_policy": relevance.POLICY_VERSION,
        "relevance_prompt_sha256": relevance.PROMPT_SHA256,
        "relevance_model": settings.chat_model,
        **prepared,
    }

    # 学习注释：统一收尾：记录最终ID和耗时，构造状态，按调用方式返回案例或完整轨迹。
    def finish(events, candidate_count, state=None, error_stage=None):
        # 学习注释：收尾时记录最终实际展示的事件；与重排前候选ID分开保存。
        trace["returned_ids"] = [str(e["事件序号"]) for e in events]
        # 学习注释：用单调时钟差计算总耗时，秒乘1000转毫秒。
        trace["timings_ms"]["total"] = round((time.perf_counter() - started) * 1000, 2)
        # 学习注释：只有error_stage为真/非空才进入这一分支。
        if error_stage:
            # 学习注释：error_stage；保存到trace['error_stage']。
            trace["error_stage"] = error_stage
        # 学习注释：_build_signal的执行结果；保存到检索状态对象（signal）。
        signal = _build_signal(events, candidate_count, prepared, state, error_stage,
                               trace.get("relevance_review", []))
        # 学习注释：兼容旧调用者只拿案例列表；但服务失败仍抛异常，不能假装空列表就是无案例。
        if not with_signal:
            # 学习注释：只有signal.signal等于evidence_mod的SIGNAL_PROCESSING_ERROR属性（SIGNAL_PROCESSING_ERROR）才进入这一分支。
            if signal.signal == evidence_mod.SIGNAL_PROCESSING_ERROR:
                # 学习注释：以异常终止这一条路径：signal的scope_detail属性（scope_detail）；上层不能把它当作正常成功。
                raise RetrievalError(signal.scope_detail)
            # 学习注释：返回事件列表（events），结束本次函数调用。
            return events
        # 学习注释：完整接口同时返回事件、状态和轨迹，上层能区分“没找到”与“查失败”。
        return {"events": events, "signal": signal, "trace": trace}

    # 学习注释：只有prepared['coverage_status']等于'out'才进入这一分支。
    if prepared["coverage_status"] == "out":
        # 学习注释：返回finish的执行结果，结束本次函数调用。
        return finish([], 0, evidence_mod.SIGNAL_OUT_OF_SCOPE)
    # 学习注释：prepared中键/位置为'effective_filters'的值；保存到实际生效的筛选条件（effective）。
    effective = prepared["effective_filters"]
    # 学习注释：None（尚无值）；保存到满足范围条件的事件ID（eligible）。
    eligible = None
    # 学习注释：只有检查是否至少有一项条件成立才进入这一分支。
    if any(k in effective for k in ("line", "supply_center", "time_range")):
        # 学习注释：先从本版本SQLite确定允许返回哪些事件，再交给Milvus执行硬过滤。
        eligible = scope.eligible_event_ids(effective, snapshot)
        # 学习注释：满足范围条件的事件ID（eligible）；保存到trace['eligible_event_ids']。
        trace["eligible_event_ids"] = eligible
        # 学习注释：只有满足范围条件的事件ID（eligible）为空或为False才进入这一分支。
        if not eligible:
            # 学习注释：返回finish的执行结果，结束本次函数调用。
            return finish([], 0, evidence_mod.SIGNAL_EMPTY)
    # 学习注释：有范围限制就生成ID白名单；没有范围限制才传None表示不加ID筛选。
    expr = _expr_for_ids(eligible) if eligible is not None else None
    # 学习注释：'embedding'；保存到stage。
    stage = "embedding"
    # 学习注释：空列表；保存到召回候选（hits）。
    hits = []
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：防止用另一个向量模型查询旧集合；维度相同也不代表语义坐标兼容。
        if snapshot.embed_model and snapshot.embed_model != settings.embed_model:
            # 学习注释：以异常终止这一条路径：'查询向量模型与发布版本不一致'；上层不能把它当作正常成功。
            raise RetrievalError("查询向量模型与发布版本不一致")
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
        t0 = time.perf_counter()
        # 学习注释：调用Embedding把用户问题变成数字向量，供dense召回使用。
        vec = await embeddings.embed_query(query)
        # 学习注释：调用round，处理计算 (time.perf_counter() - t0) * 1000；保存到trace['timings_ms']['embedding']。
        trace["timings_ms"]["embedding"] = round((time.perf_counter() - t0) * 1000, 2)
        # 学习注释：'recall'；保存到stage。
        stage = "recall"
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
        t0 = time.perf_counter()

        # 学习注释：函数work接收调用时的上下文；把这一段可复用处理封装起来。
        def work():
            # 客户端的创建和调用都放在同一专用线程。
            return _hybrid_search_sync(_get_client(), vec, query, top_k * 4,
                                       expr=expr, collection=snapshot.collection)

        # 学习注释：在专用线程执行混合召回，await等待候选返回。
        hits = await _acall(work)
        # 学习注释：调用round，处理计算 (time.perf_counter() - t0) * 1000；保存到trace['timings_ms']['recall']。
        trace["timings_ms"]["recall"] = round((time.perf_counter() - t0) * 1000, 2)
        # 学习注释：遍历召回候选（hits），将str(h['entity']['event_id'])收集为列表；保存到召回事件ID列表（candidate_ids）。
        candidate_ids = [str(h["entity"]["event_id"]) for h in hits]
        # 学习注释：同一事件不应重复占据候选位置；发现重复时按处理错误返回。
        if len(set(candidate_ids)) != len(candidate_ids):
            # 学习注释：以异常终止这一条路径：'召回含重复事件'；上层不能把它当作正常成功。
            raise RetrievalError("召回含重复事件")
        # 学习注释：再次检查召回ID全部属于白名单；这是对向量过滤结果的防御性核验。
        if eligible is not None and not set(candidate_ids).issubset(set(eligible)):
            # 学习注释：以异常终止这一条路径：'召回结果违反硬过滤范围'；上层不能把它当作正常成功。
            raise RetrievalError("召回结果违反硬过滤范围")
        # 学习注释：遍历zip(candidate_ids, hits)，将{'event_id': eid, 'rrf_score': float(h['distance'])}收集为列表；保存到trace['candidates']。
        trace["candidates"] = [
            {"event_id": eid, "rrf_score": float(h["distance"])}
            for eid, h in zip(candidate_ids, hits)
        ]
        # 学习注释：只有召回候选（hits）为空或为False才进入这一分支。
        if not hits:
            # 学习注释：返回finish的执行结果，结束本次函数调用。
            return finish([], 0, evidence_mod.SIGNAL_EMPTY)
        # 学习注释：'rerank'；保存到stage。
        stage = "rerank"
        # 学习注释：空列表；保存到候选正文列表（docs）。
        docs = []
        # 学习注释：遍历召回候选（hits），每轮把当前项交给h。
        for h in hits:
            # 学习注释：h中键/位置为'entity'的值；保存到e。
            e = h["entity"]
            # 学习注释：向候选正文列表（docs）末尾添加把花括号内的变量填入f字符串，生成文字。
            docs.append(f"{e.get('text', '')} {e.get('outage_type', '')} "
                        f"{e.get('voltage_level', '')} {e.get('line_names', '')}")
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
        t0 = time.perf_counter()
        # 学习注释：把问题与候选文本交给重排模型，只取前top_k；没有在此使用分数阈值。
        ranked = await rerank.rerank(query, docs, top_n=top_k)
        # 学习注释：调用round，处理计算 (time.perf_counter() - t0) * 1000；保存到trace['timings_ms']['rerank']。
        trace["timings_ms"]["rerank"] = round((time.perf_counter() - t0) * 1000, 2)
        # 学习注释：有候选却完全没有重排结果属于处理异常，不能直接解释成没有相关事件。
        if not ranked:
            # 学习注释：以异常终止这一条路径：'已有候选但重排返回为空'；上层不能把它当作正常成功。
            raise RetrievalError("已有候选但重排返回为空")
        # 学习注释：只有len(ranked)大于期望最多返回的案例数（top_k）才进入这一分支。
        if len(ranked) > top_k:
            # 学习注释：以异常终止这一条路径：'重排返回数量超过请求'；上层不能把它当作正常成功。
            raise RetrievalError("重排返回数量超过请求")
        # 学习注释：2项元组：空列表、转为不重复元素集合；保存到事件列表（events）、seen。
        events, seen = [], set()
        # 学习注释：遍历重排后的索引和分数（ranked），每轮把当前项交给idx、召回阶段分数（score）。
        for idx, score in ranked:
            # 学习注释：重排索引必须指向原候选且不重复，否则无法可靠地把分数对应回事件。
            if type(idx) is not int or not 0 <= idx < len(hits) or idx in seen:
                # 学习注释：以异常终止这一条路径：'重排索引无效或重复'；上层不能把它当作正常成功。
                raise RetrievalError("重排索引无效或重复")
            # 学习注释：拒绝NaN和无穷大分数，避免排序和报告出现无效数值。
            if not math.isfinite(float(score)):
                # 学习注释：以异常终止这一条路径：'重排分数无效'；上层不能把它当作正常成功。
                raise RetrievalError("重排分数无效")
            # 学习注释：调用seen.add，处理idx。
            seen.add(idx)
            # 学习注释：_to_hit的执行结果；保存到当前事件（event）。
            event = _to_hit(hits[idx])
            # 学习注释：把召回阶段分数（score）转换为浮点数；保存到event['_rerank_score']。
            event["_rerank_score"] = float(score)
            # 学习注释：向事件列表（events）末尾添加当前事件（event）。
            events.append(event)
        # 学习注释：遍历事件列表（events），将str(e['事件序号'])收集为列表；保存到trace['reranked_ids']。
        trace["reranked_ids"] = [str(e["事件序号"]) for e in events]
        # 学习注释：'relevance'；保存到stage。
        stage = "relevance"
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
        t0 = time.perf_counter()
        # 学习注释：仅对重排选出的候选做原文依据核对；未检查到的后续候选不会自动补进来。
        reviews = await relevance.review_candidates(query, events)
        # 学习注释：要求核对结果与重排事件顺序完全一致，避免把A事件的判定贴到B事件。
        if [r["event_id"] for r in reviews] != trace["reranked_ids"]:
            # 学习注释：以异常终止这一条路径：'相关性核对与重排事件不一致'；上层不能把它当作正常成功。
            raise RetrievalError("相关性核对与重排事件不一致")
        # 学习注释：候选依据判定（reviews）；保存到trace['relevance_review']。
        trace["relevance_review"] = reviews
        # 学习注释：调用round，处理计算 (time.perf_counter() - t0) * 1000；保存到trace['timings_ms']['relevance']。
        trace["timings_ms"]["relevance"] = round((time.perf_counter() - t0) * 1000, 2)
        # 学习注释：空列表；保存到通过依据核对的案例（accepted）。
        accepted = []
        # 学习注释：遍历将多组数据按位置配对；循环到最短一组结束，每轮把当前项交给当前事件（event）、review。
        for event, review in zip(events, reviews):
            # 学习注释：只放行supported；unsupported和uncertain都保留在轨迹中，但不作为支持案例展示。
            if review["decision"] == "supported":
                # 学习注释：review；保存到event['_relevance']。
                event["_relevance"] = review
                # 学习注释：向通过依据核对的案例（accepted）末尾添加当前事件（event）。
                accepted.append(event)
        # 学习注释：有通过案例则正常返回；全部扣留则返回insufficient_evidence，与无候选不同。
        return finish(accepted, len(hits), None if accepted else evidence_mod.SIGNAL_INSUFFICIENT)
    # 学习注释：统一捕获外部服务或协议异常；记录失败阶段和类型，不把异常原文泄露到响应。
    except Exception as exc:
        # 不把上游响应原文写入返回体，避免日志/报告携带敏感请求内容。
        trace["error_type"] = type(exc).__name__
        # 学习注释：返回finish的执行结果，结束本次函数调用。
        return finish([], len(hits), evidence_mod.SIGNAL_PROCESSING_ERROR, stage)


# 学习注释：把检索状态、数量、属性冲突和模型初审说明整理成统一信号。
def _build_signal(events, candidate_count, prepared, state=None, error_stage=None, reviews=None):
    # 学习注释：空列表；保存到conflicts。
    conflicts = []
    # 学习注释：遍历事件列表（events），每轮把当前项交给当前事件（event）。
    for event in events:
        # 学习注释：空列表；保存到fields。
        fields = []
        # 学习注释：只有从event读取键'停电性质冲突'，缺少时取None才进入这一分支。
        if event.get("停电性质冲突"):
            # 学习注释：向fields末尾添加'停电性质'。
            fields.append("停电性质")
        # 学习注释：只有从event读取键'供服中心冲突'，缺少时取None才进入这一分支。
        if event.get("供服中心冲突"):
            # 学习注释：向fields末尾添加'供服中心'。
            fields.append("供服中心")
        # 学习注释：只有fields为真/非空才进入这一分支。
        if fields:
            # 学习注释：向conflicts末尾添加包含event_id、fields的字典。
            conflicts.append({"event_id": event["事件序号"], "fields": "、".join(fields)})
    # 学习注释：遍历事件列表（events），只保留满足条件的项，将e['_rerank_score']收集为列表；保存到scores。
    scores = [e["_rerank_score"] for e in events if e.get("_rerank_score") is not None]
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：None（尚无值）；保存到detail。
    detail = None
    # 学习注释：只有state等于evidence_mod的SIGNAL_OUT_OF_SCOPE属性（SIGNAL_OUT_OF_SCOPE）才进入这一分支。
    if state == evidence_mod.SIGNAL_OUT_OF_SCOPE:
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到detail。
        detail = f"请求时间不在台账声明范围 [{snapshot.coverage_start}, {snapshot.coverage_end}) 内"
    # 学习注释：只有state等于evidence_mod的SIGNAL_EMPTY属性（SIGNAL_EMPTY）才进入这一分支。
    elif state == evidence_mod.SIGNAL_EMPTY:
        # 学习注释：'本次在有效范围内未召回候选；抽样台账不能据此断言现实中没有此类事件'；保存到detail。
        detail = "本次在有效范围内未召回候选；抽样台账不能据此断言现实中没有此类事件"
    # 学习注释：只有state等于evidence_mod的SIGNAL_PROCESSING_ERROR属性（SIGNAL_PROCESSING_ERROR）才进入这一分支。
    elif state == evidence_mod.SIGNAL_PROCESSING_ERROR:
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到detail。
        detail = f"{error_stage or '检索'}阶段未完成，无法判断是否存在相似事件"
    # 学习注释：只有state等于evidence_mod的SIGNAL_INSUFFICIENT属性（SIGNAL_INSUFFICIENT）才进入这一分支。
    elif state == evidence_mod.SIGNAL_INSUFFICIENT:
        # 学习注释：'已召回候选，但本次核对未得到原文依据充分的相似案例；不代表该范围或现实中没有此类事件'；保存到detail。
        detail = "已召回候选，但本次核对未得到原文依据充分的相似案例；不代表该范围或现实中没有此类事件"
    # 学习注释：versioning.evidence_context的执行结果；保存到context。
    context = versioning.evidence_context()
    # 学习注释：用prepared中键/位置为'warnings'的值更新context['warnings']；下方+=等写法把计算结果存回原变量。
    context["warnings"] += prepared["warnings"]
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（reviews or []）；保存到候选依据判定（reviews）。
    reviews = reviews or []
    # 学习注释：统计本轮模型判为缺少支持的条数；True按1累计。
    rejected = sum(r["decision"] == "unsupported" for r in reviews)
    # 学习注释：单列含义待确认的条数，不把它们当作已确认不相关。
    uncertain = sum(r["decision"] == "uncertain" for r in reviews)
    # 学习注释：只有候选依据判定（reviews）为真/非空才进入这一分支。
    if reviews:
        # 学习注释：向context['warnings']末尾添加'相关性为模型初审，已核对引用在原文存在，未经人工确认'。
        context["warnings"].append("相关性为模型初审，已核对引用在原文存在，未经人工确认")
    # 学习注释：只有rejected为真/非空，或者uncertain为真/非空才进入这一分支。
    if rejected or uncertain:
        # 学习注释：向context['warnings']末尾添加把花括号内的变量填入f字符串，生成文字。
        context["warnings"].append(f"本次重排候选中{rejected}条缺少相关支持、{uncertain}条含义待确认，未作为已支持案例展示；未放宽筛选范围")
    # 学习注释：返回调用evidence_mod.RetrievalSignal，结束本次函数调用。
    return evidence_mod.RetrievalSignal(
        **context, signal=state, has_conflict=bool(conflicts), conflicts=conflicts,
        candidate_count=candidate_count, top_rerank_score=max(scores) if scores else None,
        scope_detail=detail, requested_filters=prepared["requested_filters"],
        effective_filters=prepared["effective_filters"],
        coverage_status=prepared["coverage_status"], time_semantics=prepared["time_semantics"],
        reviewed_count=len(reviews), unsupported_count=rejected, uncertain_count=uncertain,
    )
