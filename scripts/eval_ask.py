# -*- coding: utf-8 -*-
# 阅读导航：统计与检索历史评测
# 本文件作用：读取本地题目与参考结果，执行问数或检索并逐题记录检查结果。
# 建议读法：真实调用会消耗模型额度；旧评测口径需结合最新固定集与验收报告理解。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数评测：三指标分开统计 + 独立参考查询对照

三个指标（不混成一个"准确率"）：
  1. 接口成功：意图识别是否解析出合法模板 / 是否拦截越界
  2. 流程执行成功：参数校验 + SQL 执行 / 检索是否跑通
  3. 回答事实正确：数字是否正确（用**独立参考查询**核对，不走被测函数自己的 SQL）

关键原则：统计答案的标准答案，用**不同于 query_templates 的独立 SQL 写法**重算。
  否则就是"让同一个统计函数既产答案又产标准答案"，等于自己给自己打分，评测失去意义。

运行：PYTHONPATH=. python scripts/eval_ask.py
"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：从app.core导入ask并简称ask_mod，复用对应模块的实现。
from app.core import ask as ask_mod
# 学习注释：从app.core导入similar并简称similar_mod，复用对应模块的实现。
from app.core import similar as similar_mod

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent / 'tests' / 'data' / 'ask_samples.json'；保存到SAMPLES。
SAMPLES = Path(__file__).resolve().parent.parent / "tests" / "data" / "ask_samples.json"


# ---------------------------------------------------------------------------
# 参考值不再现场计算：标准答案离线预存在样例文件的 ref_* 字段里，
# 评测只做「比对」，不做「现算」（二次审阅问题 3/9）。现场参考查询函数已删除。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 各类型的核对逻辑
# ---------------------------------------------------------------------------

