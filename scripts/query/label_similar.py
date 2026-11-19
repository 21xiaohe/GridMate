# -*- coding: utf-8 -*-
# 阅读导航：检索标注材料生成
# 本文件作用：检索候选并保存问题、事件及正文，方便后续逐条标注。
# 建议读法：会调用真实检索服务；候选正文来自业务数据，生成材料不应直接公开。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：相似检索人工标注辅助

数字类问法可自动核对，但「这个事件和 query 是否真相似」是语义判断，机器自评不可信，
需要人工标 ground truth。本脚本对一批候选 query 跑检索，把 top-k 结果（含事件文本）
打印出来供标注，并生成一个标注模板文件（标注人填 1/0）。

运行：PYTHONPATH=. python scripts/query/label_similar.py
"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：从app.core导入similar，复用对应模块的实现。
from app.core import similar

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'tests' / 'data' / 'similar_label_template.json'；保存到OUT_PATH。
OUT_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "data" / "similar_label_template.json"

# 候选 query（覆盖不同停电类型，检验检索能否区分）
QUERIES = [
    "电缆故障导致的停电",
    "开关跳闸的故障",
    "箱变故障",
    "计划检修停电",
    "10kV线路检修",
    "业扩工程导致的停电",
    "计划调电",
    "接地故障",
    "母线故障",
    "低压分支箱故障",
]


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main():
    # 学习注释：空列表；保存到template。
    template = []
    # 学习注释：遍历QUERIES，每轮把当前项交给q。
    for q in QUERIES:
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到res。
        res = await similar.search_similar(q, top_k=5)
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"\n{'='*70}\nQuery: {q}\n{'='*70}")
        # 学习注释：包含query、labels的字典；保存到entry。
        entry = {"query": q, "labels": []}
        # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、r。
        for i, r in enumerate(res):
            # 学习注释：调用(r.get('事件文本') or '').strip().replace，处理''；保存到txt。
            txt = (r.get("事件文本") or "").strip().replace("\n", " ")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"\n[{i+1}] 事件{r['事件序号']} | {r['停电性质']} | {r['电压等级']} | 重排分{r['_rerank_score']:.3f}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"    文本: {txt[:80]}")
            # 学习注释：向entry['labels']末尾添加包含event_id、outage_type、text、similar的字典。
            entry["labels"].append({
                "event_id": r["事件序号"],
                "outage_type": r["停电性质"],
                "text": txt,
                "similar": None,  # 标注人填 1(相似) / 0(不相似)
            })
        # 学习注释：向template末尾添加entry。
        template.append(entry)

    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    OUT_PATH.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"\n\n标注模板已生成: {OUT_PATH}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("请在每条的 similar 字段填 1(相似) 或 0(不相似)，存为 similar_label_gt.json")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(main())
