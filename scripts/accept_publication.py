# 阅读导航：发布可靠性演练
# 本文件作用：用独立合成版本演练发布、切换、回滚和中断，检查旧版本仍可读。
# 建议读法：会创建集合、启动和终止本脚本子进程；不是普通单元测试，学习时先读verify与报告记录逻辑。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""现有发布/回滚功能的真实验收：合成数据、真实Embedding/Milvus/Rerank。

每次使用独立验证目录与随机集合，finally仅清理本次集合，不切换业务清单。
"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入csv：读写CSV表格。
import csv
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入os：环境变量与系统文件操作。
import os
# 学习注释：导入signal：供本文件调用的模块。
import signal
# 学习注释：导入subprocess：启动独立进程或调用Git等命令。
import subprocess
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从time引入perf_counter，用于耗时测量。
from time import perf_counter
# 学习注释：从unittest.mock引入patch，用于用确定性替身模拟外部服务和故障。
from unittest.mock import patch

# 学习注释：从app.core导入similar, versioning，复用对应模块的实现。
from app.core import similar, versioning
# 学习注释：从app.core.query_templates导入execute_query，复用对应模块的实现。
from app.core.query_templates import execute_query
# 学习注释：从scripts.make_demo_data导入generate，复用对应模块的实现。
from scripts.make_demo_data import generate
# 学习注释：从scripts.query导入publish, vectorize_events并简称vector，复用对应模块的实现。
from scripts.query import publish, vectorize_events as vector


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main():
    # 学习注释：按目录层级拼接文件路径 Path('data/evaluations') / ('publication_accept_' + versioning.new_version())；保存到root。
    root = Path("data/evaluations") / ("publication_accept_" + versioning.new_version())
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    root.mkdir(parents=True)
    # 学习注释：versioning的VERSION_PATH属性（VERSION_PATH）；保存到business。
    business = versioning.VERSION_PATH
    # 学习注释：versioning.file_sha256的执行结果；保存到business_hash。
    business_hash = versioning.file_sha256(business)
    # 学习注释：包含mode、cases、collections_cleaned的字典；保存到待保存的验收报告（report）。
    report = {"mode": "real_services_synthetic_data", "cases": [], "collections_cleaned": []}
    # 学习注释：空列表；保存到collections。
    collections = []
    # 学习注释：按目录层级拼接文件路径 root / 'published'；保存到active。
    active = root / "published"
    # 学习注释：函数record接收name；把这一段可复用处理封装起来。
    def record(name, **details):
        # 学习注释：向report['cases']末尾添加包含name、status的字典。
        report["cases"].append({"name": name, "status": "pass", **details})
        # 学习注释：在命令行输出本步骤结果或进度。
        print(name, "pass", flush=True)
        # 学习注释：versioning.atomic_json的执行结果。
        versioning.atomic_json(root / "result.json", report)

    # 学习注释：函数mutate_b接收本轮报告目录（folder）；把这一段可复用处理封装起来。
    def mutate_b(folder):
        # 学习注释：遍历3项元组：'users_masked.csv'、'segments_masked.csv'、'events_sample.csv'，每轮把当前项交给name。
        for name in ("users_masked.csv", "segments_masked.csv", "events_sample.csv"):
            # 学习注释：计算 folder / name；保存到path。
            path = folder / name
            # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
            with path.open(encoding="utf-8-sig", newline="") as stream:
                # 学习注释：转为可逐项访问的列表：调用csv.DictReader，处理stream；保存到查询或表格行（rows）。
                rows = list(csv.DictReader(stream))
            # 学习注释：只有name不等于'events_sample.csv'才进入这一分支。
            if name != "events_sample.csv":
                # 学习注释：用'版本B更正说明。'更新rows[0]['备注说明']；下方+=等写法把计算结果存回原变量。
                rows[0]["备注说明"] += " 版本B更正说明。"
            # 学习注释：创建或浅复制字典：rows中键/位置为0的值；保存到extra。
            extra = dict(rows[0])
            # 学习注释：'SYN007'；保存到extra['事件序号']。
            extra["事件序号"] = "SYN007"
            # 学习注释：向查询或表格行（rows）末尾添加extra。
            rows.append(extra)
            # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                # 学习注释：调用csv.DictWriter，处理stream；保存到writer。
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                # 学习注释：调用writer.writeheader。
                writer.writeheader()
                # 学习注释：调用writer.writerows，处理查询或表格行（rows）。
                writer.writerows(rows)

    # 学习注释：异步函数verify接收expected_version、expected_count；把这一段可复用处理封装起来。
    async def verify(expected_version, expected_count):
        # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
        with versioning.dataset_scope():
            # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
            snapshot = versioning.current_snapshot()
            # 学习注释：execute_query的执行结果；保存到result。
            result = execute_query("line_outage_count", {"line_name": "线路1"},
                {"time_range": {"start_time": "2025-06-01", "end_time": "2025-07-01"}})
            # 学习注释：测试要求：result['rows']等于1项列表：包含停电次数的字典；不满足就失败。
            assert result["rows"] == [{"停电次数": expected_count}]
            # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到hit。
            hit = await similar.search_similar("停电事件", top_k=10, filters={"line": "线路1",
                "time_range": {"start": "2025-06-01", "end": "2025-07-01"}}, with_signal=True)
            # 学习注释：测试要求：snapshot.version等于expected_version等于hit['trace']中键/位置为'dataset_version'的值；不满足就失败。
            assert snapshot.version == expected_version == hit["trace"]["dataset_version"]
            # 学习注释：测试要求：hit['signal'].signal尚未设置（None），并且len(hit['events'])等于expected_count；不满足就失败。
            assert hit["signal"].signal is None and len(hit["events"]) == expected_count
            # 学习注释：遍历map(json.loads, snapshot.corpus_path.read_text(encoding='utf-8').splitlines())，将e['事件序号']→e收集为字典；保存到事件级语料（corpus）。
            corpus = {e["事件序号"]: e for e in map(json.loads, snapshot.corpus_path.read_text(encoding="utf-8").splitlines())}
            # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
            assert all(e["事件文本"] == corpus[e["事件序号"]]["事件文本"] for e in hit["events"])
            # 学习注释：测试要求：hit['trace']['collection']等于snapshot的Milvus集合名属性（collection）；不满足就失败。
            assert hit["trace"]["collection"] == snapshot.collection
            # 学习注释：返回包含version、stat_count、returned_ids、source_match的字典，结束本次函数调用。
            return {"version": expected_version, "stat_count": expected_count,
                    "returned_ids": hit["trace"]["returned_ids"], "source_match": True}

    # 学习注释：调用generate，处理按目录层级拼接文件路径 root / 'a'。
    generate(root / "a")
    # 学习注释：调用generate，处理按目录层级拼接文件路径 root / 'b'。
    generate(root / "b")
    # 学习注释：调用mutate_b，处理按目录层级拼接文件路径 root / 'b'。
    mutate_b(root / "b")
    # 学习注释：包含start、end、basis、completeness的字典；保存到coverage。
    coverage = {"start": "2025-01-01", "end": "2026-01-01", "basis": "完全虚构的回滚验收数据", "completeness": "sample"}
    # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本轮计时起点（started）。
    started = perf_counter()
    # 学习注释：执行可能失败的步骤；except处理异常，finally无论成功失败都负责清理。
    try:
        # 学习注释：在这一块临时替换外部依赖，退出时恢复原函数，避免污染其他测试。
        with patch.object(versioning, "DATA_DIR", active), patch.object(versioning, "VERSION_PATH", active / "version.json"):
            # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到a。
            a = await publish.publish(root / "a", coverage)
            # 学习注释：向collections末尾添加a中键/位置为'collection'的值。
            collections.append(a["collection"])
            # 学习注释：调用record，处理'publish_a'。
            record("publish_a", **await verify(a["version"], 2))
            # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
            with versioning.dataset_scope():
                # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到b。
                b = await publish.publish(root / "b", coverage)
                # 学习注释：向collections末尾添加b中键/位置为'collection'的值。
                collections.append(b["collection"])
                # 学习注释：调用record，处理'inflight_request_keeps_a_after_b_publish'。
                record("inflight_request_keeps_a_after_b_publish", **await verify(a["version"], 2))
            # 学习注释：调用record，处理'new_request_reads_b'。
            record("new_request_reads_b", **await verify(b["version"], 3))
            # 学习注释：publish.rollback的执行结果。
            publish.rollback(a["version"])
            # 学习注释：调用record，处理'real_rollback_b_to_a'。
            record("real_rollback_b_to_a", **await verify(a["version"], 2))
            # 学习注释：publish.rollback的执行结果。
            publish.rollback(b["version"])
            # 学习注释：调用record，处理'real_rollback_a_to_b'。
            record("real_rollback_a_to_b", **await verify(b["version"], 3))
            # 学习注释：publish.rollback的执行结果。
            publish.rollback(a["version"])
            # 学习注释：按目录层级拼接文件路径 active / b['corpus_path']；保存到artifact。
            artifact = active / b["corpus_path"]
            # 学习注释：以原始字节读取文件，便于哈希或精确比对；保存到raw。
            raw = artifact.read_bytes()
            # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
            try:
                # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
                artifact.write_text("corrupted verification artifact", encoding="utf-8")
                # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
                try:
                    # 学习注释：publish.rollback的执行结果。
                    publish.rollback(b["version"])
                except ValueError:
                    # 学习注释：测试要求：versioning.current_dataset_version()等于a中键/位置为'version'的值；不满足就失败。
                    assert versioning.current_dataset_version() == a["version"]
                    # 学习注释：调用record，处理'corrupt_rollback_rejected_current_kept'。
                    record("corrupt_rollback_rejected_current_kept")
                else:
                    # 学习注释：以异常终止这一条路径：'corrupted artifact activated'；上层不能把它当作正常成功。
                    raise AssertionError("corrupted artifact activated")
            finally:
                # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
                artifact.write_bytes(raw)

            # 真正启动发布子进程：建库后、生成语料前暂停，再强制终止。
            marker = root / "child_ready"
            # 学习注释：'import asyncio,sys,time,os from pathlib import Path fr…'；保存到child_code。
            child_code = '''import asyncio,sys,time,os
from pathlib import Path
from app.core import versioning
from scripts.query import publish
versioning.DATA_DIR=Path(sys.argv[1]); versioning.VERSION_PATH=versioning.DATA_DIR/'version.json'
original=publish._run_corpus
def pause(*args):
    Path(sys.argv[3]).write_text(str(os.getpid()))
    time.sleep(60)
    return original(*args)
publish._run_corpus=pause
asyncio.run(publish.publish(Path(sys.argv[2])))
'''
            # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
            with (root / "child.log").open("w", encoding="utf-8") as log:
                # 学习注释：调用subprocess.Popen，处理6项列表：sys的executable属性（executable）、'-c'、child_code、把把路径解析成绝对路径转换为字符串、把…；保存到child。
                child = subprocess.Popen([sys.executable, "-c", child_code, str(active.resolve()),
                    str((root / "b").resolve()), str(marker.resolve())], stdout=log, stderr=log)
                # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
                try:
                    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给_。
                    for _ in range(200):
                        # 学习注释：只有检查路径是否存在才进入这一分支。
                        if marker.exists():
                            # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                            break
                        # 学习注释：测试要求：child.poll()尚未设置（None）；不满足就失败。
                        assert child.poll() is None, "child exited before injection point"
                        # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
                        await asyncio.sleep(.1)
                    # 学习注释：测试要求：检查路径是否存在；不满足就失败。
                    assert marker.exists(), "child startup deadline exceeded"
                    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
                    try:
                        # 学习注释：versioning.publication_lock的执行结果；with结束时调用退出逻辑，关闭资源或恢复上下文。
                        with versioning.publication_lock():
                            # 学习注释：以异常终止这一条路径：'concurrent publication lock not enforced'；上层不能把它当作正常成功。
                            raise AssertionError("concurrent publication lock not enforced")
                    except RuntimeError:
                        # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
                        pass
                    # Windows venv启动器PID与真正解释器PID可能不同；终止实际持锁者。
                    worker_pid = int(marker.read_text())
                    # 学习注释：按目录层级拼接文件路径 active / '.publish.lock'；保存到本会话的互斥锁（lock）。
                    lock = active / ".publish.lock"
                    # 学习注释：测试要求：json.loads(lock.read_text())['pid']等于worker_pid；不满足就失败。
                    assert json.loads(lock.read_text())["pid"] == worker_pid
                    # 学习注释：调用os.kill，处理worker_pid。
                    os.kill(worker_pid, signal.SIGTERM)
                    # 学习注释：调用child.wait。
                    child.wait(timeout=10)
                    # 学习注释：测试要求：检查路径是否存在，并且json.loads(lock.read_text())['pid']等于worker_pid；不满足就失败。
                    assert lock.exists() and json.loads(lock.read_text())["pid"] == worker_pid
                    # 学习注释：测试要求：versioning.current_dataset_version()等于a中键/位置为'version'的值；不满足就失败。
                    assert versioning.current_dataset_version() == a["version"]
                    # 学习注释：调用record，处理'killed_publisher_keeps_a_and_leaves_detectable_lock'。
                    record("killed_publisher_keeps_a_and_leaves_detectable_lock", child_exit=child.returncode)
                    # 演练清理：已确认这个子进程死亡，且锁属于它，才删除本次锁。
                    lock.unlink()
                finally:
                    # 学习注释：只有child.poll()尚未设置（None）才进入这一分支。
                    if child.poll() is None:
                        # 学习注释：只有检查路径是否存在才进入这一分支。
                        if marker.exists():
                            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
                            try:
                                # 学习注释：调用os.kill，处理把按指定编码读取文件内容为字符串转换为整数。
                                os.kill(int(marker.read_text()), signal.SIGTERM)
                            except ProcessLookupError:
                                # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
                                pass
                        # 学习注释：调用child.kill。
                        child.kill()
                        # 学习注释：调用child.wait。
                        child.wait(timeout=10)
            # 学习注释：调用record，处理'old_data_readable_after_interruption'。
            record("old_data_readable_after_interruption", **await verify(a["version"], 2))
            # 学习注释：publish.rollback的执行结果。
            publish.rollback(b["version"])
            # 学习注释：调用record，处理'rollback_works_after_stale_lock_cleanup'。
            record("rollback_works_after_stale_lock_cleanup", **await verify(b["version"], 3))
            # 学习注释：publish.rollback的执行结果。
            publish.rollback(a["version"])
            # 学习注释：调用vector._client；保存到外部服务客户端（client）。
            client = vector._client()
            # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
            try:
                # 学习注释：调用client.drop_collection，处理b中键/位置为'collection'的值。
                client.drop_collection(b["collection"])
            finally:
                # 学习注释：关闭client占用的连接或资源。
                client.close()
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 学习注释：publish.rollback的执行结果。
                publish.rollback(b["version"])
            except Exception:
                # 学习注释：测试要求：versioning.current_dataset_version()等于a中键/位置为'version'的值；不满足就失败。
                assert versioning.current_dataset_version() == a["version"]
                # 学习注释：调用record，处理'missing_real_collection_rejects_rollback'。
                record("missing_real_collection_rejects_rollback")
            else:
                # 学习注释：以异常终止这一条路径：'missing collection activated'；上层不能把它当作正常成功。
                raise AssertionError("missing collection activated")
            # 学习注释：调用record，处理'a_readable_after_rejected_rollback'。
            record("a_readable_after_rejected_rollback", **await verify(a["version"], 2))
        # 学习注释：'pass'；保存到report['status']。
        report["status"] = "pass"
    except Exception as exc:
        # 学习注释：将新字段合并到待保存的验收报告（report），同名字段会覆盖旧值。
        report.update(status="fail", error_type=type(exc).__name__, error=str(exc))
        # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
        raise
    finally:
        # 学习注释：调用vector._client；保存到外部服务客户端（client）。
        client = vector._client()
        # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
        try:
            # 仅清理本次独立目录下的集合，包含未完成发布可能留下的集合。
            for folder in (active / "versions").glob("*"):
                # 学习注释：计算 'outage_events_' + folder.name；保存到name。
                name = "outage_events_" + folder.name
                # 学习注释：只有调用client.has_collection，处理name才进入这一分支。
                if client.has_collection(name):
                    # 学习注释：调用client.drop_collection，处理name。
                    client.drop_collection(name)
                # 学习注释：向report['collections_cleaned']末尾添加name。
                report["collections_cleaned"].append(name)
        finally:
            # 学习注释：关闭client占用的连接或资源。
            client.close()
        # 学习注释：versioning.file_sha256(business)等于business_hash；保存到report['business_manifest_unchanged']。
        report["business_manifest_unchanged"] = versioning.file_sha256(business) == business_hash
        # 学习注释：调用round，处理计算 perf_counter() - started；保存到report['seconds']。
        report["seconds"] = round(perf_counter()-started, 2)
        # 学习注释：versioning.atomic_json的执行结果。
        versioning.atomic_json(root / "result.json", report)
        # 学习注释：在命令行输出本步骤结果或进度。
        print("report:", root / "result.json", flush=True)
    # 学习注释：测试要求：report['business_manifest_unchanged']为真/非空；不满足就失败。
    assert report["business_manifest_unchanged"]


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(main())
