# 阅读导航：真实向量库冒烟
# 本文件作用：使用合成数据和模型替身，只验证真实Milvus的写入、范围过滤、读取与清理。
# 建议读法：Embedding/重排/依据核对被替换，不产生付费模型调用；不代表真实检索质量。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""合成数据验证真实本机 Milvus 的构建/读回/混合检索；不调用付费模型。

仅删除本次随机创建的 smoke 集合；不更新发布清单，不接触业务集合。
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
import sqlite3
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从unittest.mock引入patch，用于用确定性替身模拟外部服务和故障。
from unittest.mock import patch

# 学习注释：调用sys.path.insert，处理0。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings
# 学习注释：从app.core导入similar, versioning，复用对应模块的实现。
from app.core import similar, versioning
# 学习注释：从scripts.query导入vectorize_events并简称vector，复用对应模块的实现。
from scripts.query import vectorize_events as vector
# 学习注释：从scripts.query.build_db导入create_schema，复用对应模块的实现。
from scripts.query.build_db import create_schema
# 学习注释：从scripts.query.build_event_corpus导入write_corpus，复用对应模块的实现。
from scripts.query.build_event_corpus import write_corpus


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main():
    # 学习注释：计算 'smoke_' + versioning.new_version()；保存到run_id。
    run_id = "smoke_" + versioning.new_version()
    # 学习注释：计算 'outage_events_' + run_id；保存到Milvus集合名（collection）。
    collection = "outage_events_" + run_id
    # 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parents[2] / 'data/evaluations' / run_id；保存到本轮报告目录（folder）。
    folder = Path(__file__).resolve().parents[2] / "data/evaluations" / run_id
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    folder.mkdir(parents=True, exist_ok=False)
    # 学习注释：2项元组：按目录层级拼接文件路径 folder / 'gridmate.db'、按目录层级拼接文件路径 folder / 'event_corpus.jsonl'；保存到db、事件语料文件路径（corpus_path）。
    db, corpus_path = folder / "gridmate.db", folder / "event_corpus.jsonl"
    # 学习注释：打开SQLite连接；保存到数据库连接（conn）。
    conn = sqlite3.connect(db)
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：create_schema的执行结果。
        create_schema(conn)
        # 学习注释：对多行数据批量执行同一条带占位符的SQL。
        conn.executemany(
            "INSERT INTO users(事件序号,线路名称,供服中心,停电起始时间,停电终止时间,"
            "停电性质,备注说明,电压等级) VALUES(?,?,?,?,?,?,?,?)",
            [
                ("SYN_MAY", "测试线路1", "测试中心", "2025-05-31 23:00:00", "2025-06-01 01:00:00",
                 "故障", "合成电缆故障跨月记录", "10"),
                ("SYN_JUNE", "测试线路1", "测试中心", "2025-06-01 10:00:00", "2025-06-01 11:00:00",
                 "故障", "合成电缆故障同日记录", "10"),
                ("SYN_OTHER", "测试线路2", "测试中心", "2025-06-01 10:00:00", "2025-06-01 11:00:00",
                 "故障", "合成电缆故障其他线路", "10"),
            ])
        # 学习注释：提交数据库事务，使本次写入生效。
        conn.commit()
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()
    # 学习注释：write_corpus的执行结果。
    write_corpus(db, corpus_path)
    # 学习注释：计算 [1.0] + [0.0] * (vector.DIM - 1)；保存到计量单位（unit）。
    unit = [1.0] + [0.0] * (vector.DIM - 1)

    # 学习注释：测试替身：返回可预测的合成结果，替代真实模型接口，避免产生付费调用。
    async def embed_texts(texts):
        # 学习注释：返回遍历texts，将list(unit)收集为列表，结束本次函数调用。
        return [list(unit) for _ in texts]
    # 学习注释：测试替身：返回可预测的合成结果，替代真实模型接口，避免产生付费调用。
    async def embed_query(query):
        # 学习注释：返回转为可逐项访问的列表：计量单位（unit），结束本次函数调用。
        return list(unit)
    # 学习注释：测试替身：返回可预测的合成结果，替代真实模型接口，避免产生付费调用。
    async def rerank(query, docs, top_n=None):
        # 学习注释：返回遍历range(min(len(docs), top_n or len(docs)))，将(i, 0.5)收集为列表，结束本次函数调用。
        return [(i, 0.5) for i in range(min(len(docs), top_n or len(docs)))]
    # 学习注释：测试替身：返回可预测的合成结果，替代真实模型接口，避免产生付费调用。
    async def review(query, events):
        # 学习注释：返回遍历事件列表（events），将{'event_id': str(e['事件序号']), 'decision': 'supported', 'quotes': [e['事…收集为列表，结束本次函数调用。
        return [{"event_id": str(e["事件序号"]), "decision": "supported", "quotes": [e["事件文本"]],
                 "reason": "合成替身", "human_validated": False} for e in events]

    # 学习注释：包含test、collection、embedding、reranker、relevance、paid_model_calls的字典；保存到待保存的验收报告（report）。
    report = {"test": "real_milvus_synthetic_data", "collection": collection,
              "embedding": "synthetic unit vector", "reranker": "deterministic stub",
              "relevance": "deterministic stub",
              "paid_model_calls": 0, "status": "fail", "cleanup": False}
    # 学习注释：调用vector._client；保存到外部服务客户端（client）。
    client = vector._client()
    # 学习注释：只有调用client.has_collection，处理Milvus集合名（collection）才进入这一分支。
    if client.has_collection(collection):
        # 学习注释：关闭client占用的连接或资源。
        client.close()
        # 学习注释：以异常终止这一条路径：'随机测试集合已存在，拒绝使用'；上层不能把它当作正常成功。
        raise RuntimeError("随机测试集合已存在，拒绝使用")
    # 学习注释：执行可能失败的步骤；except处理异常，finally无论成功失败都负责清理。
    try:
        # 学习注释：在这一块临时替换外部依赖，退出时恢复原函数，避免污染其他测试。
        with patch.object(vector.embeddings, "embed_texts", embed_texts), \
             patch.object(similar.embeddings, "embed_query", embed_query), \
             patch.object(similar.rerank, "rerank", rerank), \
             patch.object(similar.relevance, "review_candidates", review):
            # 学习注释：vector.vectorize的执行结果，await等待结果后再继续；保存到built。
            built = await vector.vectorize(corpus_path, collection)
            # 学习注释：built中键/位置为'rows'的值；保存到report['published_rows']。
            report["published_rows"] = built["rows"]
            # 学习注释：调用versioning.DatasetSnapshot，处理run_id；保存到本次请求固定的数据版本（snapshot）。
            snapshot = versioning.DatasetSnapshot(
                run_id, db, corpus_path, collection, "2025-01-01", "2026-01-01",
                "合成数据测试范围", "verified_bundle", settings.embed_model)
            # 学习注释：包含line、time_range的字典；保存到筛选条件（filters）。
            filters = {"line": "测试线路1",
                       "time_range": {"start": "2025-06-01", "end": "2025-06-02"}}
            # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
            with versioning.dataset_scope(snapshot):
                # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到本轮计时起点（started）。
                started = await similar.search_similar("电缆故障", filters=filters, with_signal=True)
                # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到overlap。
                overlap = await similar.search_similar("电缆故障", filters={
                    **filters, "time_semantics": "event_overlap"}, with_signal=True)
            # 学习注释：只有started['signal'].signal为真/非空，或者overlap['signal'].signal为真/非空才进入这一分支。
            if started["signal"].signal or overlap["signal"].signal:
                # 学习注释：以异常终止这一条路径：计算 '真实检索未完成：' + str(started['trace'].get('error_stage') or overlap['trace'].get('error_stage'))；上层不能把它当作正常成功。
                raise RuntimeError("真实检索未完成：" + str(started["trace"].get("error_stage")
                                                         or overlap["trace"].get("error_stage")))
            # 学习注释：按指定键排序后得到新列表；保存到report['record_start_ids']。
            report["record_start_ids"] = sorted(e["事件序号"] for e in started["events"])
            # 学习注释：按指定键排序后得到新列表；保存到report['overlap_ids']。
            report["overlap_ids"] = sorted(e["事件序号"] for e in overlap["events"])
            # 学习注释：只有report['record_start_ids']不等于1项列表：'SYN_JUNE'才进入这一分支。
            if report["record_start_ids"] != ["SYN_JUNE"]:
                # 学习注释：以异常终止这一条路径：'开始时间口径过滤失败'；上层不能把它当作正常成功。
                raise RuntimeError("开始时间口径过滤失败")
            # 学习注释：只有report['overlap_ids']不等于2项列表：'SYN_JUNE'、'SYN_MAY'才进入这一分支。
            if report["overlap_ids"] != ["SYN_JUNE", "SYN_MAY"]:
                # 学习注释：以异常终止这一条路径：'重叠口径过滤失败'；上层不能把它当作正常成功。
                raise RuntimeError("重叠口径过滤失败")
            # 学习注释：'pass'；保存到report['status']。
            report["status"] = "pass"
    except Exception as exc:
        # 学习注释：type(exc)的__name__属性（__name__）；保存到report['error_type']。
        report["error_type"] = type(exc).__name__
        # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
        raise
    finally:
        # collection 仅由本函数的固定前缀与随机版本构造，并在创建前检查不存在。
        if client.has_collection(collection):
            # 学习注释：调用client.drop_collection，处理Milvus集合名（collection）。
            client.drop_collection(collection)
        # 学习注释：不满足（调用client.has_collection，处理Milvus集合名（collection））；保存到report['cleanup']。
        report["cleanup"] = not client.has_collection(collection)
        # 学习注释：关闭client占用的连接或资源。
        client.close()
        # 学习注释：只有similar._CLIENT已经有值（不是None）才进入这一分支。
        if similar._CLIENT is not None:
            # 学习注释：similar._acall的执行结果，await等待结果后再继续。
            await similar._acall(similar._CLIENT.close)
        # 学习注释：versioning.atomic_json的执行结果。
        versioning.atomic_json(folder / "result.json", report)
        # 学习注释：在命令行输出本步骤结果或进度。
        print(json.dumps(report, ensure_ascii=False))
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"报告：{folder / 'result.json'}")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(main())
