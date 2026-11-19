# -*- coding: utf-8 -*-
# 阅读导航：向量版本构建
# 本文件作用：把事件文本向量化，在新Milvus集合写入正文、属性、向量和BM25字段，再读回验证。
# 建议读法：会调用Embedding服务并写入Milvus；只建新集合，不负责切换当前业务版本。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：把事件级语料向量化写入 Milvus 独立版本集合

复用项目已有的 Milvus 基建思路（dense 向量 + text 中文分词 + BM25 sparse），
集合名 outage_events_<版本号>，schema 用事件级字段。此脚本不切换当前发布清单。

字段设计：
  - dense: bge-m3 向量（1024 维，事件文本）
  - text: 事件文本（供 BM25 分词检索）
  - sparse: BM25 Function 输出
  - 标量字段：事件序号、停电性质、电压等级、供服中心、线路名称、线路数、
    涉及用户数、起始/终止时间、时户数合计、缺供电量合计

检索命中后能直接带出事件的结构化属性，支撑「结构化统计 + 文本证据联合回答」。

运行：python -m scripts.query.vectorize_events；完整发布使用 python -m scripts.query.publish。
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：调用sys.path.insert，处理0。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 学习注释：从pymilvus引入DataType, Function, FunctionType, MilvusClient，用于与Milvus向量库通信、定义检索与字段结构。
from pymilvus import DataType, Function, FunctionType, MilvusClient

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings
# 学习注释：从app.core导入embeddings，复用对应模块的实现。
from app.core import embeddings

# 学习注释：'outage_events'；保存到COLLECTION。
COLLECTION = "outage_events"
# 学习注释：1024；保存到DIM。
DIM = 1024
# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'data' / 'masked' / 'event_corpus.jsonl'；保存到CORPUS_PATH。
CORPUS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "masked" / "event_corpus.jsonl"

# Milvus 字段名必须以字母/下划线开头（不支持中文开头），故用英文字段名，
# 中文语义列名（对应 gridmate.db 的列名）在 Python 层做映射，不丢业务语义。
# 检索命中时要带出的标量字段（英文名）
_OUTPUT = [
    "event_id", "outage_type", "voltage_level", "supply_center", "line_names", "line_count",
    "user_count", "start_time", "end_time", "household_hours", "energy_shortage",
]

# 英文字段名 -> 中文业务名（返回给上层/展示用）
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


# 学习注释：函数_client接收调用时的上下文；把这一段可复用处理封装起来。
def _client() -> MilvusClient:
    # 学习注释：返回创建连接指定Milvus地址的客户端，结束本次函数调用。
    return MilvusClient(uri=settings.milvus_uri)


# 学习注释：把语料 + 向量拼成 Milvus 行（校验维度/字段齐全后才写入）。
def _build_rows(corpus: list[dict], vecs: list[list[float]]) -> list[dict]:
    """把语料 + 向量拼成 Milvus 行（校验维度/字段齐全后才写入）。"""
    # 学习注释：空列表；保存到查询或表格行（rows）。
    rows = []
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、e、vec。
    for i, (e, vec) in enumerate(zip(corpus, vecs)):
        # 学习注释：只有len(vec)不等于DIM才进入这一分支。
        if len(vec) != DIM:
            # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
            raise RuntimeError(
                f"第 {i} 条向量维度 {len(vec)} != {DIM}，中止，避免写入残缺向量")
        # 学习注释：向查询或表格行（rows）末尾添加包含id、dense、text、event_id、outage_type、outage_type_detail的字典。
        rows.append({
            "id": i,
            "dense": vec,
            "text": e["事件文本"],
            "event_id": e["事件序号"],
            "outage_type": e["停电性质"],
            "outage_type_detail": e.get("停电性质明细", ""),
            "type_conflict": bool(e.get("停电性质冲突", False)),
            "voltage_level": e["电压等级"],
            "supply_center": e["供服中心"],
            "supply_center_detail": e.get("供服中心明细", ""),
            "center_conflict": bool(e.get("供服中心冲突", False)),
            "line_names": e["线路名称"],
            "line_count": e["线路数"],
            "user_count": e["涉及用户数"],
            "start_time": e["起始时间"],
            "end_time": e["终止时间"],
            "household_hours": e["时户数合计"],
            "energy_shortage": e["缺供电量合计"],
        })
    # 学习注释：返回查询或表格行（rows），结束本次函数调用。
    return rows


