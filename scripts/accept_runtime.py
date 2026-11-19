# 阅读导航：合成副本运行验收
# 本文件作用：验证复杂范围、恢复、会话并发等现有行为，运行入口限定合成演示副本。
# 建议读法：调用真实模型与Milvus；部分场景会卸载并恢复测试集合，请勿拿业务副本随意替换测试目录。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""现有查询/会话功能补验收。只接受完全虚构的数据副本。

真实模型、Embedding、Rerank和Milvus；仅卸载/恢复合成演示集合来测试故障恢复。
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从time引入perf_counter，用于耗时测量。
from time import perf_counter
# 学习注释：从unittest.mock引入patch，用于用确定性替身模拟外部服务和故障。
from unittest.mock import patch

# 学习注释：导入httpx：发送HTTP请求。
import httpx

# 学习注释：从app.core导入agent, similar, versioning，复用对应模块的实现。
from app.core import agent, similar, versioning
# 学习注释：从app.main导入app，复用对应模块的实现。
from app.main import app
# 学习注释：从scripts.query.vectorize_events导入_client，复用对应模块的实现。
from scripts.query.vectorize_events import _client


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main(dataset_dir: Path):
    # 学习注释：按目录层级拼接文件路径 Path('data/evaluations') / ('runtime_accept_' + versioning.new_version())；保存到root。
    root = Path("data/evaluations") / ("runtime_accept_" + versioning.new_version())
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    root.mkdir(parents=True)
    # 学习注释：包含mode、results的字典；保存到待保存的验收报告（report）。
    report = {"mode": "real_services_synthetic_data", "results": []}
    # 学习注释：异步函数check接收name、fn；把这一段可复用处理封装起来。
    async def check(name, fn):
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本阶段计时起点（t0）。
        t0 = perf_counter()
        # 学习注释：包含name的字典；保存到row。
        row = {"name": name}
        # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
        try:
            # 学习注释：调用fn，await等待结果后再继续；保存到row['details']。
            row["details"] = await fn()
            # 学习注释：'pass'；保存到row['status']。
            row["status"] = "pass"
        except Exception as exc:
            # 学习注释：将新字段合并到row，同名字段会覆盖旧值。
            row.update(status="fail", error_type=type(exc).__name__, error=str(exc))
        # 学习注释：调用round，处理计算 perf_counter() - t0；保存到row['seconds']。
        row["seconds"] = round(perf_counter()-t0, 2)
        # 学习注释：向report['results']末尾添加row。
        report["results"].append(row)
        # 学习注释：versioning.atomic_json的执行结果。
        versioning.atomic_json(root / "result.json", report)
        # 学习注释：在命令行输出本步骤结果或进度。
        print(name, row["status"], flush=True)

    # 学习注释：在这一块临时替换外部依赖，退出时恢复原函数，避免污染其他测试。
    with patch.object(versioning, "DATA_DIR", dataset_dir), patch.object(versioning, "VERSION_PATH", dataset_dir / "version.json"):
        # 学习注释：versioning.load_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
        snapshot = versioning.load_snapshot()
        # 学习注释：遍历snapshot.corpus_path.read_text(encoding='utf-8').splitlines()，将json.loads(line)收集为列表；保存到事件级语料（corpus）。
        corpus = [json.loads(line) for line in snapshot.corpus_path.read_text(encoding="utf-8").splitlines()]
        # 学习注释：测试要求：len(corpus)等于6，并且检查是否每一项条件都成立；不满足就失败。
        assert len(corpus) == 6 and all(str(e["事件序号"]).startswith("SYN") for e in corpus)
        # 学习注释：测试要求：'虚构'包含在snapshot的数据覆盖范围的声明依据属性（coverage_basis）；不满足就失败。
        assert "虚构" in snapshot.coverage_basis, "只允许操作合成演示集合"
        # 学习注释：snapshot的版本号属性（version）；保存到report['dataset_version']。
        report["dataset_version"] = snapshot.version

        # 学习注释：异步函数retrieval接收筛选条件（filters）、期望状态（expected）；把这一段可复用处理封装起来。
        async def retrieval(filters, expected):
            # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
            result = await similar.search_similar("停电事件", top_k=10, filters=filters, with_signal=True)
            # 学习注释：测试要求：set(result['trace']['returned_ids'])等于转为不重复元素集合：期望状态（expected）；不满足就失败。
            assert set(result["trace"]["returned_ids"]) == set(expected), result["trace"]["returned_ids"]
            # 学习注释：测试要求：result['signal'].signal等于期望状态（expected）为真/非空时取None（尚无值），否则取'empty'；不满足就失败。
            assert result["signal"].signal == (None if expected else "empty")
            # 学习注释：测试要求：result['trace']['dataset_version']等于snapshot的版本号属性（version）；不满足就失败。
            assert result["trace"]["dataset_version"] == snapshot.version
            # 学习注释：返回包含filters、ids的字典，结束本次函数调用。
            return {"filters": filters, "ids": result["trace"]["returned_ids"]}
        # 学习注释：调用check，处理'real_three_filters'，await等待结果后再继续。
        await check("real_three_filters", lambda: retrieval({"line": "线路1", "supply_center": "供服中心1",
            "time_range": {"start": "2025-06-01", "end": "2025-07-01"}}, ["SYN001", "SYN002"]))
        # 学习注释：调用check，处理'real_valid_entities_but_empty_intersection'，await等待结果后再继续。
        await check("real_valid_entities_but_empty_intersection", lambda: retrieval({"line": "线路1", "supply_center": "供服中心2"}, []))
        # 学习注释：调用check，处理'real_event_overlap'，await等待结果后再继续。
        await check("real_event_overlap", lambda: retrieval({"line": "线路1", "time_semantics": "event_overlap",
            "time_range": {"start": "2025-06-01", "end": "2025-06-02"}}, ["SYN004"]))
        # 学习注释：调用check，处理'real_record_start_differs_from_overlap'，await等待结果后再继续。
        await check("real_record_start_differs_from_overlap", lambda: retrieval({"line": "线路1",
            "time_range": {"start": "2025-06-01", "end": "2025-06-02"}}, []))

        # 学习注释：异步函数unavailable接收调用时的上下文；把这一段可复用处理封装起来。
        async def unavailable():
            # 学习注释：调用_client；保存到外部服务客户端（client）。
            client = _client()
            # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
            try:
                # 学习注释：调用client.release_collection，处理snapshot的Milvus集合名属性（collection）。
                client.release_collection(snapshot.collection)
                # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
                result = await similar.search_similar("电缆故障", with_signal=True)
                # 学习注释：测试要求：result['signal'].signal等于'processing_error'；不满足就失败。
                assert result["signal"].signal == "processing_error"
                # 学习注释：测试要求：result['trace']['error_stage']等于'recall'，并且不满足（result['events']为真/非空）；不满足就失败。
                assert result["trace"]["error_stage"] == "recall" and not result["events"]
            finally:
                # 学习注释：调用client.load_collection，处理snapshot的Milvus集合名属性（collection）。
                client.load_collection(snapshot.collection)
                # 学习注释：关闭client占用的连接或资源。
                client.close()
            # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到recovered。
            recovered = await similar.search_similar("电缆故障", with_signal=True)
            # 学习注释：测试要求：recovered['events']为真/非空，并且recovered['signal'].signal尚未设置（None）；不满足就失败。
            assert recovered["events"] and recovered["signal"].signal is None
            # 学习注释：返回包含injection、stage、recovered_without_app_restart的字典，结束本次函数调用。
            return {"injection": "release_collection", "stage": "recall", "recovered_without_app_restart": True}
        # 学习注释：调用check，处理'real_milvus_collection_unavailable_then_recovers'，await等待结果后再继续。
        await check("real_milvus_collection_unavailable_then_recovers", unavailable)

        # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120) as client:
            # 学习注释：异步函数request接收用户查询文本（query）、sid、name；把这一段可复用处理封装起来。
            async def request(query, sid=None, name="response"):
                # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
                response = await client.post("/api/chat" if sid else "/api/ask",
                    json={"query": query, **({"session_id": sid} if sid else {})})
                # 学习注释：测试要求：response.status_code等于200；不满足就失败。
                assert response.status_code == 200
                # 学习注释：把HTTP响应正文解析为JSON对象；保存到body。
                body = response.json()
                # 学习注释：versioning.atomic_json的执行结果。
                versioning.atomic_json(root / (name + ".json"), body)
                # 学习注释：测试要求：body['answer']为真/非空，并且不满足（body['degraded']为真/非空）；不满足就失败。
                assert body["answer"] and not body["degraded"]
                # 学习注释：测试要求：body['dataset_version']等于snapshot的版本号属性（version）；不满足就失败。
                assert body["dataset_version"] == snapshot.version
                # 学习注释：返回body，结束本次函数调用。
                return body

            # 学习注释：异步函数natural_scope接收调用时的上下文；把这一段可复用处理封装起来。
            async def natural_scope():
                # 学习注释：调用request，处理'找线路1、供服中心1在2025年6月的历史停电事件，最多10条，只检索案例。'，await等待结果后再继续；保存到body。
                body = await request("找线路1、供服中心1在2025年6月的历史停电事件，最多10条，只检索案例。", name="natural_scope")
                # 学习注释：遍历body['evidence']，将c收集为列表；保存到案例列表（cases）。
                cases = [c for b in body["evidence"] for c in b.get("cases", [])]
                # 学习注释：测试要求：{c['event_id'] for c in cases}等于2项集合：'SYN001'、'SYN002'；不满足就失败。
                assert {c["event_id"] for c in cases} == {"SYN001", "SYN002"}
                # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
                assert all(c["effective_filters"].get("line") == "线路1" and
                    c["effective_filters"].get("supply_center") == "供服中心1" and
                    c["effective_filters"].get("time_range") == {"start": "2025-06-01", "end": "2025-07-01"} for c in cases)
                # 学习注释：返回包含ids的字典，结束本次函数调用。
                return {"ids": [c["event_id"] for c in cases]}
            # 学习注释：调用check，处理'natural_language_three_filters'，await等待结果后再继续。
            await check("natural_language_three_filters", natural_scope)

            # 学习注释：异步函数natural_overlap接收调用时的上下文；把这一段可复用处理封装起来。
            async def natural_overlap():
                # 学习注释：调用request，处理'检索线路1在2025年6月1日当天仍有时间重叠的停电事件，包括之前开始的事件，按整个事件最早开始到最晚结束的…'，await等待结果后再继续；保存到body。
                body = await request("检索线路1在2025年6月1日当天仍有时间重叠的停电事件，包括之前开始的事件，按整个事件最早开始到最晚结束的时间范围判断，不能仅筛选当天开始的记录。", name="natural_overlap")
                # 学习注释：遍历body['evidence']，将c收集为列表；保存到案例列表（cases）。
                cases = [c for b in body["evidence"] for c in b.get("cases", [])]
                # 学习注释：测试要求：案例列表（cases）为真/非空，并且{c['event_id'] for c in cases}等于1项集合：'SYN004'；不满足就失败。
                assert cases and {c["event_id"] for c in cases} == {"SYN004"}
                # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
                assert all(c["time_semantics"] == "event_overlap" for c in cases)
                # 学习注释：返回包含ids、time_semantics的字典，结束本次函数调用。
                return {"ids": [c["event_id"] for c in cases], "time_semantics": "event_overlap"}
            # 学习注释：调用check，处理'natural_language_event_overlap'，await等待结果后再继续。
            await check("natural_language_event_overlap", natural_overlap)

            # 学习注释：函数count接收body、期望状态（expected）；把这一段可复用处理封装起来。
            def count(body, expected):
                # 学习注释：遍历body['evidence']，只保留满足条件的项，将b['stat']收集为列表；保存到stats。
                stats = [b["stat"] for b in body["evidence"] if b.get("stat")]
                # 学习注释：测试要求：stats为真/非空，并且stats[0]['rows']等于1项列表：包含停电次数的字典；不满足就失败。
                assert stats and stats[0]["rows"] == [{"停电次数": expected}], stats
            # 学习注释：异步函数parallel_sessions接收调用时的上下文；把这一段可复用处理封装起来。
            async def parallel_sessions():
                # 学习注释：异步函数query接收i；把这一段可复用处理封装起来。
                async def query(i):
                    # 学习注释：i % 2等于0时取2项元组：6、5，否则取2项元组：7、1；保存到month、期望状态（expected）。
                    month, expected = (6, 5) if i % 2 == 0 else (7, 1)
                    # 学习注释：调用request，处理把花括号内的变量填入f字符串，生成文字，await等待结果后再继续；保存到result。
                    result = await request(f"2025年{month}月一共发生了多少次停电？", f"runtime-{i}", f"parallel-{i}")
                    # 学习注释：调用count，处理result。
                    count(result, expected)
                    # 学习注释：返回result中键/位置为'request_id'的值，结束本次函数调用。
                    return result["request_id"]
                # 学习注释：并发运行这些异步任务并收集结果，await等待结果后再继续；保存到ids。
                ids = await asyncio.gather(*(query(i) for i in range(4)))
                # 学习注释：测试要求：len(set(ids))等于4；不满足就失败。
                assert len(set(ids)) == 4
                # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给i。
                for i in range(4):
                    # 学习注释：向指定地址发送HTTP DELETE请求，await等待结果后再继续；保存到response。
                    response = await client.delete(f"/api/chat/runtime-{i}")
                    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
                    assert response.status_code == 200 and not agent._SESSIONS.get(f"runtime-{i}")
                # 学习注释：返回包含concurrency、distinct_request_ids、sessions_cleared的字典，结束本次函数调用。
                return {"concurrency": 4, "distinct_request_ids": len(set(ids)), "sessions_cleared": True}
            # 学习注释：调用check，处理'real_four_concurrent_sessions'，await等待结果后再继续。
            await check("real_four_concurrent_sessions", parallel_sessions)

            # 学习注释：异步函数clear_restart接收调用时的上下文；把这一段可复用处理封装起来。
            async def clear_restart():
                # 学习注释：'runtime-clear'；保存到sid。
                sid = "runtime-clear"
                # 学习注释：调用request，处理'线路1在2025年6月停电几次？'，await等待结果后再继续；保存到first。
                first = await request("线路1在2025年6月停电几次？", sid, "clear-first")
                # 学习注释：调用count，处理first。
                count(first, 2)
                # 学习注释：向指定地址发送HTTP DELETE请求，await等待结果后再继续；保存到response。
                response = await client.delete("/api/chat/"+sid)
                # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
                assert response.status_code == 200 and not agent._SESSIONS.get(sid)
                # 学习注释：'2025年7月全台账停电几次？'；保存到用户查询文本（query）。
                query = "2025年7月全台账停电几次？"
                # 学习注释：调用request，处理用户查询文本（query），await等待结果后再继续；保存到fresh。
                fresh = await request(query, sid, "clear-new")
                # 学习注释：调用count，处理fresh。
                count(fresh, 1)
                # 学习注释：测试要求：fresh['resolved_query']等于用户查询文本（query）；不满足就失败。
                assert fresh["resolved_query"] == query
                # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
                assert len(agent._SESSIONS.get(sid)) == 2
                # 学习注释：向指定地址发送HTTP DELETE请求，await等待结果后再继续。
                await client.delete("/api/chat/"+sid)
                # 学习注释：返回包含count_before、count_after、old_context_removed的字典，结束本次函数调用。
                return {"count_before": 2, "count_after": 1, "old_context_removed": True}
            # 学习注释：调用check，处理'real_clear_then_restart'，await等待结果后再继续。
            await check("real_clear_then_restart", clear_restart)
    # 学习注释：检查是否每一项条件都成立时取'pass'，否则取'fail'；保存到report['status']。
    report["status"] = "pass" if all(r["status"] == "pass" for r in report["results"]) else "fail"
    # 学习注释：versioning.atomic_json的执行结果。
    versioning.atomic_json(root / "result.json", report)
    # 学习注释：在命令行输出本步骤结果或进度。
    print("report:", root / "result.json")
    # 学习注释：返回把report['status']不等于'pass'转换为整数，结束本次函数调用。
    return int(report["status"] != "pass")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--dataset-dir'。
    parser.add_argument("--dataset-dir", type=Path, required=True)
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：以异常终止这一条路径：启动事件循环，执行脚本的异步主函数；上层不能把它当作正常成功。
    raise SystemExit(asyncio.run(main(args.dataset_dir.resolve())))
