# 阅读导航：固定题集验收
# 本文件作用：真实检索一次，同时检查范围、来源、标签和拒绝行为，保存pass/fail/unverified报告。
# 建议读法：会调用真实模型；v2与v3预期不同，AI标签不是人工金标准，不能把8题通过当业务准确率。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""固定检索回归：一次检索轨迹，明确 pass/fail/unverified，失败退出非零。

0=全部通过，1=至少一个失败，2=无失败但仍有未验证项。
AI 初审标签只用于小样本诊断；未标注结果不计为相关性通过。
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入subprocess：启动独立进程或调用Git等命令。
import subprocess
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：导入time：耗时测量。
import time
# 学习注释：从datetime引入datetime，用于日期时间解析与计算。
from datetime import datetime
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：调用sys.path.insert，处理0。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 学习注释：从app.core导入similar, scope, versioning, relevance，复用对应模块的实现。
from app.core import similar, scope, versioning, relevance
# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings

# 学习注释：Path(__file__).resolve().parents中键/位置为2的值；保存到BASE。
BASE = Path(__file__).resolve().parents[2]
# 学习注释：按目录层级拼接文件路径 BASE / 'tests/data/similar_label_ai_review_v2_20261116.json'；保存到AI_REVIEW。
AI_REVIEW = BASE / "tests/data/similar_label_ai_review_v2_20261116.json"
# 学习注释：按目录层级拼接文件路径 BASE / 'data/evaluations'；保存到OUTPUT_DIR。
OUTPUT_DIR = BASE / "data/evaluations"
# 学习注释：8项列表：包含id、query、filters、expect的字典、包含id、query、filters、expect的字典、包含id、query、filters、expect的字典、包含id、query、f…；保存到FIXED_SET_V2。
FIXED_SET_V2 = [
    {"id": "N1", "query": "电缆故障导致的停电", "filters": None, "expect": "hit"},
    {"id": "N2", "query": "开关跳闸的故障", "filters": None, "expect": "hit"},
    {"id": "N3", "query": "计划检修停电", "filters": None, "expect": "hit"},
    {"id": "R1", "query": "电缆故障导致的停电", "filters": {"line": "线路32"}, "expect": "hit"},
    {"id": "R2", "query": "开关跳闸的故障",
     "filters": {"time_range": {"start": "2025-06-01", "end": "2025-07-01"}}, "expect": "hit"},
    {"id": "I1", "query": "电缆故障导致的停电",
     "filters": {"line": "线路9999"}, "expect": "invalid_filters"},
    {"id": "E1", "query": "电缆故障导致的停电",
     "filters": {"line": "线路32", "time_range": {"start": "2025-01-01", "end": "2025-01-02"}},
     "expect": "empty"},
    {"id": "O1", "query": "电缆故障导致的停电",
     "filters": {"time_range": {"start": "2024-01-01", "end": "2024-02-01"}}, "expect": "out_of_scope"},
]
# v3依据已补审的负例修订R1：范围内的施工候选不能冒充电缆故障。
# v2及历史报告保留，可显式重跑旧“必须命中”的预期；不可混用两版通过率。
FIXED_SET = [{**item, "expect": "insufficient_evidence"} if item["id"] == "R1" else dict(item)
             for item in FIXED_SET_V2]


# 学习注释：函数_load_ground_truth接收调用时的上下文；把这一段可复用处理封装起来。
def _load_ground_truth() -> dict[str, dict[str, int]]:
    # 学习注释：只有不满足（检查路径是否存在）才进入这一分支。
    if not AI_REVIEW.exists():
        # 学习注释：返回空字典，结束本次函数调用。
        return {}
    # 学习注释：把JSON文本解析为Python对象；保存到raw。
    raw = json.loads(AI_REVIEW.read_text(encoding="utf-8"))
    # 学习注释：检查raw是否属于dict类型时取从raw读取键'queries'，缺少时取空列表，否则取raw；保存到queries。
    queries = raw.get("queries", []) if isinstance(raw, dict) else raw
    # 学习注释：返回遍历queries，将item['query']→{str(label['event_id']): label['similar'] for label in …收集为字典，结束本次函数调用。
    return {
        item["query"]: {str(label["event_id"]): label["similar"]
                        for label in item.get("labels", []) if label.get("similar") in (0, 1)}
        for item in queries
    }