# 学习注释：读回向量集合的事件ID和正文，逐项核对对应语料。
def verify_collection(client, collection: str, corpus: list[dict]) -> int:
    """读回所有事件 ID/正文，防止只核对条数而漏掉内容不一致。"""
    # 学习注释：调用client.load_collection，处理Milvus集合名（collection）。
    client.load_collection(collection)
    # 学习注释：把client.query(collection, filter='', output_fields=['count(*)'], consistency_level='Strong')[0]中键/位置为'count(*)'的值转换为整数；保存到count。
    count = int(client.query(collection, filter="", output_fields=["count(*)"],
                             consistency_level="Strong")[0]["count(*)"])
    # 学习注释：遍历事件级语料（corpus），将str(e['事件序号'])→e['事件文本']收集为字典；保存到期望状态（expected）。
    expected = {str(e["事件序号"]): e["事件文本"] for e in corpus}
    # 学习注释：只有期望状态（expected）为空或为False，或者len(expected)不等于计算事件级语料（corpus）的元素数量，或者count不等于计算事件级语料（corpus）的元素数量才进入这一分支。
    if not expected or len(expected) != len(corpus) or count != len(corpus):
        # 学习注释：以异常终止这一条路径：'集合条数或语料事件唯一性校验失败'；上层不能把它当作正常成功。
        raise RuntimeError("集合条数或语料事件唯一性校验失败")
    # 学习注释：空字典；保存到actual。
    actual = {}
    # 学习注释：调用client.query_iterator；保存到iterator。
    iterator = client.query_iterator(collection_name=collection, batch_size=512,
                                     filter="", output_fields=["event_id", "text"],
                                     consistency_level="Strong")
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：在True为真/非空期间重复下面步骤；循环体负责推进或退出。
        while True:
            # 学习注释：调用iterator.next；保存到当前处理的一批候选（batch）。
            batch = iterator.next()
            # 学习注释：只有当前处理的一批候选（batch）为空或为False才进入这一分支。
            if not batch:
                # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                break
            # 学习注释：遍历当前处理的一批候选（batch），每轮把当前项交给row。
            for row in batch:
                # 学习注释：把row中键/位置为'event_id'的值转换为字符串；保存到eid。
                eid = str(row["event_id"])
                # 学习注释：只有eid包含在actual才进入这一分支。
                if eid in actual:
                    # 学习注释：以异常终止这一条路径：'集合存在重复事件'；上层不能把它当作正常成功。
                    raise RuntimeError("集合存在重复事件")
                # 学习注释：row中键/位置为'text'的值；保存到actual[eid]。
                actual[eid] = row["text"]
    finally:
        # 学习注释：关闭iterator占用的连接或资源。
        iterator.close()
    # 学习注释：只有actual不等于期望状态（expected）才进入这一分支。
    if actual != expected:
        # 学习注释：以异常终止这一条路径：'集合事件 ID/正文与本版本语料不一致'；上层不能把它当作正常成功。
        raise RuntimeError("集合事件 ID/正文与本版本语料不一致")
    # 学习注释：返回count，结束本次函数调用。
    return count


