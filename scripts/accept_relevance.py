# 阅读导航：合成相关性样例验收
# 本文件作用：让真实模型判断AI编写的正例、难负例和歧义例，保存规则和提示词哈希。
# 建议读法：development与validation是功能样例拆分，不等于独立人工业务测试集。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""合成正例/难负例/歧义例真实模型验收；不代表人工业务金标准。"""
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

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings
# 学习注释：从app.core导入relevance, versioning，复用对应模块的实现。
from app.core import relevance, versioning


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main(split: str):
    # 学习注释：构造文件路径对象：'tests/fixtures/relevance_cases.json'；保存到fixture。
    fixture = Path("tests/fixtures/relevance_cases.json")
    # 学习注释：把JSON文本解析为Python对象；保存到data。
    data = json.loads(fixture.read_text(encoding="utf-8"))
    # 学习注释：按目录层级拼接文件路径 Path('data/evaluations') / ('relevance_synthetic_' + versioning.new_version())；保存到本轮报告目录（folder）。
    folder = Path("data/evaluations") / ("relevance_synthetic_" + versioning.new_version())
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    folder.mkdir(parents=True)
    # 学习注释：包含split、provenance、fixture_sha256、policy、prompt_sha256、model的字典；保存到待保存的验收报告（report）。
    report = {"split": split, "provenance": data["provenance"], "fixture_sha256": versioning.file_sha256(fixture),
              "policy": relevance.POLICY_VERSION, "prompt_sha256": relevance.PROMPT_SHA256,
              "model": settings.chat_model, "results": []}
    # 学习注释：创建并发名额限制器，防止同时启动过多调用；保存到semaphore。
    semaphore = asyncio.Semaphore(3)
    # 学习注释：异步函数run接收case；把这一段可复用处理封装起来。
    async def run(case):
        # 学习注释：semaphore；with结束时调用退出逻辑，关闭资源或恢复上下文。
        async with semaphore:
            # 学习注释：读取单调高精度时钟，用于相减计算耗时；保存到本轮计时起点（started）。
            started = perf_counter()
            # 学习注释：包含id、expected的字典；保存到result。
            result = {"id": case["id"], "expected": case["label"]}
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 学习注释：relevance.review_candidates的执行结果，await等待结果后再继续；保存到候选依据判定（reviews）。
                reviews = await relevance.review_candidates(case["query"], [
                    {"事件序号": case["id"], "事件文本": case["text"]}])
                # 学习注释：将新字段合并到result，同名字段会覆盖旧值。
                result.update(status="pass" if reviews[0]["decision"] == case["label"] else "fail",
                              review=reviews[0])
            except Exception as exc:
                # 学习注释：将新字段合并到result，同名字段会覆盖旧值。
                result.update(status="fail", error_type=type(exc).__name__)
            # 学习注释：调用round，处理计算 perf_counter() - started；保存到result['seconds']。
            result["seconds"] = round(perf_counter()-started, 2)
            # 学习注释：向report['results']末尾添加result。
            report["results"].append(result)
            # 学习注释：versioning.atomic_json的执行结果。
            versioning.atomic_json(folder/"result.json", report)
            # 学习注释：在命令行输出本步骤结果或进度。
            print(case["id"], result["status"], flush=True)
    # 学习注释：并发运行这些异步任务并收集结果，await等待结果后再继续。
    await asyncio.gather(*(run(c) for c in data["cases"] if c["split"] == split))
    # 学习注释：调用report['results'].sort。
    report["results"].sort(key=lambda r: r["id"])
    # 学习注释：检查是否每一项条件都成立时取'pass'，否则取'fail'；保存到report['status']。
    report["status"] = "pass" if all(r["status"] == "pass" for r in report["results"]) else "fail"
    # 学习注释：versioning.atomic_json的执行结果。
    versioning.atomic_json(folder/"result.json", report)
    # 学习注释：在命令行输出本步骤结果或进度。
    print("report:", folder/"result.json")
    # 学习注释：返回把report['status']不等于'pass'转换为整数，结束本次函数调用。
    return int(report["status"] != "pass")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--split'。
    parser.add_argument("--split", choices=["development", "validation"], required=True)
    # 学习注释：以异常终止这一条路径：启动事件循环，执行脚本的异步主函数；上层不能把它当作正常成功。
    raise SystemExit(asyncio.run(main(parser.parse_args().split)))
