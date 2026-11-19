# 阅读导航：浏览器交互验收
# 本文件作用：用Playwright控制浏览器；默认注入响应测交互，开启live后再测真实模型链路。
# 建议读法：注入响应只验证页面行为；真实业务问答截图与响应不要混入公开演示材料。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""Playwright 浏览器验收；默认注入响应，--live 额外调用真实模型。

启动服务后运行。真实回答、截图和报告只写入本地忽略目录。
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入hashlib：内容哈希，用于检查文件是否变化。
import hashlib
# 学习注释：从datetime引入datetime，用于日期时间解析与计算。
from datetime import datetime
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从time引入perf_counter，用于耗时测量。
from time import perf_counter

# 学习注释：从playwright.sync_api引入sync_playwright, expect，用于控制浏览器并检查页面行为。
from playwright.sync_api import sync_playwright, expect


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main():
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--url'。
    parser.add_argument("--url", default="http://127.0.0.1:8118")
    # 学习注释：调用parser.add_argument，处理'--output'。
    parser.add_argument("--output", type=Path, default=Path("data/evaluations/browser"))
    # 学习注释：调用parser.add_argument，处理'--live'。
    parser.add_argument("--live", action="store_true")
    # 学习注释：调用parser.add_argument，处理'--channel'。
    parser.add_argument("--channel", default="msedge")
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    args.output.mkdir(parents=True, exist_ok=True)
    # 学习注释：3项元组：空列表、空列表、空列表；保存到结果列表（results）、responses、errors。
    results, responses, errors = [], [], []

    # 学习注释：函数check接收name、fn、mode；把这一段可复用处理封装起来。
    def check(name, fn, mode="injected"):
        # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本轮计时起点（started）。
        started = perf_counter()
        # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
        try:
            # 学习注释：调用fn。
            fn()
            # 学习注释：向结果列表（results）末尾添加包含name、mode、status的字典。
            results.append({"name": name, "mode": mode, "status": "pass"})
        except Exception as exc:
            # 学习注释：向结果列表（results）末尾添加包含name、mode、status、error的字典。
            results.append({"name": name, "mode": mode, "status": "fail", "error": str(exc)})
        # 学习注释：调用round，处理计算 perf_counter() - started；保存到results[-1]['seconds']。
        results[-1]["seconds"] = round(perf_counter() - started, 2)
        # 学习注释：在命令行输出本步骤结果或进度。
        print(name, results[-1]["status"], flush=True)

    # 学习注释：调用sync_playwright；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with sync_playwright() as p:
        # 学习注释：调用p.chromium.launch；保存到browser。
        browser = p.chromium.launch(channel=args.channel, headless=True)
        # 学习注释：调用browser.new_page；保存到page。
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # 学习注释：调用page.set_default_timeout，处理15000。
        page.set_default_timeout(15000)
        # 学习注释：调用page.on，处理'pageerror'。
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        # 学习注释：调用page.goto，处理args的url属性（url）。
        page.goto(args.url)
        # 学习注释：空列表；保存到requests。
        requests = []
        # 学习注释：空列表；保存到pending。
        pending = []
        # 学习注释：包含kind的字典；保存到behavior。
        behavior = {"kind": "ok"}

        # 学习注释：函数intercept接收route；把这一段可复用处理封装起来。
        def intercept(route):
            # 学习注释：route.request的post_data_json属性（post_data_json）；保存到body。
            body = route.request.post_data_json
            # 学习注释：向requests末尾添加body。
            requests.append(body)
            # 学习注释：behavior中键/位置为'kind'的值；保存到kind。
            kind = behavior["kind"]
            # 学习注释：只有kind等于'hold'才进入这一分支。
            if kind == "hold":
                # 学习注释：向pending末尾添加route。
                pending.append(route)
            # 学习注释：只有kind等于'http'才进入这一分支。
            elif kind == "http":
                # 学习注释：调用route.fulfill。
                route.fulfill(status=503, json={"detail": "合成服务故障"})
            # 学习注释：只有kind等于'network'才进入这一分支。
            elif kind == "network":
                # 学习注释：调用route.abort，处理'failed'。
                route.abort("failed")
            else:
                # 学习注释：调用route.fulfill。
                route.fulfill(json={"answer": body["query"], "resolved_query": body["query"]})

        # 学习注释：调用page.route，处理'**/api/chat'。
        page.route("**/api/chat", intercept)
        # 学习注释：2项元组：调用page.locator，处理'#input'、调用page.locator，处理'#send'；保存到box、send。
        box, send = page.locator("#input"), page.locator("#send")

        # 学习注释：函数no_send接收调用时的上下文；把这一段可复用处理封装起来。
        def no_send():
            # 学习注释：调用box.fill，处理''。
            box.fill("   ")
            # 学习注释：调用send.click。
            send.click()
            # 学习注释：调用box.fill，处理'中文输入'。
            box.fill("中文输入")
            # 学习注释：调用box.dispatch_event，处理'keydown'。
            box.dispatch_event("keydown", {"key": "Enter", "isComposing": True})
            # 学习注释：调用box.press，处理'Shift+Enter'。
            box.press("Shift+Enter")
            # 学习注释：调用page.wait_for_timeout，处理250。
            page.wait_for_timeout(250)
            # 学习注释：测试要求：requests等于空列表；不满足就失败。
            assert requests == []
        # 学习注释：调用check，处理'空白、中文组合输入、Shift+Enter均不发送'。
        check("空白、中文组合输入、Shift+Enter均不发送", no_send)

        # 学习注释：函数busy接收调用时的上下文；把这一段可复用处理封装起来。
        def busy():
            # 学习注释：'hold'；保存到behavior['kind']。
            behavior["kind"] = "hold"
            # 学习注释：调用box.fill，处理'合成问题'。
            box.fill("合成问题")
            # 学习注释：调用box.press，处理'Enter'。
            box.press("Enter")
            # 学习注释：调用expect(send).to_be_disabled。
            expect(send).to_be_disabled()
            # 学习注释：调用box.fill，处理'保留草稿'。
            box.fill("保留草稿")
            # 学习注释：调用box.press，处理'Enter'。
            box.press("Enter")
            # 学习注释：调用page.evaluate，处理'sendMsg()'。
            page.evaluate("sendMsg()")
            # 学习注释：调用page.wait_for_timeout，处理250。
            page.wait_for_timeout(250)
            # 学习注释：测试要求：len(requests)等于1，并且len(pending)等于1；不满足就失败。
            assert len(requests) == 1 and len(pending) == 1
            # 学习注释：调用pending.pop().fulfill。
            pending.pop().fulfill(json={"answer": "合成回答", "resolved_query": "合成问题"})
            # 学习注释：调用expect(send).to_be_enabled。
            expect(send).to_be_enabled()
            # 学习注释：调用expect(box).to_have_value，处理'保留草稿'。
            expect(box).to_have_value("保留草稿")
            # 学习注释：调用expect(page.locator('.resolved')).to_have_count，处理0。
            expect(page.locator(".resolved")).to_have_count(0)
        # 学习注释：调用check，处理'请求中防重复发送、保留草稿、无虚假指代标识'。
        check("请求中防重复发送、保留草稿、无虚假指代标识", busy)

        # 学习注释：函数xss接收调用时的上下文；把这一段可复用处理封装起来。
        def xss():
            # 学习注释：'ok'；保存到behavior['kind']。
            behavior["kind"] = "ok"
            # 学习注释：'<img src=x onerror="window.__injected=1">'；保存到发送给服务的请求体（payload）。
            payload = '<img src=x onerror="window.__injected=1">'
            # 学习注释：调用box.fill，处理发送给服务的请求体（payload）。
            box.fill(payload)
            # 学习注释：调用send.click。
            send.click()
            # 学习注释：调用expect(page.locator('.assistant .bubble').last).to_have_text，处理发送给服务的请求体（payload）。
            expect(page.locator(".assistant .bubble").last).to_have_text(payload)
            # 学习注释：测试要求：page.evaluate('window.__injected || 0')等于0；不满足就失败。
            assert page.evaluate("window.__injected || 0") == 0
            # 学习注释：调用expect(page.locator('#chat img')).to_have_count，处理0。
            expect(page.locator("#chat img")).to_have_count(0)
        # 学习注释：调用check，处理'用户与模型文本均不执行HTML'。
        check("用户与模型文本均不执行HTML", xss)

        # 学习注释：函数error_case接收kind、prefix；把这一段可复用处理封装起来。
        def error_case(kind, prefix):
            # 学习注释：kind；保存到behavior['kind']。
            behavior["kind"] = kind
            # 学习注释：调用box.fill，处理'合成失败请求'。
            box.fill("合成失败请求")
            # 学习注释：调用send.click。
            send.click()
            # 学习注释：调用expect(page.locator('.assistant .bubble').last).to_contain_text，处理prefix。
            expect(page.locator(".assistant .bubble").last).to_contain_text(prefix)
            # 学习注释：调用expect(send).to_be_enabled。
            expect(send).to_be_enabled()
            # 学习注释：调用expect(box).to_be_focused。
            expect(box).to_be_focused()
        # 学习注释：调用check，处理'HTTP失败提示且恢复输入'。
        check("HTTP失败提示且恢复输入", lambda: error_case("http", "出错了"))
        # 学习注释：调用check，处理'网络失败提示且恢复输入'。
        check("网络失败提示且恢复输入", lambda: error_case("network", "网络错误"))
        # 学习注释：调用check，处理'失败后可重新发送'。
        check("失败后可重新发送", xss)

        # 学习注释：函数mobile接收调用时的上下文；把这一段可复用处理封装起来。
        def mobile():
            # 学习注释：调用page.set_viewport_size，处理包含width、height的字典。
            page.set_viewport_size({"width": 390, "height": 844})
            # 学习注释：测试要求：不满足（调用page.evaluate，处理'document.documentElement.scrollWidth > innerWidth'）；不满足就失败。
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            # 学习注释：调用expect(send).to_be_in_viewport。
            expect(send).to_be_in_viewport()
            # 学习注释：调用expect(box).to_be_in_viewport。
            expect(box).to_be_in_viewport()
            # 学习注释：调用page.screenshot。
            page.screenshot(path=str(args.output / "mobile-injected.png"))
        # 学习注释：调用check，处理'390px移动视口布局'。
        check("390px移动视口布局", mobile)
        # 学习注释：调用page.unroute，处理'**/api/chat'。
        page.unroute("**/api/chat", intercept)

        # 学习注释：只有args.live为真/非空才进入这一分支。
        if args.live:
            # 学习注释：调用page.set_viewport_size，处理包含width、height的字典。
            page.set_viewport_size({"width": 1280, "height": 900})
            # 学习注释：调用page.reload。
            page.reload()

            # 学习注释：函数live_question接收用户查询文本（query）、kind、month；把这一段可复用处理封装起来。
            def live_question(query, kind, month=None):
                # 学习注释：调用box.fill，处理用户查询文本（query）。
                box.fill(query)
                # 学习注释：调用page.expect_response，处理lambda r: r.url.endswith('/api/chat')；with结束时调用退出逻辑，关闭资源或恢复上下文。
                with page.expect_response(lambda r: r.url.endswith("/api/chat"), timeout=180000) as future:
                    # 学习注释：调用send.click。
                    send.click()
                # 学习注释：future的value属性（value）；保存到response。
                response = future.value
                # 学习注释：测试要求：response.status等于200；不满足就失败。
                assert response.status == 200
                # 学习注释：把HTTP响应正文解析为JSON对象；保存到data。
                data = response.json()
                # 学习注释：向responses末尾添加包含query、request、body的字典。
                responses.append({"query": query, "request": response.request.post_data_json, "body": data})
                # 学习注释：调用expect(send).to_be_enabled。
                expect(send).to_be_enabled(timeout=15000)
                # 学习注释：page.locator('.assistant')的last属性（last）；保存到bubble。
                bubble = page.locator(".assistant").last
                # 学习注释：调用expect(bubble.locator('.bubble')).to_have_text，处理data中键/位置为'answer'的值。
                expect(bubble.locator(".bubble")).to_have_text(data["answer"])
                # 学习注释：测试要求：data['answer']为真/非空，并且不满足（从data读取键'degraded'，缺少时取None）；不满足就失败。
                assert data["answer"] and not data.get("degraded"), "回答为空或已降级"
                # 学习注释：从data读取键'evidence'，缺少时取空列表；保存到ev。
                ev = data.get("evidence", [])
                # 学习注释：测试要求：ev为真/非空，并且检查是否每一项条件都成立；不满足就失败。
                assert ev and all(b.get("dataset_version") == data["dataset_version"] for b in ev)
                # 学习注释：只有kind等于'stat'才进入这一分支。
                if kind == "stat":
                    # 学习注释：从app.core导入versioning，复用对应模块的实现。
                    from app.core import versioning
                    # 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
                    import sqlite3
                    # 学习注释：versioning.load_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
                    snapshot = versioning.load_snapshot()
                    # 学习注释：打开SQLite连接，mode=ro表示只读；保存到数据库连接（conn）。
                    conn = sqlite3.connect(snapshot.db_path.resolve().as_uri()+"?mode=ro", uri=True)
                    # 学习注释：conn.execute('SELECT COUNT(DISTINCT 事件序号) FROM users WHERE 停电起始时间 >= ? AND 停电起始时间 < ?', (f'2025-{month:02d}-01', f'2025-{month + 1:02d}-01')).fetchone()中键/位置为0的值；保存到期望状态（expected）。
                    expected = conn.execute(
                        'SELECT COUNT(DISTINCT 事件序号) FROM users WHERE 停电起始时间 >= ? AND 停电起始时间 < ?',
                        (f"2025-{month:02d}-01", f"2025-{month+1:02d}-01")).fetchone()[0]
                    # 学习注释：关闭conn占用的连接或资源。
                    conn.close()
                    # 学习注释：遍历ev，只保留满足条件的项，将b['stat']收集为列表；保存到stats。
                    stats = [b["stat"] for b in ev if b.get("stat")]
                    # 学习注释：测试要求：stats为真/非空，并且检查是否至少有一项条件成立；不满足就失败。
                    assert stats and any(s.get("rows") == [{"停电次数": expected}] for s in stats)
                    # 学习注释：测试要求：str(expected)包含在data中键/位置为'answer'的值；不满足就失败。
                    assert str(expected) in data["answer"]
                # 学习注释：只有kind等于'cases'才进入这一分支。
                elif kind == "cases":
                    # 学习注释：测试要求：检查是否至少有一项条件成立；不满足就失败。
                    assert any(b.get("cases") for b in ev)
                else:
                    # 学习注释：测试要求：检查是否至少有一项条件成立；不满足就失败。
                    assert any(b.get("signal", {}).get("signal") == "out_of_scope" for b in ev if b.get("signal"))
                # 学习注释：调用bubble.locator('summary').click。
                bubble.locator("summary").click()
                # 学习注释：调用expect(bubble.locator('pre')).to_contain_text，处理data中键/位置为'dataset_version'的值。
                expect(bubble.locator("pre")).to_contain_text(data["dataset_version"])
                # 学习注释：只有month等于6才进入这一分支。
                if month == 6:
                    # 学习注释：调用page.screenshot。
                    page.screenshot(path=str(args.output / "desktop-live-stat.png"))
                # 学习注释：只有month等于7才进入这一分支。
                if month == 7:
                    # 学习注释：测试要求：'2025'包含在从data读取键'resolved_query'，缺少时取''；不满足就失败。
                    assert "2025" in data.get("resolved_query", "")
                    # 学习注释：responses[-1]['request']中键/位置为'session_id'的值；保存到sid。
                    sid = responses[-1]["request"]["session_id"]
                    # 学习注释：测试要求：sid为真/非空，并且sid等于responses[0]['request']中键/位置为'session_id'的值；不满足就失败。
                    assert sid and sid == responses[0]["request"]["session_id"]
                    # 学习注释：调用page.screenshot。
                    page.screenshot(path=str(args.output / "desktop-live-followup.png"))
            # 学习注释：调用check，处理'真实统计与SQLite对照、展开证据'。
            check("真实统计与SQLite对照、展开证据", lambda: live_question("2025年6月一共发生了多少次停电？", "stat", 6), "live")
            # 学习注释：调用check，处理'真实多轮追问与SQLite对照'。
            check("真实多轮追问与SQLite对照", lambda: live_question("那2025年7月呢？", "stat", 7), "live")
            # 学习注释：调用check，处理'真实历史事件检索、展示来源'。
            check("真实历史事件检索、展示来源", lambda: live_question("请查找电缆故障导致停电的历史事件，不限制线路和时间。", "cases"), "live")
            # 学习注释：调用check，处理'真实覆盖范围外请求'。
            check("真实覆盖范围外请求", lambda: live_question("查找2024年1月电缆故障导致停电的事件。", "out"), "live")

        # 学习注释：调用check，处理'无浏览器脚本异常'。
        check("无浏览器脚本异常", lambda: expect_no_errors(errors), "browser")
        # 学习注释：关闭browser占用的连接或资源。
        browser.close()
    # 学习注释：遍历Path('app/static/index.html')、Path(__file__)，将str(path)→hashlib.sha256(path.read_bytes()).hexdigest()收集为字典；保存到sources。
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
               [Path("app/static/index.html"), Path(__file__)]}
    # 学习注释：包含date、browser_channel、source_sha256、results、page_errors的字典；保存到待保存的验收报告（report）。
    report = {"date": datetime.now().isoformat(), "browser_channel": args.channel,
              "source_sha256": sources, "results": results, "page_errors": errors}
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (args.output / "browser-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (args.output / "live-responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
    # 学习注释：返回把检查是否至少有一项条件成立转换为整数，结束本次函数调用。
    return int(any(r["status"] != "pass" for r in results))


# 学习注释：函数expect_no_errors接收errors；把这一段可复用处理封装起来。
def expect_no_errors(errors):
    # 学习注释：测试要求：errors为空或为False；不满足就失败。
    assert not errors, errors


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：以异常终止这一条路径：调用main；上层不能把它当作正常成功。
    raise SystemExit(main())