# 学习注释：函数_load_sources接收调用时的上下文；把这一段可复用处理封装起来。
def _load_sources() -> dict[str, dict]:
    # 学习注释：versioning.current_snapshot()的事件语料文件路径属性（corpus_path）；保存到path。
    path = versioning.current_snapshot().corpus_path
    # 学习注释：返回遍历(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())，将str(item['事件序号'])→item收集为字典，结束本次函数调用。
    return {str(item["事件序号"]): item for item in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


# 学习注释：将绑定版本与语料哈希的补充标签合并；null明确覆盖旧确定标签。
def _apply_review(path: Path, gt: dict) -> dict:
    """Apply a version-bound review by case; null explicitly overrides earlier labels."""
    # 学习注释：把JSON文本解析为Python对象；保存到review。
    review = json.loads(path.read_text(encoding="utf-8"))
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：只有review.get('dataset_version')不等于snapshot的版本号属性（version），或者review.get('corpus_sha256')不等于versioning.file_sha256的执行结果才进入这一分支。
    if (review.get("dataset_version") != snapshot.version or
            review.get("corpus_sha256") != versioning.file_sha256(snapshot.corpus_path)):
        # 学习注释：以异常终止这一条路径：'补标注与当前数据版本/语料不一致，请重新复核'；上层不能把它当作正常成功。
        raise ValueError("补标注与当前数据版本/语料不一致，请重新复核")
    # 学习注释：遍历FIXED_SET，将item['id']→item收集为字典；保存到案例列表（cases）。
    cases = {item["id"]: item for item in FIXED_SET}
    # 学习注释：遍历gt.items()，将key→dict(value)收集为字典；保存到merged。
    merged = {key: dict(value) for key, value in gt.items()}
    # 学习注释：转为不重复元素集合；保存到seen。
    seen = set()
    # 学习注释：遍历review中键/位置为'cases'的值，每轮把当前项交给item。
    for item in review["cases"]:
        # 学习注释：item中键/位置为'id'的值；保存到case_id。
        case_id = item["id"]
        # 学习注释：从cases读取键case_id，缺少时取None；保存到case。
        case = cases.get(case_id)
        # 学习注释：只有case尚未设置（None），或者case_id包含在seen，或者item['query']不等于case中键/位置为'query'的值，或者item.get('filters')不等于从case读取键'filters'，缺少时取None才进入这一分支。
        if (case is None or case_id in seen or item["query"] != case["query"] or
                item.get("filters") != case.get("filters")):
            # 学习注释：以异常终止这一条路径：'补标注题目或筛选条件不匹配/重复'；上层不能把它当作正常成功。
            raise ValueError("补标注题目或筛选条件不匹配/重复")
        # 学习注释：调用seen.add，处理case_id。
        seen.add(case_id)
        # 学习注释：创建或浅复制字典：从gt读取键case中键/位置为'query'的值，缺少时取空字典；保存到labels。
        labels = dict(gt.get(case["query"], {}))
        # 学习注释：转为不重复元素集合；保存到event_ids。
        event_ids = set()
        # 学习注释：遍历item中键/位置为'labels'的值，每轮把当前项交给label。
        for label in item["labels"]:
            # 学习注释：2项元组：label中键/位置为'similar'的值、把label中键/位置为'event_id'的值转换为字符串；保存到value、事件标识（event_id）。
            value, event_id = label["similar"], str(label["event_id"])
            # 学习注释：只有value不包含在3项元组：0、1、None（尚无值），或者事件标识（event_id）包含在event_ids才进入这一分支。
            if value not in (0, 1, None) or event_id in event_ids:
                # 学习注释：以异常终止这一条路径：'补标注标签非法或事件重复'；上层不能把它当作正常成功。
                raise ValueError("补标注标签非法或事件重复")
            # 学习注释：调用event_ids.add，处理事件标识（event_id）。
            event_ids.add(event_id)
            # 学习注释：value；保存到labels[event_id]。
            labels[event_id] = value
        # 学习注释：labels；保存到merged['case:' + case_id]。
        merged["case:" + case_id] = labels
    # 学习注释：返回merged，结束本次函数调用。
    return merged


# 学习注释：函数_check_scope接收事件列表（events）、筛选条件（filters）；把这一段可复用处理封装起来。
def _check_scope(events: list[dict], filters: dict | None) -> bool | None:
    # 学习注释：只有筛选条件（filters）为空或为False才进入这一分支。
    if not filters:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：scope.prepare_scope的执行结果；保存到校验后的范围信息（prepared）。
    prepared = scope.prepare_scope(filters)
    # 学习注释：只有prepared['coverage_status']等于'out'才进入这一分支。
    if prepared["coverage_status"] == "out":
        # 学习注释：返回事件列表（events）为空或为False，结束本次函数调用。
        return not events
    # 学习注释：转为不重复元素集合：scope.eligible_event_ids的执行结果；保存到allowed。
    allowed = set(scope.eligible_event_ids(prepared["effective_filters"]))
    # 从原始行计算同口径的事件集合，不把完整时间戳截断为日期。
    return all(str(event.get("事件序号", "")) in allowed for event in events)


# 学习注释：比对返回事件正文和关键属性与当前语料是否一致，不能只检查ID存在。
def _source_matches(event: dict, sources: dict) -> bool:
    # 学习注释：从sources读取键把从event读取键'事件序号'，缺少时取''转换为字符串，缺少时取None；保存到source。
    source = sources.get(str(event.get("事件序号", "")))
    # 学习注释：只有source尚未设置（None）才进入这一分支。
    if source is None:
        # 学习注释：返回False（不成立），结束本次函数调用。
        return False
    # 比对引用正文及关键属性，不仅检查 ID/正文非空。
    fields = ("事件文本", "线路名称", "起始时间", "终止时间", "停电性质",
              "停电性质冲突", "供服中心冲突")
    # 学习注释：返回检查是否每一项条件都成立，结束本次函数调用。
    return all(event.get(key) == source.get(key) for key in fields)


# 学习注释：只执行一次该题检索，并用同一轨迹核对范围、来源、标签和误拒绝。
@versioning.pinned_dataset
async def _run_one(item: dict, gt: dict, top_k: int = 5, sources: dict | None = None) -> dict:
    # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本轮计时起点（started）。
    started = time.perf_counter()
    # 学习注释：包含status、reasons、dataset_version的字典；保存到out。
    out = {**item, "status": "fail", "reasons": [],
           "dataset_version": versioning.current_dataset_version()}
    # 学习注释：执行可能失败的步骤；except处理异常，finally无论成功失败都负责清理。
    try:
        # 参数错误也必须在此处验证；不把后续服务故障当作“非法参数测试通过”。
        try:
            # 学习注释：scope.prepare_scope的执行结果。
            scope.prepare_scope(item.get("filters"))
        except ValueError:
            # 学习注释：只有item['expect']等于'invalid_filters'才进入这一分支。
            if item["expect"] == "invalid_filters":
                # 学习注释：将新字段合并到out，同名字段会覆盖旧值。
                out.update(status="pass", signal="invalid_filters", relevance_status="not_applicable")
                # 学习注释：返回out，结束本次函数调用。
                return out
            # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
            raise
        # 学习注释：只有item['expect']等于'invalid_filters'才进入这一分支。
        if item["expect"] == "invalid_filters":
            # 学习注释：向out['reasons']末尾添加'非法过滤参数没有被拒绝'。
            out["reasons"].append("非法过滤参数没有被拒绝")
            # 学习注释：返回out，结束本次函数调用。
            return out
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
        result = await similar.search_similar(item["query"], top_k=top_k,
                                             filters=item.get("filters"), with_signal=True)
        # 学习注释：3项元组：result中键/位置为'events'的值、result中键/位置为'signal'的值、result中键/位置为'trace'的值；保存到事件列表（events）、检索状态对象（signal）、检索过程记录（trace）。
        events, signal, trace = result["events"], result["signal"], result["trace"]
        # 学习注释：将新字段合并到out，同名字段会覆盖旧值。
        out.update(
            signal=signal.signal, has_conflict=signal.has_conflict,
            candidate_count=signal.candidate_count, returned_count=len(events),
            returned_ids=[str(event["事件序号"]) for event in events],
            trace=trace, scope_ok=_check_scope(events, item.get("filters")),
        )
        # 学习注释：item中键/位置为'expect'的值；保存到期望状态（expected）。
        expected = item["expect"]
        # 学习注释：期望状态（expected）等于'hit'时取按and短路取第一个假值；都为真则取最后一个值（bool(events) and signal.signal is None），否则取按and短路取第一个假值；都为真则取最后一个值（signal.signal == expected and (not events)）；保存到expected_ok。
        expected_ok = (bool(events) and signal.signal is None) if expected == "hit" else (
            signal.signal == expected and not events)
        # 学习注释：只有expected_ok为空或为False才进入这一分支。
        if not expected_ok:
            # 学习注释：向out['reasons']末尾添加'实际结果不符合题目期望'。
            out["reasons"].append("实际结果不符合题目期望")
        # 学习注释：只有signal.signal等于'processing_error'才进入这一分支。
        if signal.signal == "processing_error":
            # 学习注释：向out['reasons']末尾添加'检索处理未完成'。
            out["reasons"].append("检索处理未完成")
        # 学习注释：只有out['scope_ok']是同一对象/空值False（不成立）才进入这一分支。
        if out["scope_ok"] is False:
            # 学习注释：向out['reasons']末尾添加'返回事件不在声明范围内'。
            out["reasons"].append("返回事件不在声明范围内")
        # 学习注释：遍历事件列表（events），只保留满足条件的项，将str(e['事件序号'])收集为列表；保存到conflict_events。
        conflict_events = [
            str(e["事件序号"]) for e in events if e.get("停电性质冲突") or e.get("供服中心冲突")
        ]
        # 学习注释：只有bool(conflict_events)不等于signal的has_conflict属性（has_conflict）才进入这一分支。
        if bool(conflict_events) != signal.has_conflict:
            # 学习注释：向out['reasons']末尾添加'属性冲突标记未正确保留'。
            out["reasons"].append("属性冲突标记未正确保留")
        # 学习注释：conflict_events；保存到out['conflict_events']。
        out["conflict_events"] = conflict_events
        # 学习注释：sources已经有值（不是None）时取sources，否则取调用_load_sources；保存到sources。
        sources = sources if sources is not None else _load_sources()
        # 学习注释：事件列表（events）为真/非空时取检查是否每一项条件都成立，否则取None（尚无值）；保存到out['source_match']。
        out["source_match"] = all(_source_matches(e, sources) for e in events) if events else None
        # 学习注释：只有out['source_match']是同一对象/空值False（不成立）才进入这一分支。
        if out["source_match"] is False:
            # 学习注释：向out['reasons']末尾添加'引用事件/正文/属性与当前语料版本不一致'。
            out["reasons"].append("引用事件/正文/属性与当前语料版本不一致")
        # 学习注释：从gt读取键计算 'case:' + item['id']，缺少时取从gt读取键item中键/位置为'query'的值，缺少时取空字典；保存到当前问题的参考标签（q_gt）。
        q_gt = gt.get("case:" + item["id"], gt.get(item["query"], {}))
        # 学习注释：out中键/位置为'returned_ids'的值；保存到ids。
        ids = out["returned_ids"]
        # 学习注释：遍历ids，只保留满足条件的项，将q_gt[eid]收集为列表；保存到有确定标签的返回位置（labeled）。
        labeled = [q_gt[eid] for eid in ids if q_gt.get(eid) in (0, 1)]
        # 学习注释：累计各项数值（布尔值True按1计数）；保存到out['ambiguous_count']。
        out["ambiguous_count"] = sum(eid in q_gt and q_gt[eid] is None for eid in ids)
        # 学习注释：累计各项数值（布尔值True按1计数）；保存到out['missing_label_count']。
        out["missing_label_count"] = sum(eid not in q_gt for eid in ids)
        # 学习注释：计算 len(ids) - len(labeled)；保存到out['unlabeled_count']。
        out["unlabeled_count"] = len(ids) - len(labeled)
        # 学习注释：有确定标签的返回位置（labeled）为真/非空时取计算 sum(labeled) / len(labeled)，否则取None（尚无值）；保存到out['precision_labeled']。
        out["precision_labeled"] = sum(labeled) / len(labeled) if labeled else None
        # 学习注释：遍历trace['candidates']，将candidate['event_id']收集为集合；保存到recalled。
        recalled = {candidate["event_id"] for candidate in trace["candidates"]}
        # 学习注释：遍历q_gt.items()，只保留满足条件的项，将eid收集为集合；保存到已知正例ID集合（positives）。
        positives = {eid for eid, label in q_gt.items() if label == 1}
        # 学习注释：只有从item读取键'filters'，缺少时取None才进入这一分支。
        if item.get("filters"):
            # 学习注释：用转为不重复元素集合：scope.eligible_event_ids的执行结果更新已知正例ID集合（positives）；下方+=等写法把计算结果存回原变量。
            positives &= set(scope.eligible_event_ids(trace["effective_filters"]))
        # 学习注释：按指定键排序后得到新列表；保存到out['known_positive_misses']。
        out["known_positive_misses"] = sorted(positives - recalled)
        # 学习注释：已知正例ID集合（positives）为真/非空时取计算 len(positives & recalled) / len(positives)，否则取None（尚无值）；保存到out['known_positive_recall']。
        out["known_positive_recall"] = len(positives & recalled) / len(positives) if positives else None
        # 学习注释：遍历ids，只保留满足条件的项，将eid收集为列表；保存到out['known_irrelevant_returned']。
        out["known_irrelevant_returned"] = [eid for eid in ids if q_gt.get(eid) == 0]
        # 学习注释：遍历trace.get('relevance_review', [])，将r['event_id']→r收集为字典；保存到reviewed。
        reviewed = {r["event_id"]: r for r in trace.get("relevance_review", [])}
        # 学习注释：遍历reviewed，只保留满足条件的项，将eid收集为列表；保存到out['known_positive_withheld']。
        out["known_positive_withheld"] = [eid for eid in reviewed if q_gt.get(eid) == 1 and eid not in ids]
        # 学习注释：遍历reviewed，只保留满足条件的项，将eid收集为列表；保存到out['known_negative_withheld']。
        out["known_negative_withheld"] = [eid for eid in reviewed if q_gt.get(eid) == 0 and eid not in ids]
        # 学习注释：遍历reviewed，只保留满足条件的项，将eid收集为列表；保存到out['ambiguous_withheld']。
        out["ambiguous_withheld"] = [eid for eid in reviewed if eid in q_gt and q_gt[eid] is None and eid not in ids]
        # 学习注释：遍历reviewed，只保留满足条件的项，将eid收集为列表；保存到out['reviewed_unknown_labels']。
        out["reviewed_unknown_labels"] = [eid for eid in reviewed if eid not in q_gt or q_gt[eid] is None]
        # 学习注释：只有out['known_positive_withheld']为真/非空才进入这一分支。
        if out["known_positive_withheld"]:
            # 学习注释：向out['reasons']末尾添加'依据核对未展示已标注正例，需检查误拒绝'。
            out["reasons"].append("依据核对未展示已标注正例，需检查误拒绝")
        # 学习注释：'not_applicable'；保存到out['relevance_status']。
        out["relevance_status"] = "not_applicable"
        # 学习注释：只有期望状态（expected）等于'hit'才进入这一分支。
        if expected == "hit":
            # 学习注释：只有out['known_irrelevant_returned']为真/非空才进入这一分支。
            if out["known_irrelevant_returned"]:
                # 学习注释：'fail'；保存到out['relevance_status']。
                out["relevance_status"] = "fail"
                # 学习注释：向out['reasons']末尾添加'最终结果含已标注不相关事件'。
                out["reasons"].append("最终结果含已标注不相关事件")
            # 学习注释：只有out['unlabeled_count']为真/非空，或者有确定标签的返回位置（labeled）为空或为False才进入这一分支。
            elif out["unlabeled_count"] or not labeled:
                # 学习注释：'unverified'；保存到out['relevance_status']。
                out["relevance_status"] = "unverified"
            else:
                # 学习注释：'pass'；保存到out['relevance_status']。
                out["relevance_status"] = "pass"
        # 学习注释：只有期望状态（expected）等于'insufficient_evidence'才进入这一分支。
        elif expected == "insufficient_evidence":
            # 学习注释：只有reviewed为空或为False，或者不满足（out['candidate_count']为真/非空）才进入这一分支。
            if not reviewed or not out["candidate_count"]:
                # 学习注释：向out['reasons']末尾添加'依据不足必须有已核对的召回候选，不能用空检索充当拒绝'。
                out["reasons"].append("依据不足必须有已核对的召回候选，不能用空检索充当拒绝")
            # 学习注释：只有out['reviewed_unknown_labels']为真/非空才进入这一分支。
            elif out["reviewed_unknown_labels"]:
                # 学习注释：'unverified'；保存到out['relevance_status']。
                out["relevance_status"] = "unverified"
            else:
                # 学习注释：out['known_positive_withheld']为真/非空时取'fail'，否则取'pass'；保存到out['relevance_status']。
                out["relevance_status"] = "fail" if out["known_positive_withheld"] else "pass"
        # 学习注释：out['relevance_status']等于'unverified'；保存到unverified。
        unverified = out["relevance_status"] == "unverified"
        # 学习注释：只有versioning.current_snapshot().consistency不等于'verified_bundle'才进入这一分支。
        if versioning.current_snapshot().consistency != "verified_bundle":
            # 学习注释：'旧布局尚未验证跨库版本一致性'；保存到out['verification_note']。
            out["verification_note"] = "旧布局尚未验证跨库版本一致性"
            # 学习注释：True（成立）；保存到unverified。
            unverified = True
        # 学习注释：out['reasons']为真/非空时取'fail'，否则取unverified为真/非空时取'unverified'，否则取'pass'；保存到out['status']。
        out["status"] = "fail" if out["reasons"] else "unverified" if unverified else "pass"
    except Exception as exc:
        # 学习注释：'fail'；保存到out['status']。
        out["status"] = "fail"
        # 学习注释：type(exc)的__name__属性（__name__）；保存到out['error_type']。
        out["error_type"] = type(exc).__name__
        # 学习注释：向out['reasons']末尾添加'评测或检索调用异常，详查对应阶段；不计为通过'。
        out["reasons"].append("评测或检索调用异常，详查对应阶段；不计为通过")
    finally:
        # 学习注释：调用round，处理计算 (time.perf_counter() - started) * 1000；保存到out['elapsed_ms']。
        out["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：保存当前源码哈希和Git状态，让评测结果能追溯到具体代码。
def _code_metadata() -> dict:
    # 学习注释：按指定键排序后得到新列表；保存到files。
    files = sorted(set(BASE.glob("app/**/*.py")) | set(BASE.glob("scripts/**/*.py")))
    # 学习注释：遍历files，将path.relative_to(BASE).as_posix()→versioning.file_sha256(path)收集为字典；保存到hashes。
    hashes = {path.relative_to(BASE).as_posix(): versioning.file_sha256(path) for path in files}
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：清理subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=BASE, capture_output=True, text=True, check=True, timeout=10).stdout首尾或指定一侧的字符；保存到commit。
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE, capture_output=True,
                                text=True, check=True, timeout=10).stdout.strip()
        # 学习注释：把subprocess.run(['git', 'status', '--porcelain'], cwd=BASE, capture_output=True, text=True, check=True, timeout=10)的stdout属性（stdout）转换为真假值；保存到dirty。
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=BASE, capture_output=True,
                                    text=True, check=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError):
        # 学习注释：2项元组：None（尚无值）、None（尚无值）；保存到commit、dirty。
        commit, dirty = None, None
    # 学习注释：返回包含commit、dirty、source_sha256的字典，结束本次函数调用。
    return {"commit": commit, "dirty": dirty, "source_sha256": hashes}


