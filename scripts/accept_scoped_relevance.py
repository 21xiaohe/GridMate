# 阅读导航：业务网页相关性验收
# 本文件作用：从网页验证窄范围无关候选被拦下、歧义提示和正常案例保留。
# 建议读法：会调用真实模型；场景依赖本地业务台账，不能套到仅6个事件的合成演示集。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""真实浏览器核对相关性收尾行为。固定业务样本，需先启动对应业务版本服务。"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：从playwright.sync_api引入sync_playwright, expect，用于控制浏览器并检查页面行为。
from playwright.sync_api import sync_playwright, expect
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main(url):
    # 学习注释：按目录层级拼接文件路径 Path('data/evaluations') / ('scoped_relevance_' + versioning.new_version())；保存到本轮报告目录（folder）。
    folder = Path("data/evaluations") / ("scoped_relevance_" + versioning.new_version())
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    folder.mkdir(parents=True)
    # 学习注释：空列表；保存到结果列表（results）。
    results = []
    # 学习注释：调用sync_playwright；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with sync_playwright() as p:
        # 学习注释：调用p.chromium.launch；保存到browser。
        browser = p.chromium.launch(channel="msedge", headless=True)
        # 学习注释：调用browser.new_page；保存到page。
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        # 学习注释：遍历3项列表：2项元组：'negative_scope'、'请查找线路32上电缆故障导致停电的历史事件，严格限定线路32，不要查其他线路。'、2项元组：'uncertain_candidates'、'查找计划检修…，每轮把当前项交给name、用户查询文本（query）。
        for name, query in [
            ("negative_scope", "请查找线路32上电缆故障导致停电的历史事件，严格限定线路32，不要查其他线路。"),
            ("uncertain_candidates", "查找计划检修停电的历史事件，最多5条，不限定线路或时间。"),
            ("positive_cases", "查找开关跳闸的历史停电案例，最多3条，不限定线路或时间。"),
        ]:
            # 学习注释：包含case、query、status的字典；保存到row。
            row = {"case": name, "query": query, "status": "fail"}
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 每题独立会话，避免上一题范围影响下一题。
                page.goto(url)
                # 学习注释：调用page.locator('#input').fill，处理用户查询文本（query）。
                page.locator("#input").fill(query)
                # 学习注释：调用page.expect_response，处理lambda r: r.url.endswith('/api/chat')；with结束时调用退出逻辑，关闭资源或恢复上下文。
                with page.expect_response(lambda r: r.url.endswith("/api/chat"), timeout=180000) as future:
                    # 学习注释：调用page.locator('#send').click。
                    page.locator("#send").click()
                # 学习注释：future的value属性（value）；保存到response。
                response = future.value
                # 学习注释：把HTTP响应正文解析为JSON对象；保存到body。
                body = response.json()
                # 学习注释：versioning.atomic_json的执行结果。
                versioning.atomic_json(folder/(name+".json"), body)
                # 学习注释：测试要求：response.status等于200，并且不满足（body['degraded']为真/非空）；不满足就失败。
                assert response.status == 200 and not body["degraded"]
                # 学习注释：调用expect(page.locator('#send')).to_be_enabled。
                expect(page.locator("#send")).to_be_enabled()
                # 学习注释：调用expect(page.locator('.assistant .bubble').last).to_have_text，处理body中键/位置为'answer'的值。
                expect(page.locator(".assistant .bubble").last).to_have_text(body["answer"])
                # 学习注释：遍历body['evidence']，只保留满足条件的项，将b收集为列表；保存到bundles。
                bundles = [b for b in body["evidence"] if b.get("signal")]
                # 学习注释：测试要求：bundles为真/非空；不满足就失败。
                assert bundles, "模型没有调用检索工具"
                # 学习注释：遍历bundles，将c收集为列表；保存到案例列表（cases）。
                cases = [c for b in bundles for c in b["cases"]]
                # 学习注释：遍历bundles，将r收集为列表；保存到decisions。
                decisions = [r for b in bundles for r in b["retrieval_trace"].get("relevance_review", [])]
                # 学习注释：只有name等于'negative_scope'才进入这一分支。
                if name == "negative_scope":
                    # 学习注释：测试要求：案例列表（cases）为空或为False；不满足就失败。
                    assert not cases
                    # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
                    assert all(b["status"] == "insufficient_evidence" for b in bundles)
                    # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
                    assert all(b["effective_filters"].get("line") == "线路32" for b in bundles)
                    # 学习注释：测试要求：decisions为真/非空，并且检查是否每一项条件都成立；不满足就失败。
                    assert decisions and all(r["decision"] == "unsupported" for r in decisions)
                    # 学习注释：测试要求：'依据'包含在body中键/位置为'answer'的值，或者'证据'包含在body中键/位置为'answer'的值；不满足就失败。
                    assert "依据" in body["answer"] or "证据" in body["answer"]
                # 学习注释：只有name等于'uncertain_candidates'才进入这一分支。
                elif name == "uncertain_candidates":
                    # 学习注释：遍历decisions，只保留满足条件的项，将r['event_id']收集为集合；保存到excluded。
                    excluded = {r["event_id"] for r in decisions if r["decision"] == "uncertain"}
                    # 学习注释：测试要求：len(excluded)等于2，并且案例列表（cases）为真/非空；不满足就失败。
                    assert len(excluded) == 2 and cases
                    # 学习注释：测试要求：不满足（调用excluded.intersection，处理遍历案例列表（cases），将c['event_id']收集为生成器）；不满足就失败。
                    assert not excluded.intersection(c["event_id"] for c in cases)
                    # 学习注释：测试要求：'确认'包含在body中键/位置为'answer'的值，或者'不明确'包含在body中键/位置为'answer'的值，或者'不足'包含在body中键/位置为'answer'的值；不满足就失败。
                    assert "确认" in body["answer"] or "不明确" in body["answer"] or "不足" in body["answer"]
                else:
                    # 学习注释：测试要求：案例列表（cases）为真/非空，并且检查是否每一项条件都成立；不满足就失败。
                    assert cases and all(c["supporting_quotes"] and c["relevance_status"] == "supported" for c in cases)
                # 学习注释：遍历案例列表（cases），每轮把当前项交给c。
                for c in cases:
                    # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
                    assert all(q in c["text"] for q in c["supporting_quotes"])
                # 学习注释：调用page.locator('.assistant summary').last.click。
                page.locator(".assistant summary").last.click()
                # 学习注释：调用expect(page.locator('.assistant pre').last).to_contain_text，处理body中键/位置为'dataset_version'的值。
                expect(page.locator(".assistant pre").last).to_contain_text(body["dataset_version"])
                # 学习注释：调用page.screenshot。
                page.screenshot(path=str(folder/(name+".png")))
                # 学习注释：将新字段合并到row，同名字段会覆盖旧值。
                row.update(status="pass", returned_cases=len(cases), verification_status=body["verification_status"],
                           review_decisions=[{"event_id": r["event_id"], "decision": r["decision"]} for r in decisions])
            except Exception as exc:
                # 学习注释：将新字段合并到row，同名字段会覆盖旧值。
                row.update(error_type=type(exc).__name__, error=str(exc))
            # 学习注释：向结果列表（results）末尾添加row。
            results.append(row)
            # 学习注释：versioning.atomic_json的执行结果。
            versioning.atomic_json(folder/"result.json", {"results": results})
            # 学习注释：在命令行输出本步骤结果或进度。
            print(name, row["status"], flush=True)
        # 学习注释：关闭browser占用的连接或资源。
        browser.close()
    # 学习注释：在命令行输出本步骤结果或进度。
    print("report:", folder/"result.json")
    # 学习注释：返回把检查是否至少有一项条件成立转换为整数，结束本次函数调用。
    return int(any(r["status"] != "pass" for r in results))


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--url'。
    parser.add_argument("--url", default="http://127.0.0.1:8121")
    # 学习注释：以异常终止这一条路径：调用main，处理parser.parse_args()的url属性（url）；上层不能把它当作正常成功。
    raise SystemExit(main(parser.parse_args().url))
