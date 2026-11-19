# 阅读导航：异常展示与短时负载
# 本文件作用：用确定性替身模拟回答/检查器故障，通过HTTP与浏览器验证降级和会话行为。
# 建议读法：这里的1200请求负载不调用真实模型，因此不能当作真实模型吞吐量。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""启动临时HTTP服务，注入模型/检查器故障，验收真实后端分支与浏览器展示。

不调用付费模型，不使用业务台账；输出明确标为故障注入。
"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入gc：供本文件调用的模块。
import gc
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入socket：网络连接；测试中用于拦截外部调用。
import socket
# 学习注释：导入threading：供本文件调用的模块。
import threading
# 学习注释：导入time：耗时测量。
import time
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从unittest.mock引入patch，用于用确定性替身模拟外部服务和故障。
from unittest.mock import patch

# 学习注释：导入httpx：发送HTTP请求。
import httpx
# 学习注释：导入uvicorn：供本文件调用的模块。
import uvicorn
# 学习注释：从langchain_core.messages引入AIMessage, ToolMessage，用于区分用户、助手和工具消息。
from langchain_core.messages import AIMessage, ToolMessage
# 学习注释：从playwright.sync_api引入sync_playwright, expect，用于控制浏览器并检查页面行为。
from playwright.sync_api import sync_playwright, expect