# 学习注释：调用Embedding构造新版本向量集合并读回检查；本函数不切换业务版本。
async def vectorize(corpus_path: Path = CORPUS_PATH, collection: str | None = None) -> dict:
    """只创建新版本集合；不删除/重命名旧集合，也不切换当前版本。"""
    # 学习注释：从app.core导入versioning，复用对应模块的实现。
    from app.core import versioning
    # 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
    import re
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（collection or 'outage_events_' + versioning.new_version()）；保存到Milvus集合名（collection）。
    collection = collection or "outage_events_" + versioning.new_version()
    # 学习注释：只有不满足（调用re.fullmatch，处理'outage_events_[A-Za-z0-9_]+'）才进入这一分支。
    if not re.fullmatch(r"outage_events_[A-Za-z0-9_]+", collection):
        # 学习注释：以异常终止这一条路径：'必须使用独立版本集合名，不能覆盖 outage_events'；上层不能把它当作正常成功。
        raise ValueError("必须使用独立版本集合名，不能覆盖 outage_events")
    # 学习注释：遍历Path(corpus_path).read_text(encoding='utf-8').splitlines()，只保留满足条件的项，将json.loads(line)收集为列表；保存到事件级语料（corpus）。
    corpus = [json.loads(line) for line in Path(corpus_path).read_text(encoding="utf-8").splitlines()
              if line.strip()]
    # 学习注释：只有事件级语料（corpus）为空或为False，或者len({e['事件序号'] for e in corpus})不等于计算事件级语料（corpus）的元素数量才进入这一分支。
    if not corpus or len({e["事件序号"] for e in corpus}) != len(corpus):
        # 学习注释：以异常终止这一条路径：'语料为空或含重复事件'；上层不能把它当作正常成功。
        raise ValueError("语料为空或含重复事件")
    # 学习注释：调用_client；保存到外部服务客户端（client）。
    client = _client()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：只有调用client.has_collection，处理Milvus集合名（collection）才进入这一分支。
        if client.has_collection(collection):
            # 学习注释：以异常终止这一条路径：'目标集合已存在，拒绝覆盖'；上层不能把它当作正常成功。
            raise ValueError("目标集合已存在，拒绝覆盖")
        # 学习注释：embeddings.embed_texts的执行结果，await等待结果后再继续；保存到vecs。
        vecs = await embeddings.embed_texts([e["事件文本"] for e in corpus])
        # 学习注释：只有len(vecs)不等于计算事件级语料（corpus）的元素数量才进入这一分支。
        if len(vecs) != len(corpus):
            # 学习注释：以异常终止这一条路径：'向量条数与语料条数不一致'；上层不能把它当作正常成功。
            raise RuntimeError("向量条数与语料条数不一致")
        # 学习注释：调用_build_rows，处理事件级语料（corpus）；保存到查询或表格行（rows）。
        rows = _build_rows(corpus, vecs)
        # 学习注释：调用_create_collection，处理外部服务客户端（client）。
        _create_collection(client, collection)
        # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给当前批次起点（offset）。
        for offset in range(0, len(rows), 200):
            # 学习注释：调用client.upsert，处理Milvus集合名（collection）。
            client.upsert(collection, rows[offset:offset + 200])
        # 学习注释：把Python文件缓冲送交操作系统。
        client.flush(collection)
        # 学习注释：verify_collection的执行结果；保存到count。
        count = verify_collection(client, collection, corpus)
        # 学习注释：调用client.search；保存到召回候选（hits）。
        hits = client.search(collection_name=collection, data=[vecs[0]],
                             anns_field="dense", limit=1,
                             search_params={"metric_type": "COSINE"},
                             consistency_level="Strong")
        # 学习注释：只有召回候选（hits）为空或为False，或者不满足（hits[0]为真/非空）才进入这一分支。
        if not hits or not hits[0]:
            # 学习注释：以异常终止这一条路径：'新集合向量查询验证失败'；上层不能把它当作正常成功。
            raise RuntimeError("新集合向量查询验证失败")
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"新集合已验证：{collection}，共 {count} 个事件（尚未切换发布清单）")
        # 学习注释：返回包含collection、rows的字典，结束本次函数调用。
        return {"collection": collection, "rows": count}
    finally:
        # 学习注释：关闭client占用的连接或资源。
        client.close()