# 学习注释：问数类样例：核对意图模板 + 状态 + 数字正确性（比对离线预存 ref_answer）。
async def _check_ask(s: dict) -> dict:
    """问数类样例：核对意图模板 + 状态 + 数字正确性（比对离线预存 ref_answer）。

    关键修正（二次审阅问题 9）：标准答案来自样例里**离线预存**的 ref_* 字段，
    而不是拿被测系统自己解析出的 intent 参数去现场算参考值——否则模型把
    「线路A」解析成「线路B」，双方按 B 算，错题也能判对。
    """
    # 学习注释：ask_mod.ask的执行结果，await等待结果后再继续；保存到r。
    r = await ask_mod.ask(s["query"])
    # 学习注释：包含id、query的字典；保存到out。
    out = {"id": s["id"], "query": s["query"]}

    # 指标1：接口成功（模板识别正确）
    # 越界拦截类样例（expect_intercept=true）不强求 template 精确值，
    # 只要求"被正确拦截"（confidence 低 → 状态 low_confidence/out_of_coverage/error）。
    got_template = (r.get("intent") or {}).get("template")
    # 学习注释：只有从s读取键'expect_intercept'，缺少时取None才进入这一分支。
    if s.get("expect_intercept"):
        # 学习注释：True（成立）；保存到out['template_ok']。
        out["template_ok"] = True  # 越界场景下模板取值不参与判定
        # 学习注释：got_template；保存到out['template']。
        out["template"] = got_template
    else:
        # 学习注释：got_template等于s中键/位置为'expect_template'的值；保存到out['template_ok']。
        out["template_ok"] = (got_template == s["expect_template"])
        # 学习注释：got_template；保存到out['template']。
        out["template"] = got_template

    # 指标2：流程执行成功（状态符合预期）
    out["status_ok"] = (r.get("status") == s["expect_status"])
    # 学习注释：从r读取键'status'，缺少时取None；保存到out['status']。
    out["status"] = r.get("status")

    # 指标3：事实正确（仅 status=ok 时核对数字，比对离线 ref_answer，不现场算参考值）
    out["fact_ok"] = None
    # 学习注释：只有r.get('status')等于'ok'，并且s['expect_status']等于'ok'才进入这一分支。
    if r.get("status") == "ok" and s["expect_status"] == "ok":
        # 学习注释：从r读取键'intent'，缺少时取空字典；保存到intent。
        intent = r.get("intent", {})
        # 学习注释：从intent读取键'template'，缺少时取None；保存到选中的SQL模板定义（tpl）。
        tpl = intent.get("template")
        # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
        try:
            # 学习注释：只有选中的SQL模板定义（tpl）等于'line_outage_count'才进入这一分支。
            if tpl == "line_outage_count":
                # ① 先校验解析出的参数是否等于预期（参数错直接判错）
                if intent.get("line_name") != s.get("ref_line_name"):
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"解析线路={intent.get('line_name')}，预期={s.get('ref_line_name')}（参数错）"
                else:
                    # 学习注释：r['result']['rows'][0]中键/位置为'停电次数'的值；保存到got。
                    got = r["result"]["rows"][0]["停电次数"]
                    # 学习注释：s中键/位置为'ref_answer'的值；保存到ref。
                    ref = s["ref_answer"]
                    # 学习注释：got等于ref；保存到out['fact_ok']。
                    out["fact_ok"] = (got == ref)
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"线路{s['ref_line_name']}: 系统={got}, 离线参考={ref}"
            # 学习注释：只有选中的SQL模板定义（tpl）等于'supply_center_energy'才进入这一分支。
            elif tpl == "supply_center_energy":
                # 学习注释：只有intent.get('supply_center')不等于从s读取键'ref_supply_center'，缺少时取None才进入这一分支。
                if intent.get("supply_center") != s.get("ref_supply_center"):
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"解析供服中心={intent.get('supply_center')}，预期={s.get('ref_supply_center')}（参数错）"
                else:
                    # 学习注释：调用round，处理r['result']['rows'][0]中键/位置为'缺供电量总和'的值；保存到got。
                    got = round(r["result"]["rows"][0]["缺供电量总和"], 2)
                    # 学习注释：调用round，处理s中键/位置为'ref_answer'的值；保存到ref。
                    ref = round(s["ref_answer"], 2)
                    # 学习注释：abs(got - ref)小于0.01；保存到out['fact_ok']。
                    out["fact_ok"] = (abs(got - ref) < 0.01)
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"供服中心{s['ref_supply_center']}: 系统={got}, 离线参考={ref}"
            # 学习注释：只有选中的SQL模板定义（tpl）等于'time_range_outage_count'才进入这一分支。
            elif tpl == "time_range_outage_count":
                # 学习注释：只有intent.get('start_time')不等于从s读取键'ref_start'，缺少时取None，或者intent.get('end_time')不等于从s读取键'ref_end'，缺少时取None才进入这一分支。
                if intent.get("start_time") != s.get("ref_start") or intent.get("end_time") != s.get("ref_end"):
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = (f"解析区间=[{intent.get('start_time')},{intent.get('end_time')})，"
                                     f"预期=[{s.get('ref_start')},{s.get('ref_end')})（边界错）")
                else:
                    # 学习注释：r['result']['rows'][0]中键/位置为'停电次数'的值；保存到got。
                    got = r["result"]["rows"][0]["停电次数"]
                    # 学习注释：s中键/位置为'ref_answer'的值；保存到ref。
                    ref = s["ref_answer"]
                    # 学习注释：got等于ref；保存到out['fact_ok']。
                    out["fact_ok"] = (got == ref)
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"[{s['ref_start']},{s['ref_end']}): 系统={got}, 离线参考={ref}"
            # 学习注释：只有选中的SQL模板定义（tpl）等于'outage_type_ratio'才进入这一分支。
            elif tpl == "outage_type_ratio":
                # ② 占比逐类比较：不只比种类数，逐类核对次数与百分比
                got_rows = r["result"]["rows"]
                # 学习注释：从s读取键'ref_distribution'，缺少时取空列表；保存到ref_dist。
                ref_dist = s.get("ref_distribution", [])
                # 学习注释：只有len(got_rows)不等于计算ref_dist的元素数量才进入这一分支。
                if len(got_rows) != len(ref_dist):
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"种类数不符: 系统={len(got_rows)}, 预期={len(ref_dist)}"
                else:
                    # 学习注释：遍历got_rows，将row['停电性质']→row收集为字典；保存到got_map。
                    got_map = {row["停电性质"]: row for row in got_rows}
                    # 学习注释：遍历ref_dist，每轮把当前项交给ref_row。
                    for ref_row in ref_dist:
                        # 学习注释：ref_row中键/位置为'停电性质'的值；保存到name。
                        name = ref_row["停电性质"]
                        # 学习注释：从got_map读取键name，缺少时取None；保存到g。
                        g = got_map.get(name)
                        # 学习注释：只有g尚未设置（None），或者g['停电次数']不等于ref_row中键/位置为'停电次数'的值，或者abs(g['占比_pct'] - ref_row['占比_pct'])大于0.01才进入这一分支。
                        if g is None or g["停电次数"] != ref_row["停电次数"] or abs(g["占比_pct"] - ref_row["占比_pct"]) > 0.01:
                            # 学习注释：False（不成立）；保存到out['fact_ok']。
                            out["fact_ok"] = False
                            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                            out["detail"] = f"性质「{name}」不符: 系统={g}，预期={ref_row}"
                            # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                            break
                    else:
                        # 学习注释：True（成立）；保存到out['fact_ok']。
                        out["fact_ok"] = True
                        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                        out["detail"] = f"{len(ref_dist)} 类占比逐项核对一致"
        except Exception as e:
            # 学习注释：False（不成立）；保存到out['fact_ok']。
            out["fact_ok"] = False
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
            out["detail"] = f"核对异常: {e}"
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：相似检索类样例：核对 top-1 的停电性质是否符合预期。
async def _check_similar(s: dict) -> dict:
    """相似检索类样例：核对 top-1 的停电性质是否符合预期。"""
    # 学习注释：similar_mod.search_similar的执行结果，await等待结果后再继续；保存到res。
    res = await similar_mod.search_similar(s["query"], top_k=1)
    # 学习注释：包含id、query的字典；保存到out。
    out = {"id": s["id"], "query": s["query"]}
    # 学习注释：只有res为空或为False才进入这一分支。
    if not res:
        # 学习注释：False（不成立）；保存到out['fact_ok']。
        out["fact_ok"] = False
        # 学习注释：'无检索结果'；保存到out['detail']。
        out["detail"] = "无检索结果"
        # 学习注释：返回out，结束本次函数调用。
        return out
    # 学习注释：从res[0]读取键'停电性质'，缺少时取None；保存到top_type。
    top_type = res[0].get("停电性质")
    # 学习注释：top_type等于s中键/位置为'expect_outage_type'的值；保存到out['fact_ok']。
    out["fact_ok"] = (top_type == s["expect_outage_type"])
    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
    out["detail"] = f"top1 停电性质={top_type}，期望={s['expect_outage_type']}"
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main() -> int:
    # 学习注释：把JSON文本解析为Python对象；保存到samples。
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    # 学习注释：遍历samples，只保留满足条件的项，将s收集为列表；保存到ask_samples。
    ask_samples = [s for s in samples if s["type"] == "ask"]
    # 学习注释：遍历samples，只保留满足条件的项，将s收集为列表；保存到sim_samples。
    sim_samples = [s for s in samples if s["type"] == "similar"]

    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)
    # 学习注释：在命令行输出本步骤结果或进度。
    print("停电台账问数评测：接口成功 / 流程执行成功 / 事实正确（独立参考对照）")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)

    # 学习注释：空列表；保存到结果列表（results）。
    results = []
    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n【问数类】")
    # 学习注释：遍历ask_samples，每轮把当前项交给s。
    for s in ask_samples:
        # 学习注释：调用_check_ask，处理s，await等待结果后再继续；保存到r。
        r = await _check_ask(s)
        # 学习注释：向结果列表（results）末尾添加r。
        results.append(r)
        # 学习注释：r['template_ok']为真/非空，并且r['status_ok']为真/非空，并且r['fact_ok']不是同一对象/空值False（不成立）时取'✅'，否则取'❌'；保存到mark。
        mark = "✅" if (r["template_ok"] and r["status_ok"] and r["fact_ok"] is not False) else "❌"
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"{mark} {s['id']} {s['query']!r}")
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"    模板={'✓' if r['template_ok'] else '✗'}({r['template']}) "
              f"状态={'✓' if r['status_ok'] else '✗'}({r['status']}) "
              f"事实={r['fact_ok']} {r.get('detail','')}")

    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n【相似检索类】")
    # 学习注释：遍历sim_samples，每轮把当前项交给s。
    for s in sim_samples:
        # 学习注释：调用_check_similar，处理s，await等待结果后再继续；保存到r。
        r = await _check_similar(s)
        # 学习注释：向结果列表（results）末尾添加r。
        results.append(r)
        # 学习注释：r['fact_ok']为真/非空时取'✅'，否则取'❌'；保存到mark。
        mark = "✅" if r["fact_ok"] else "❌"
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"{mark} {s['id']} {s['query']!r} -> {r['detail']}")

    # 汇总三个指标
    ask_ok = [r for r in results if r.get("template_ok") is not None]
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_template。
    n_template = sum(1 for r in ask_ok if r["template_ok"])
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_status。
    n_status = sum(1 for r in ask_ok if r["status_ok"])
    # 学习注释：遍历ask_ok，只保留满足条件的项，将r收集为列表；保存到n_fact_checked。
    n_fact_checked = [r for r in ask_ok if r.get("fact_ok") is not None]
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_fact。
    n_fact = sum(1 for r in n_fact_checked if r["fact_ok"])
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_sim。
    n_sim = sum(1 for r in results if r.get("id", "").startswith("sim_") and r["fact_ok"])

    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n" + "=" * 70)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"接口成功（意图识别正确）: {n_template}/{len(ask_ok)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"流程执行成功（状态符合预期）: {n_status}/{len(ask_ok)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"事实正确（独立参考对照）: {n_fact}/{len(n_fact_checked)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"相似检索正确: {n_sim}/{len(sim_samples)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)

    # 有任何 fail 返回 1
    all_ok = (n_template == len(ask_ok) and n_status == len(ask_ok)
              and n_fact == len(n_fact_checked) and n_sim == len(sim_samples))
    # 学习注释：返回all_ok为真/非空时取0，否则取1，结束本次函数调用。
    return 0 if all_ok else 1


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用sys.exit，处理启动事件循环，执行脚本的异步主函数。
    sys.exit(asyncio.run(main()))
