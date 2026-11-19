# -*- coding: utf-8 -*-
# 阅读导航：检索策略对比
# 本文件作用：对固定问题比较不同召回/重排路径，并用本地标签统计候选相关性。
# 建议读法：这是历史诊断脚本；不同数据、标签和模型的报告不能直接比较，缺失标签不能当确定正例。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""相似事件检索：修复重排 bug 前后的 Precision@5 对比

背景：
  - 旧结果（修复前）：tests/data/similar_label_template.json，10 query 各 Top-5，重排时漏传 text 正文。
  - 旧标签（ground truth）：来自 AI 辅助语义初审（相似检索校验结果-2026-11-16.md），
    逐 event_id 给出 1(相关)/0(不相关)/null(待复核)。
  - 新结果（修复后）：用当前代码重新跑 10 query 的 Top-5，重排已拼入 text 正文。

对比口径（诚实声明）：
  - 标签是按「旧 Top-5」标的；修复后 Top-5 可能出现旧标签没覆盖的新 event_id。
  - 因此对比时，只对「旧标签已覆盖的 event_id」计分（可判定集），新出现事件单独列出、不计入分母。
  - P@5 = 可判定且判为「相关」的数量 / 5（保持每 query 固定 5 个位置的分母），
    待复核(null)按保守计入（不相关），并另给一个「仅算明确 0/1」的口径作参考。

运行：PYTHONPATH=. python scripts/query/eval_similar_compare.py
"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：从app.core导入similar，复用对应模块的实现。
from app.core import similar

# 学习注释：Path(__file__).resolve().parent.parent的parent属性（parent）；保存到BASE。
BASE = Path(__file__).resolve().parent.parent.parent
# 学习注释：按目录层级拼接文件路径 BASE / 'tests' / 'data' / 'similar_label_template.json'；保存到OLD_TEMPLATE。
OLD_TEMPLATE = BASE / "tests" / "data" / "similar_label_template.json"
# 学习注释：按目录层级拼接文件路径 BASE / 'tests' / 'data' / 'similar_label_ai_review_20261116.json'；保存到AI_REVIEW。
AI_REVIEW = BASE / "tests" / "data" / "similar_label_ai_review_20261116.json"
# 学习注释：按目录层级拼接文件路径 BASE / 'tests' / 'data' / 'similar_compare_result.json'；保存到OUT_PATH。
OUT_PATH = BASE / "tests" / "data" / "similar_compare_result.json"

# 学习注释：10项列表：'电缆故障导致的停电'、'开关跳闸的故障'、'箱变故障'、'计划检修停电'、'10kV线路检修'、'业扩工程导致的停电'、'计划调电'、'接地故障'、'母线故障'、'低压分支箱故障'；保存到QUERIES。
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


# 学习注释：返回 {query: {event_id: 1/0/null}}，从 AI 初审 JSON 读；缺文件则回退到旧模板内嵌标签。
def load_labels() -> dict[str, dict[str, int]]:
    """返回 {query: {event_id: 1/0/null}}，从 AI 初审 JSON 读；缺文件则回退到旧模板内嵌标签。"""
    # 学习注释：空字典；保存到labels。
    labels: dict[str, dict[str, int]] = {}
    # 学习注释：只有检查路径是否存在才进入这一分支。
    if AI_REVIEW.exists():
        # 学习注释：把JSON文本解析为Python对象；保存到raw。
        raw = json.loads(AI_REVIEW.read_text(encoding="utf-8"))
        # AI 初审 JSON 顶层是 dict，真正的 query 列表在 "queries" 字段
        qlist = raw.get("queries", []) if isinstance(raw, dict) else raw
        # 学习注释：遍历qlist，每轮把当前项交给q。
        for q in qlist:
            # 学习注释：从q读取键'query'，缺少时取None；保存到q_text。
            q_text = q.get("query")
            # 学习注释：从q读取键'labels'，缺少时取空列表；保存到q_labels。
            q_labels = q.get("labels", [])
            # 学习注释：空字典；保存到m。
            m = {}
            # 学习注释：遍历q_labels，每轮把当前项交给l。
            for l in q_labels:
                # 学习注释：从l读取键'similar'，缺少时取None；保存到m[l['event_id']]。
                m[l["event_id"]] = l.get("similar")
            # 学习注释：m；保存到labels[q_text]。
            labels[q_text] = m
        # 学习注释：返回labels，结束本次函数调用。
        return labels

    # 回退：旧模板里的 similar 字段（大多为 null）
    raw = json.loads(OLD_TEMPLATE.read_text(encoding="utf-8"))
    # 学习注释：遍历raw，每轮把当前项交给q。
    for q in raw:
        # 学习注释：遍历q['labels']，将l['event_id']→l.get('similar')收集为字典；保存到labels[q['query']]。
        labels[q["query"]] = {l["event_id"]: l.get("similar") for l in q["labels"]}
    # 学习注释：返回labels，结束本次函数调用。
    return labels