# 学习注释：按指定名字建事件集合（schema 与 ensure_event_collection 一致）。
def _create_collection(client: MilvusClient, name: str) -> None:
    """按指定名字建事件集合（schema 与 ensure_event_collection 一致）。"""
    # 学习注释：client.create_schema的执行结果；保存到schema。
    schema = client.create_schema(auto_id=False)
    # 学习注释：调用schema.add_field，处理'id'。
    schema.add_field("id", DataType.INT64, is_primary=True)
    # 学习注释：调用schema.add_field，处理'dense'。
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=DIM)
    # 学习注释：调用schema.add_field，处理'text'。
    schema.add_field("text", DataType.VARCHAR, max_length=4096,
                     enable_analyzer=True, analyzer_params={"type": "chinese"})
    # 学习注释：调用schema.add_field，处理'sparse'。
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    # 学习注释：调用schema.add_field，处理'event_id'。
    schema.add_field("event_id", DataType.VARCHAR, max_length=64)
    # 学习注释：调用schema.add_field，处理'outage_type'。
    schema.add_field("outage_type", DataType.VARCHAR, max_length=64)
    # 学习注释：调用schema.add_field，处理'outage_type_detail'。
    schema.add_field("outage_type_detail", DataType.VARCHAR, max_length=512)
    # 学习注释：调用schema.add_field，处理'type_conflict'。
    schema.add_field("type_conflict", DataType.BOOL)
    # 学习注释：调用schema.add_field，处理'voltage_level'。
    schema.add_field("voltage_level", DataType.VARCHAR, max_length=32)
    # 学习注释：调用schema.add_field，处理'supply_center'。
    schema.add_field("supply_center", DataType.VARCHAR, max_length=32)
    # 学习注释：调用schema.add_field，处理'supply_center_detail'。
    schema.add_field("supply_center_detail", DataType.VARCHAR, max_length=512)
    # 学习注释：调用schema.add_field，处理'center_conflict'。
    schema.add_field("center_conflict", DataType.BOOL)
    # 学习注释：调用schema.add_field，处理'line_names'。
    schema.add_field("line_names", DataType.VARCHAR, max_length=512)
    # 学习注释：调用schema.add_field，处理'line_count'。
    schema.add_field("line_count", DataType.INT64)
    # 学习注释：调用schema.add_field，处理'user_count'。
    schema.add_field("user_count", DataType.INT64)
    # 学习注释：调用schema.add_field，处理'start_time'。
    schema.add_field("start_time", DataType.VARCHAR, max_length=32)
    # 学习注释：调用schema.add_field，处理'end_time'。
    schema.add_field("end_time", DataType.VARCHAR, max_length=32)
    # 学习注释：调用schema.add_field，处理'household_hours'。
    schema.add_field("household_hours", DataType.FLOAT)
    # 学习注释：调用schema.add_field，处理'energy_shortage'。
    schema.add_field("energy_shortage", DataType.FLOAT)
    # 学习注释：调用schema.add_function，处理调用Function。
    schema.add_function(Function(
        name="text_bm25", input_field_names=["text"],
        output_field_names=["sparse"], function_type=FunctionType.BM25,
    ))
    # 学习注释：调用client.prepare_index_params；保存到index_params。
    index_params = client.prepare_index_params()
    # 学习注释：调用index_params.add_index。
    index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
    # 学习注释：调用index_params.add_index。
    index_params.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    # 学习注释：调用client.create_collection，处理name。
    client.create_collection(name, schema=schema, index_params=index_params)


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main():
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser(description="仅构建未激活的集合；完整发布请使用 publish.py")
    # 学习注释：调用ap.add_argument，处理'--corpus'。
    ap.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    # 学习注释：调用ap.add_argument，处理'--collection'。
    ap.add_argument("--collection", default=None)
    # 学习注释：调用ap.parse_args；保存到args。
    args = ap.parse_args()
    # 学习注释：vectorize的执行结果，await等待结果后再继续。
    await vectorize(args.corpus, args.collection)


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(main())
