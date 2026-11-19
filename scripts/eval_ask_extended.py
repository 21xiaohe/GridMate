# -*- coding: utf-8 -*-
# 阅读导航：扩展统计评测
# 本文件作用：对组合条件、边界输入等扩展问法执行统计检查并保存报告。
# 建议读法：读check理解判分规则；再读main了解执行与落盘，避免只看通过数字。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：扩展评测（数字类，26 条，独立参考自动核对）

与 eval_ask.py 的区别：本脚本跑大批量数字类样例（ask_samples_extended.json）。

**标准答案来源（关键）**：样例文件里由生成器 `scripts/query/gen_eval_samples.py`
离线预存了 `ref_line_name` / `ref_supply_center` / `ref_start` / `ref_end` /
`ref_answer` / `ref_distribution`。评测器**只做比对**，不现场算参考值——
尤其不拿被测系统自己解析出的 `r['intent']` 参数去算参考值。否则模型把「线路A」
解析成「线路B」，双方按 B 算，错题也能判对。

三指标照旧分开：接口成功 / 流程执行成功 / 事实正确。

运行：PYTHONPATH=. python scripts/eval_ask_extended.py            # 用现有样例文件
      PYTHONPATH=. python scripts/eval_ask_extended.py --refresh  # 评测前从当前库重新生成样例

--refresh 的作用（解决「评测样例硬编码假名」问题）：
  样例文件里的 ref_line_name / ref_supply_center 是脱敏假名，假名由 PseudoMapper
  按遍历顺序分配，数据一旦重建就会重排，导致参考值失效。加 --refresh 后，评测前
  会先调 gen_eval_samples 从**当前库**重新生成样例与参考值，保证参考值永远和
  被测系统读的是同一份数据，假名重排不再影响评测结论。
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
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

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent / 'tests' / 'data' / 'ask_samples_extended.json'；保存到SAMPLES。
SAMPLES = Path(__file__).resolve().parent.parent / "tests" / "data" / "ask_samples_extended.json"