# 学习注释：算 P@5。
def p5(label_list: list) -> tuple[float, float]:
    """算 P@5。
    返回 (保守口径, 明确口径)：
      - 保守口径：null(待复核) 算「不相关」
      - 明确口径：只统计已明确标 0/1 的位置，分母=明确标出的数量
    """
    # 学习注释：计算label_list的元素数量；保存到n。
    n = len(label_list)
    # 学习注释：只有n等于0才进入这一分支。
    if n == 0:
        # 学习注释：返回2项元组：0.0、0.0，结束本次函数调用。
        return 0.0, 0.0
    # 学习注释：计算 sum((1 for x in label_list if x == 1)) / n；保存到conservative。
    conservative = sum(1 for x in label_list if x == 1) / n
    # 学习注释：遍历label_list，只保留满足条件的项，将x收集为列表；保存到decided。
    decided = [x for x in label_list if x in (0, 1)]
    # 学习注释：只有decided为空或为False才进入这一分支。
    if not decided:
        # 学习注释：0.0；保存到explicit。
        explicit = 0.0
    else:
        # 学习注释：计算 sum((1 for x in decided if x == 1)) / len(decided)；保存到explicit。
        explicit = sum(1 for x in decided if x == 1) / len(decided)
    # 学习注释：返回2项元组：conservative、explicit，结束本次函数调用。
    return conservative, explicit


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main():
    # 学习注释：调用load_labels；保存到labels。
    labels = load_labels()
    # 学习注释：只有labels为空或为False才进入这一分支。
    if not labels:
        # 学习注释：在命令行输出本步骤结果或进度。
        print("找不到标签文件，退出")
        # 学习注释：提前结束当前函数，不再执行后面的步骤。
        return

    # 读旧结果（修复前 Top-5 的 event_id）
    old_raw = json.loads(OLD_TEMPLATE.read_text(encoding="utf-8"))
    # 学习注释：遍历old_raw，将q['query']→[l['event_id'] for l in q['labels']]收集为字典；保存到old_order。
    old_order = {q["query"]: [l["event_id"] for l in q["labels"]] for q in old_raw}

    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"{'Query':<22} {'旧P@5(保守)':>12} {'新P@5(保守)':>12} {'旧P@5(明确)':>12} {'新P@5(明确)':>12} {'新出现未标':>10}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("-" * 90)

    # 学习注释：空列表；保存到查询或表格行（rows）。
    rows = []
    # 学习注释：0.0；保存到agg_old_c、agg_new_c、agg_old_e、agg_new_e。
    agg_old_c = agg_new_c = agg_old_e = agg_new_e = 0.0
    # 学习注释：0；保存到agg_new_unseen。
    agg_new_unseen = 0

    # 学习注释：遍历QUERIES，每轮把当前项交给q。
    for q in QUERIES:
        # 学习注释：从labels读取键q，缺少时取空字典；保存到ql。
        ql = labels.get(q, {})
        # 学习注释：从old_order读取键q，缺少时取空列表；保存到old_ids。
        old_ids = old_order.get(q, [])

        # 旧结果：用旧 event_id 顺序映射标签
        old_label = [ql.get(eid, None) for eid in old_ids]
        # 学习注释：调用p5，处理old_label；保存到old_c、old_e。
        old_c, old_e = p5(old_label)

        # 新结果：重新跑当前代码
        res = await similar.search_similar(q, top_k=5)
        # 学习注释：遍历res，将r['事件序号']收集为列表；保存到new_ids。
        new_ids = [r["事件序号"] for r in res]
        # 学习注释：遍历new_ids，将ql.get(eid, None)收集为列表；保存到new_label。
        new_label = [ql.get(eid, None) for eid in new_ids]
        # 学习注释：累计各项数值（布尔值True按1计数）；保存到unseen。
        unseen = sum(1 for eid in new_ids if eid not in ql)  # 新出现、旧标签没覆盖
        # 学习注释：调用p5，处理new_label；保存到new_c、new_e。
        new_c, new_e = p5(new_label)

        # 学习注释：用old_c更新agg_old_c；下方+=等写法把计算结果存回原变量。
        agg_old_c += old_c
        # 学习注释：用new_c更新agg_new_c；下方+=等写法把计算结果存回原变量。
        agg_new_c += new_c
        # 学习注释：用old_e更新agg_old_e；下方+=等写法把计算结果存回原变量。
        agg_old_e += old_e
        # 学习注释：用new_e更新agg_new_e；下方+=等写法把计算结果存回原变量。
        agg_new_e += new_e
        # 学习注释：用unseen更新agg_new_unseen；下方+=等写法把计算结果存回原变量。
        agg_new_unseen += unseen

        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"{q:<22} {old_c:>12.2f} {new_c:>12.2f} {old_e:>12.2f} {new_e:>12.2f} {unseen:>10}")
        # 学习注释：向查询或表格行（rows）末尾添加包含query、old_top5_ids、new_top5_ids、old_label、new_label、new_unseen_count的字典。
        rows.append({
            "query": q,
            "old_top5_ids": old_ids,
            "new_top5_ids": new_ids,
            "old_label": old_label,
            "new_label": new_label,
            "new_unseen_count": unseen,
        })

    # 学习注释：计算QUERIES的元素数量；保存到n。
    n = len(QUERIES)
    # 学习注释：在命令行输出本步骤结果或进度。
    print("-" * 90)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"{'平均':<22} {agg_old_c/n:>12.2f} {agg_new_c/n:>12.2f} {agg_old_e/n:>12.2f} {agg_new_e/n:>12.2f} {agg_new_unseen:>10}")

    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    OUT_PATH.write_text(json.dumps({
        "note": "保守口径=待复核(null)算不相关；明确口径=只统计已明确标0/1的位置",
        "avg_old_conservative": agg_old_c / n,
        "avg_new_conservative": agg_new_c / n,
        "avg_old_explicit": agg_old_e / n,
        "avg_new_explicit": agg_new_e / n,
        "new_unseen_total": agg_new_unseen,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"\n结果已写入: {OUT_PATH}")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(main())