# 学习注释：从app.core导入agent, evidence, faithfulness, versioning，复用对应模块的实现。
from app.core import agent, evidence, faithfulness, versioning
# 学习注释：从app.core.memory导入SessionStore，复用对应模块的实现。
from app.core.memory import SessionStore
# 学习注释：从app.main导入app，复用对应模块的实现。
from app.main import app


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main(browsers=("msedge", "firefox"), skip_load=False):
    # 学习注释：按目录层级拼接文件路径 Path('data/evaluations') / ('degradation_browser_' + versioning.new_version())；保存到root。
    root = Path("data/evaluations") / ("degradation_browser_" + versioning.new_version())
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    root.mkdir(parents=True)
    # 本场景不查库；快照只用于标记来源，绝不冒充业务发布验证。
    snapshot = versioning.DatasetSnapshot("fault-injection", root/"unused.db", root/"unused.jsonl",
        "unused", "2025-01-01", "2026-01-01", "故障注入的合成证据", "legacy_unverified")
    # 学习注释：包含value、calls的字典；保存到mode。
    mode = {"value": "ok", "calls": 0}
    # 学习注释：定义FakeAgent对象，集中保存下方属性和方法。
    class FakeAgent:
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：用1更新mode['calls']；下方+=等写法把计算结果存回原变量。
            mode["calls"] += 1
            # 学习注释：只有mode['value']等于'model_error'才进入这一分支。
            if mode["value"] == "model_error":
                # 学习注释：以异常终止这一条路径：'private-upstream-diagnostic'；上层不能把它当作正常成功。
                raise RuntimeError("private-upstream-diagnostic")
            # 学习注释：调用evidence.ToolEvidence；保存到一份完整工具证据（bundle）。
            bundle = evidence.ToolEvidence(**versioning.evidence_context(), message="合成事实：已返回工具数据。")
            # 学习注释：evidence.transport的执行结果；保存到content、artifact。
            content, artifact = evidence.transport(bundle)
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [ToolMessage(content=content, artifact=artifact,
                name="query_outage_stat", tool_call_id="injected"), AIMessage(content="合成模型回答")]}
    # 学习注释：定义Judge对象，集中保存下方属性和方法。
    class Judge:
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：只有mode['value']等于'checker_error'才进入这一分支。
            if mode["value"] == "checker_error":
                # 学习注释：以异常终止这一条路径：'injected checker timeout'；上层不能把它当作正常成功。
                raise httpx.ReadTimeout("injected checker timeout")
            # 学习注释：返回调用faithfulness._FaithCheck，结束本次函数调用。
            return faithfulness._FaithCheck(faithful=mode["value"] != "unfaithful", reason="合成判断")
    # 学习注释：异步函数resolve接收用户查询文本（query）、用于补全追问的历史文本（history）；把这一段可复用处理封装起来。
    async def resolve(query, history):
        # 学习注释：返回用户查询文本（query），结束本次函数调用。
        return query
    # 学习注释：调用socket.socket；保存到sock。
    sock = socket.socket()
    # 学习注释：调用sock.bind，处理2项元组：'127.0.0.1'、0。
    sock.bind(("127.0.0.1", 0))
    # 学习注释：调用sock.listen，处理128。
    sock.listen(128)
    # 学习注释：sock.getsockname()中键/位置为1的值；保存到port。
    port = sock.getsockname()[1]
    # 学习注释：调用uvicorn.Server，处理调用uvicorn.Config，处理app；保存到server。
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    # 学习注释：空列表；保存到结果列表（results）。
    results = []
    # 学习注释：在这一块临时替换外部依赖，退出时恢复原函数，避免污染其他测试。
    with patch.object(versioning, "load_snapshot", lambda: snapshot), \
         patch.object(agent, "_get_agent", lambda: FakeAgent()), \
         patch.object(faithfulness, "_chain", lambda: Judge()), \
         patch.object(agent.coref, "resolve_outage", resolve), \
         patch.object(agent, "_SESSIONS", SessionStore()):
        # 学习注释：调用threading.Thread；保存到thread。
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        # 学习注释：调用thread.start。
        thread.start()
        # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
        try:
            # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给_。
            for _ in range(100):
                # 学习注释：只有server.started为真/非空才进入这一分支。
                if server.started:
                    # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                    break
                # 学习注释：调用time.sleep，处理0.05。
                time.sleep(.05)
            # 学习注释：测试要求：server.started为真/非空；不满足就失败。
            assert server.started
            # 学习注释：调用sync_playwright；with结束时调用退出逻辑，关闭资源或恢复上下文。
            with sync_playwright() as p:
                # 学习注释：遍历browsers，每轮把当前项交给browser_name。
                for browser_name in browsers:
                    # 学习注释：browser_name等于'msedge'时取调用p.chromium.launch，否则取调用p.firefox.launch；保存到browser。
                    browser = (p.chromium.launch(channel="msedge", headless=True) if browser_name == "msedge"
                               else p.firefox.launch(headless=True))
                    # 学习注释：调用browser.new_page；保存到page。
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    # 学习注释：调用page.goto，处理把花括号内的变量填入f字符串，生成文字。
                    page.goto(f"http://127.0.0.1:{port}")
                    # 学习注释：遍历4项元组：3项元组：'unfaithful'、'unfaithful'、3、3项元组：'checker_error'、'unverified'、1、3项元组：'model_error'、None（尚无值）、1…，每轮把当前项交给failure、expected_status、expected_calls。
                    for failure, expected_status, expected_calls in (("unfaithful", "unfaithful", 3),
                            ("checker_error", "unverified", 1), ("model_error", None, 1), ("ok", "faithful", 1)):
                        # 学习注释：将新字段合并到mode，同名字段会覆盖旧值。
                        mode.update(value=failure, calls=0)
                        # 学习注释：调用page.locator('#input').fill，处理计算 '故障注入：' + failure。
                        page.locator("#input").fill("故障注入："+failure)
                        # 学习注释：调用page.expect_response，处理lambda r: r.url.endswith('/api/chat')；with结束时调用退出逻辑，关闭资源或恢复上下文。
                        with page.expect_response(lambda r: r.url.endswith("/api/chat")) as future:
                            # 学习注释：调用page.locator('#send').click。
                            page.locator("#send").click()
                        # 学习注释：future的value属性（value）；保存到response。
                        response = future.value
                        # 学习注释：把HTTP响应正文解析为JSON对象；保存到body。
                        body = response.json()
                        # 学习注释：调用expect(page.locator('#send')).to_be_enabled。
                        expect(page.locator("#send")).to_be_enabled()
                        # 学习注释：page.locator('.assistant')的last属性（last）；保存到row。
                        row = page.locator(".assistant").last
                        # 学习注释：response.request.post_data_json中键/位置为'session_id'的值；保存到sid。
                        sid = response.request.post_data_json["session_id"]
                        # 学习注释：测试要求：mode['calls']等于expected_calls；不满足就失败。
                        assert mode["calls"] == expected_calls
                        # 学习注释：只有failure等于'model_error'才进入这一分支。
                        if failure == "model_error":
                            # 学习注释：测试要求：response.status等于500；不满足就失败。
                            assert response.status == 500
                            # 学习注释：调用expect(row.locator('.bubble')).to_contain_text，处理'执行失败'。
                            expect(row.locator(".bubble")).to_contain_text("执行失败")
                            # 学习注释：测试要求：'private-upstream'不包含在调用row.inner_text；不满足就失败。
                            assert "private-upstream" not in row.inner_text()
                        else:
                            # 学习注释：测试要求：response.status等于200，并且body['verification_status']等于expected_status；不满足就失败。
                            assert response.status == 200 and body["verification_status"] == expected_status
                            # 学习注释：调用expect(row.locator('.bubble')).to_have_text，处理body中键/位置为'answer'的值。
                            expect(row.locator(".bubble")).to_have_text(body["answer"])
                            # 学习注释：只有failure不等于'ok'才进入这一分支。
                            if failure != "ok":
                                # 学习注释：测试要求：body['degraded']为真/非空，并且'合成模型回答'不包含在body中键/位置为'answer'的值；不满足就失败。
                                assert body["degraded"] and "合成模型回答" not in body["answer"]
                                # 学习注释：调用expect(row.locator('.faith.bad')).to_contain_text，处理failure等于'checker_error'时取'检查器异常'，否则取'多次核对不通过'。
                                expect(row.locator(".faith.bad")).to_contain_text(
                                    "检查器异常" if failure == "checker_error" else "多次核对不通过")
                            else:
                                # 学习注释：调用expect(row.locator('.faith.ok')).to_contain_text，处理'忠实度通过'。
                                expect(row.locator(".faith.ok")).to_contain_text("忠实度通过")
                        # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
                        assert len(agent._SESSIONS.get(sid)) == (2 if failure == "ok" else 0)
                        # 学习注释：向结果列表（results）末尾添加包含browser、case、status、model_calls的字典。
                        results.append({"browser": browser_name, "case": failure, "status": "pass", "model_calls": expected_calls})
                        # 学习注释：在命令行输出本步骤结果或进度。
                        print(browser_name, failure, "pass", flush=True)
                    # 学习注释：调用page.screenshot。
                    page.screenshot(path=str(root/(browser_name+"-faults.png")))
                    # 学习注释：关闭browser占用的连接或资源。
                    browser.close()

            # 学习注释：异步函数short_load接收调用时的上下文；把这一段可复用处理封装起来。
            async def short_load():
                # 学习注释：创建并发名额限制器，防止同时启动过多调用；保存到sem。
                sem = asyncio.Semaphore(20)
                # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                    # 学习注释：异步函数one接收i；把这一段可复用处理封装起来。
                    async def one(i):
                        # 学习注释：sem；with结束时调用退出逻辑，关闭资源或恢复上下文。
                        async with sem:
                            # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
                            response = await client.post("/api/chat", json={"session_id": f"load-{i}", "query": "合成负载"})
                            # 学习注释：测试要求：response.status_code等于200，并且不满足（response.json()['degraded']为真/非空）；不满足就失败。
                            assert response.status_code == 200 and not response.json()["degraded"]
                    # 学习注释：并发运行这些异步任务并收集结果，await等待结果后再继续。
                    await asyncio.gather(*(one(i) for i in range(1200)))
            # 学习注释：只有skip_load为空或为False才进入这一分支。
            if not skip_load:
                # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到start。
                start = time.perf_counter()
                # 学习注释：启动事件循环，执行脚本的异步主函数。
                asyncio.run(short_load())
                # 学习注释：调用gc.collect。
                gc.collect()
                # 学习注释：测试要求：len(agent._SESSIONS._sessions)等于1000，并且不满足（agent._session_locks为真/非空）；不满足就失败。
                assert len(agent._SESSIONS._sessions) == 1000 and not agent._session_locks
                # 学习注释：向结果列表（results）末尾添加包含case、status、requests、concurrency、seconds、remaining_sessions的字典。
                results.append({"case": "bounded_http_load", "status": "pass", "requests": 1200,
                                "concurrency": 20, "seconds": round(time.perf_counter()-start, 2),
                                "remaining_sessions": 1000, "remaining_locks": 0, "model": "deterministic_stub"})
                # 学习注释：在命令行输出本步骤结果或进度。
                print("bounded_http_load pass", flush=True)
        finally:
            # 学习注释：True（成立）；保存到server.should_exit。
            server.should_exit = True
            # 学习注释：用指定分隔符连接各段文本。
            thread.join(timeout=10)
            # 学习注释：关闭sock占用的连接或资源。
            sock.close()
            # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
            (root/"result.json").write_text(json.dumps({"mode": "fault_injection", "results": results,
                "server_stopped": not thread.is_alive()}, ensure_ascii=False, indent=2), encoding="utf-8")
            # 学习注释：在命令行输出本步骤结果或进度。
            print("report:", root/"result.json", flush=True)


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--browsers'。
    parser.add_argument("--browsers", nargs="+", choices=["msedge", "firefox"], default=["msedge", "firefox"])
    # 学习注释：调用parser.add_argument，处理'--skip-load'。
    parser.add_argument("--skip-load", action="store_true")
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：调用main，处理args的browsers属性（browsers）。
    main(args.browsers, args.skip_load)