# 学习注释：异步函数check接收s；把这一段可复用处理封装起来。
async def check(s: dict) -> dict:
    # 学习注释：ask_mod.ask的执行结果，await等待结果后再继续；保存到r。
    r = await ask_mod.ask(s["query"])
    # 学习注释：包含id、query的字典；保存到out。
    out = {"id": s["id"], "query": s["query"]}

    # 学习注释：从r.get('intent') or {}读取键'template'，缺少时取None；保存到got_template。
    got_template = (r.get("intent") or {}).get("template")
    # 学习注释：got_template等于s中键/位置为'expect_template'的值；保存到out['template_ok']。
    out["template_ok"] = (got_template == s["expect_template"])
    # 学习注释：r.get('status')等于s中键/位置为'expect_status'的值；保存到out['status_ok']。
    out["status_ok"] = (r.get("status") == s["expect_status"])
    # 学习注释：got_template；保存到out['template']。
    out["template"] = got_template
    # 学习注释：从r读取键'status'，缺少时取None；保存到out['status']。
    out["status"] = r.get("status")

    # 学习注释：None（尚无值）；保存到out['fact_ok']。
    out["fact_ok"] = None
    # 学习注释：只有r.get('status')不等于'ok'才进入这一分支。
    if r.get("status") != "ok":
        # 学习注释：返回out，结束本次函数调用。
        return out

    # 学习注释：从r读取键'intent'，缺少时取空字典；保存到intent。
    intent = r.get("intent", {})
    # 学习注释：从intent读取键'template'，缺少时取None；保存到选中的SQL模板定义（tpl）。
    tpl = intent.get("template")

    # 关键修正：标准答案来自样例里**独立预存**的 ref_* 条件与 ref_answer，
    # 而不是拿被测系统自己解析出的 intent 参数去现场算参考值。
    # 否则模型把「线路A」解析成「线路B」，双方按 B 算，错题也能判对。
    try:
        # 学习注释：只有选中的SQL模板定义（tpl）等于'line_outage_count'才进入这一分支。
        if tpl == "line_outage_count":
            # ① 参数校验：解析出的线路必须等于预期线路
            if intent.get("line_name") != s.get("ref_line_name"):
                # 学习注释：False（不成立）；保存到out['fact_ok']。
                out["fact_ok"] = False
                # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                out["detail"] = f"解析线路={intent.get('line_name')}，预期={s.get('ref_line_name')}（参数错）"
                # 学习注释：返回out，结束本次函数调用。
                return out
            # 学习注释：r['result']['rows'][0]中键/位置为'停电次数'的值；保存到got。
            got = r["result"]["rows"][0]["停电次数"]
            # 学习注释：s中键/位置为'ref_answer'的值；保存到ref。
            ref = s["ref_answer"]
            # 学习注释：got等于ref；保存到out['fact_ok']。
            out["fact_ok"] = (got == ref)
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
            out["detail"] = f"线路{s['ref_line_name']}: 系统={got}, 预期={ref}"

        # 学习注释：只有选中的SQL模板定义（tpl）等于'supply_center_energy'才进入这一分支。
        elif tpl == "supply_center_energy":
            # 学习注释：只有intent.get('supply_center')不等于从s读取键'ref_supply_center'，缺少时取None才进入这一分支。
            if intent.get("supply_center") != s.get("ref_supply_center"):
                # 学习注释：False（不成立）；保存到out['fact_ok']。
                out["fact_ok"] = False
                # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                out["detail"] = f"解析供服中心={intent.get('supply_center')}，预期={s.get('ref_supply_center')}（参数错）"
                # 学习注释：返回out，结束本次函数调用。
                return out
            # 学习注释：调用round，处理r['result']['rows'][0]中键/位置为'缺供电量总和'的值；保存到got。
            got = round(r["result"]["rows"][0]["缺供电量总和"], 2)
            # 学习注释：调用round，处理s中键/位置为'ref_answer'的值；保存到ref。
            ref = round(s["ref_answer"], 2)
            # 学习注释：abs(got - ref)小于0.01；保存到out['fact_ok']。
            out["fact_ok"] = (abs(got - ref) < 0.01)
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
            out["detail"] = f"供服中心{s['ref_supply_center']}: 系统={got}, 预期={ref}"

        # 学习注释：只有选中的SQL模板定义（tpl）等于'time_range_outage_count'才进入这一分支。
        elif tpl == "time_range_outage_count":
            # 学习注释：只有intent.get('start_time')不等于从s读取键'ref_start'，缺少时取None，或者intent.get('end_time')不等于从s读取键'ref_end'，缺少时取None才进入这一分支。
            if intent.get("start_time") != s.get("ref_start") or intent.get("end_time") != s.get("ref_end"):
                # 学习注释：False（不成立）；保存到out['fact_ok']。
                out["fact_ok"] = False
                # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                out["detail"] = (f"解析区间=[{intent.get('start_time')},{intent.get('end_time')})，"
                                 f"预期=[{s.get('ref_start')},{s.get('ref_end')})（边界错）")
                # 学习注释：返回out，结束本次函数调用。
                return out
            # 学习注释：r['result']['rows'][0]中键/位置为'停电次数'的值；保存到got。
            got = r["result"]["rows"][0]["停电次数"]
            # 学习注释：s中键/位置为'ref_answer'的值；保存到ref。
            ref = s["ref_answer"]
            # 学习注释：got等于ref；保存到out['fact_ok']。
            out["fact_ok"] = (got == ref)
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
            out["detail"] = f"[{s['ref_start']},{s['ref_end']}): 系统={got}, 预期={ref}"

        # 学习注释：只有选中的SQL模板定义（tpl）等于'outage_type_ratio'才进入这一分支。
        elif tpl == "outage_type_ratio":
            # ② 占比逐类比较：不只比种类数，要比每类的次数与百分比
            got_rows = r["result"]["rows"]
            # 学习注释：从s读取键'ref_distribution'，缺少时取空列表；保存到ref_dist。
            ref_dist = s.get("ref_distribution", [])
            # 学习注释：只有len(got_rows)不等于计算ref_dist的元素数量才进入这一分支。
            if len(got_rows) != len(ref_dist):
                # 学习注释：False（不成立）；保存到out['fact_ok']。
                out["fact_ok"] = False
                # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                out["detail"] = f"种类数不符: 系统={len(got_rows)}, 预期={len(ref_dist)}"
                # 学习注释：返回out，结束本次函数调用。
                return out
            # 按停电性质对齐后逐类比次数和占比
            got_map = {row["停电性质"]: row for row in got_rows}
            # 学习注释：遍历ref_dist，每轮把当前项交给ref_row。
            for ref_row in ref_dist:
                # 学习注释：ref_row中键/位置为'停电性质'的值；保存到name。
                name = ref_row["停电性质"]
                # 学习注释：从got_map读取键name，缺少时取None；保存到g。
                g = got_map.get(name)
                # 学习注释：只有g尚未设置（None）才进入这一分支。
                if g is None:
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = f"缺少性质「{name}」"
                    # 学习注释：返回out，结束本次函数调用。
                    return out
                # 学习注释：只有g['停电次数']不等于ref_row中键/位置为'停电次数'的值，或者abs(g['占比_pct'] - ref_row['占比_pct'])大于0.01才进入这一分支。
                if g["停电次数"] != ref_row["停电次数"] or abs(g["占比_pct"] - ref_row["占比_pct"]) > 0.01:
                    # 学习注释：False（不成立）；保存到out['fact_ok']。
                    out["fact_ok"] = False
                    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到out['detail']。
                    out["detail"] = (f"性质「{name}」不符: 系统={g['停电次数']}次/{g['占比_pct']}%, "
                                     f"预期={ref_row['停电次数']}次/{ref_row['占比_pct']}%")
                    # 学习注释：返回out，结束本次函数调用。
                    return out
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


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main(refresh: bool = False) -> int:
    # 学习注释：只有refresh为真/非空才进入这一分支。
    if refresh:
        # 评测前从当前库重新生成样例（参考值跟着数据走，假名重排不失效）
        sys.path.insert(0, str(Path(__file__).resolve().parent / "query"))
        # 学习注释：从gen_eval_samples导入generate_samples，复用对应模块的实现。
        from gen_eval_samples import generate_samples
        # 学习注释：调用generate_samples；保存到samples。
        samples = generate_samples()
        # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
        SAMPLES.parent.mkdir(parents=True, exist_ok=True)
        # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
        SAMPLES.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"[refresh] 已从当前库重新生成 {len(samples)} 条样例")
    else:
        # 学习注释：把JSON文本解析为Python对象；保存到samples。
        samples = json.loads(SAMPLES.read_text(encoding="utf-8"))

    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"停电台账问数扩展评测（数字类 {len(samples)} 条，独立参考自动核对）")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)

    # 学习注释：空列表；保存到结果列表（results）。
    results = []
    # 学习注释：空列表；保存到fails。
    fails = []
    # 学习注释：遍历samples，每轮把当前项交给s。
    for s in samples:
        # 学习注释：调用check，处理s，await等待结果后再继续；保存到r。
        r = await check(s)
        # 学习注释：向结果列表（results）末尾添加r。
        results.append(r)
        # 学习注释：按and短路取第一个假值；都为真则取最后一个值（r['template_ok'] and r['status_ok'] and (r['fact_ok'] is not False)）；保存到ok。
        ok = r["template_ok"] and r["status_ok"] and r["fact_ok"] is not False
        # 学习注释：只有ok为空或为False才进入这一分支。
        if not ok:
            # 学习注释：向fails末尾添加r。
            fails.append(r)
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"❌ {r['id']} {r['query']!r}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"    模板={'✓' if r['template_ok'] else '✗'}({r['template']}) "
                  f"状态={'✓' if r['status_ok'] else '✗'}({r['status']}) "
                  f"事实={r['fact_ok']} {r.get('detail','')}")

    # 汇总
    n_template = sum(1 for r in results if r["template_ok"])
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_status。
    n_status = sum(1 for r in results if r["status_ok"])
    # 学习注释：遍历结果列表（results），只保留满足条件的项，将r收集为列表；保存到checked。
    checked = [r for r in results if r.get("fact_ok") is not None]
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到n_fact。
    n_fact = sum(1 for r in checked if r["fact_ok"])

    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n" + "=" * 70)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"接口成功: {n_template}/{len(results)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"流程执行成功: {n_status}/{len(results)}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"事实正确: {n_fact}/{len(checked)}  (独立参考对照)")
    # 学习注释：只有fails为真/非空才进入这一分支。
    if fails:
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"\n失败 {len(fails)} 条（详见上方 ❌ 行）")
    else:
        # 学习注释：在命令行输出本步骤结果或进度。
        print("\n✅ 全部通过")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("=" * 70)

    # 学习注释：返回fails为真/非空时取1，否则取0，结束本次函数调用。
    return 1 if fails else 0


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser()
    # 学习注释：调用ap.add_argument，处理'--refresh'。
    ap.add_argument("--refresh", action="store_true",
                    help="评测前从当前库重新生成样例（数据重建后建议加，避免假名重排导致参考值失效）")
    # 学习注释：调用ap.parse_args；保存到args。
    args = ap.parse_args()
    # 学习注释：调用sys.exit，处理启动事件循环，执行脚本的异步主函数。
    sys.exit(asyncio.run(main(refresh=args.refresh)))