# 学习注释：函数_render_md接收结果列表（results）、meta；把这一段可复用处理封装起来。
def _render_md(results: list[dict], meta: dict) -> str:
    # 学习注释：9项列表：'# 固定检索回归'、''、把花括号内的变量填入f字符串，生成文字、把花括号内的变量填入f字符串，生成文字、''、'0=全部通过；1=存在失败；2=仍有未验证项。AI 初审标签未经人工全量复核，不外…；保存到lines。
    lines = [
        "# 固定检索回归", "", f"运行：{meta['run_id']}；数据版本：{meta['dataset_version']}",
        f"判定：{meta['status']}；退出码：{meta['exit_code']}", "",
        "0=全部通过；1=存在失败；2=仍有未验证项。AI 初审标签未经人工全量复核，不外推业务准确率。",
        "", "| ID | 判定 | 期望 | 信号 | 返回/候选 | 范围 | 来源一致 | 已标注精度 | 耗时(ms) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    # 学习注释：遍历结果列表（results），每轮把当前项交给r。
    for r in results:
        # 学习注释：向lines末尾添加把花括号内的变量填入f字符串，生成文字。
        lines.append(
            f"| {r['id']} | {r['status']} | {r['expect']} | {r.get('signal')} | "
            f"{r.get('returned_count', 0)}/{r.get('candidate_count', 0)} | {r.get('scope_ok')} | "
            f"{r.get('source_match')} | {r.get('precision_labeled')} | {r['elapsed_ms']} |")
    # 学习注释：把3项列表：''、'## 问题与边界'、''的各元素追加到lines。
    lines.extend(["", "## 问题与边界", ""])
    # 学习注释：遍历结果列表（results），每轮把当前项交给r。
    for r in results:
        # 学习注释：计算 r.get('reasons', []) + ([r['verification_note']] if r.get('verification_note') else [])；保存到notes。
        notes = r.get("reasons", []) + ([r["verification_note"]] if r.get("verification_note") else [])
        # 学习注释：只有r.get('relevance_status')等于'unverified'才进入这一分支。
        if r.get("relevance_status") == "unverified":
            # 学习注释：向notes末尾添加把花括号内的变量填入f字符串，生成文字。
            notes.append(f"相关性仍待确认：缺失标签 {r.get('missing_label_count', r.get('unlabeled_count', 0))}，歧义标签 {r.get('ambiguous_count', 0)}")
        # 学习注释：只有notes为真/非空才进入这一分支。
        if notes:
            # 学习注释：向lines末尾添加计算 f"- {r['id']}: " + '；'.join(notes)。
            lines.append(f"- {r['id']}: " + "；".join(notes))
    # 学习注释：把5项列表：''、'候选与最终结果来自同一次检索。known_positive_misses 是已标注正例的召回漏检，'、'不是系统拒答；source_match 核对返回事件正文和属性，不代表生成回答的每一句…的各元素追加到lines。
    lines.extend([
        "", "候选与最终结果来自同一次检索。known_positive_misses 是已标注正例的召回漏检，",
        "不是系统拒答；source_match 核对返回事件正文和属性，不代表生成回答的每一句引用都已验证。",
        "耗时为本题总耗时，trace.timings_ms 单独记录检索各阶段耗时。未使用未经校准的分数拒答阈值。",
        "固定集不保证每次召回属性冲突事件；冲突及故障分支由离线回归测试补充覆盖。",
    ])
    # 学习注释：只有从meta读取键'baseline_comparison'，缺少时取None才进入这一分支。
    if meta.get("baseline_comparison"):
        # 学习注释：把4项列表：''、'## 与基线比较'、''、把Python对象编码成JSON文本的各元素追加到lines。
        lines.extend(["", "## 与基线比较", "", json.dumps(meta["baseline_comparison"], ensure_ascii=False)])
    # 学习注释：返回计算 '\n'.join(lines) + '\n'，结束本次函数调用。
    return "\n".join(lines) + "\n"


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
@versioning.pinned_dataset
async def main(top_k: int = 5, output_dir: Path | None = None, baseline: Path | None = None,
               labels: Path | None = None, set_version: str = "fixed-set-v3") -> int:
    # 学习注释：调用_load_ground_truth；保存到参考标签映射（gt）。
    gt = _load_ground_truth()
    # 学习注释：只有labels为真/非空才进入这一分支。
    if labels:
        # 学习注释：_apply_review的执行结果；保存到参考标签映射（gt）。
        gt = _apply_review(labels, gt)
    # 任一题出错仍继续写完整报告；不会让第二阶段异常抹掉前面结果。
    cases = FIXED_SET_V2 if set_version == "fixed-set-v2" else FIXED_SET
    # 学习注释：遍历案例列表（cases），将await _run_one(item, gt, top_k=top_k)收集为列表；保存到结果列表（results）。
    results = [await _run_one(item, gt, top_k=top_k) for item in cases]
    # 学习注释：检查是否至少有一项条件成立；保存到failed。
    failed = any(r["status"] == "fail" for r in results)
    # 学习注释：检查是否至少有一项条件成立；保存到unverified。
    unverified = any(r["status"] == "unverified" for r in results)
    # 学习注释：failed为真/非空时取1，否则取unverified为真/非空时取2，否则取0；保存到exit_code。
    exit_code = 1 if failed else 2 if unverified else 0
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：包含run_id、date、dataset_version、data_consistency、set_version、top_k的字典；保存到meta。
    meta = {
        "run_id": versioning.new_version(), "date": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": snapshot.version, "data_consistency": snapshot.consistency,
        "set_version": set_version, "top_k": top_k,
        "models": {"embedding": settings.embed_model, "reranker": settings.rerank_model},
        "retrieval": {"dense_recall": 50, "sparse_recall": 50, "fusion_limit": top_k * 4,
                      "time_semantics": "record_start"},
        "evidence_check": {"version": relevance.POLICY_VERSION, "model": settings.chat_model,
                           "prompt_sha256": relevance.PROMPT_SHA256, "human_validated": False},
        "ground_truth": {"file": AI_REVIEW.name, "status": "ai_provisional",
                         "sha256": versioning.file_sha256(AI_REVIEW) if AI_REVIEW.exists() else None},
        "code": _code_metadata(),
        "status": "fail" if failed else "unverified" if unverified else "pass",
        "exit_code": exit_code,
    }
    # 学习注释：只有labels为真/非空才进入这一分支。
    if labels:
        # 学习注释：把JSON文本解析为Python对象；保存到review。
        review = json.loads(labels.read_text(encoding="utf-8"))
        # 学习注释：包含file、sha256、annotator_type、human_validated的字典；保存到meta['ground_truth']['supplement']。
        meta["ground_truth"]["supplement"] = {
            "file": labels.name, "sha256": versioning.file_sha256(labels),
            "annotator_type": review.get("annotator_type"),
            "human_validated": review.get("human_validated", False),
        }
    # 学习注释：只有baseline为真/非空才进入这一分支。
    if baseline:
        # 学习注释：把JSON文本解析为Python对象；保存到previous。
        previous = json.loads(baseline.read_text(encoding="utf-8"))
        # 学习注释：previous中键/位置为'meta'的值；保存到previous_meta。
        previous_meta = previous["meta"]
        # 学习注释：检查是否每一项条件都成立；保存到comparable。
        comparable = all(previous_meta.get(k) == meta[k] for k in
                         ("dataset_version", "set_version", "top_k", "models", "retrieval", "ground_truth", "evidence_check"))
        # 学习注释：遍历previous['results']，将r['id']→r['status']收集为字典；保存到old。
        old = {r["id"]: r["status"] for r in previous["results"]}
        # 学习注释：遍历结果列表（results），只保留满足条件的项，将r['id']收集为列表；保存到regressions。
        regressions = [r["id"] for r in results if old.get(r["id"]) == "pass" and r["status"] != "pass"]
        # 学习注释：包含comparable、regressions、note的字典；保存到meta['baseline_comparison']。
        meta["baseline_comparison"] = {"comparable": comparable,
                                       "regressions": regressions if comparable else [],
                                       "note": "" if comparable else "数据/题集/模型/标注配置不同，不自动判定退化"}
    # 学习注释：按目录层级拼接文件路径 (output_dir or OUTPUT_DIR) / meta['run_id']；保存到本轮报告目录（folder）。
    folder = (output_dir or OUTPUT_DIR) / meta["run_id"]
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    folder.mkdir(parents=True, exist_ok=False)
    # 学习注释：versioning.atomic_json的执行结果。
    versioning.atomic_json(folder / "result.json", {"meta": meta, "results": results})
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (folder / "report.md").write_text(_render_md(results, meta), encoding="utf-8")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"评测 {meta['status']}；结果：{folder}；退出码：{exit_code}")
    # 学习注释：返回exit_code，结束本次函数调用。
    return exit_code


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--top-k'。
    parser.add_argument("--top-k", type=int, default=5, choices=range(1, 51))
    # 学习注释：调用parser.add_argument，处理'--output-dir'。
    parser.add_argument("--output-dir", type=Path, default=None)
    # 学习注释：调用parser.add_argument，处理'--baseline'。
    parser.add_argument("--baseline", type=Path, default=None)
    # 学习注释：调用parser.add_argument，处理'--labels'。
    parser.add_argument("--labels", type=Path, default=None, help="与当前版本绑定的补审标签 JSON")
    # 学习注释：调用parser.add_argument，处理'--set-version'。
    parser.add_argument("--set-version", choices=["fixed-set-v2", "fixed-set-v3"], default="fixed-set-v3")
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：调用sys.exit，处理启动事件循环，执行脚本的异步主函数。
    sys.exit(asyncio.run(main(args.top_k, args.output_dir, args.baseline, args.labels, args.set_version)))
