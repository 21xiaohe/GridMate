# -*- coding: utf-8 -*-
# 阅读导航：统计参考题生成
# 本文件作用：从当前数据库构造问题，并独立计算参考数字，供统计评测使用。
# 建议读法：生成文件可能包含业务实体与数字，只留本地；参考答案与当前数据版本有关。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：批量生成可自动核对的评测样例（数字类）

数字类问法（线路停电次数 / 供服中心缺供电量 / 时间段事件数 / 停电性质占比）
的标准答案可以用独立参考查询自动算，因此可以批量生成大量样例，自动核对。

与相似检索类（需人工标注"是否真相似"）分开——本脚本只生成数字类。

产出：tests/data/ask_samples_extended.json（含独立参考标准答案）
"""
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入random：可控抽样。
import random
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'tests' / 'data' / 'ask_samples_extended.json'；保存到OUT_PATH。
OUT_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "data" / "ask_samples_extended.json"

# 学习注释：调用random.seed，处理42。
random.seed(42)  # 可复现


# 学习注释：函数_conn接收调用时的上下文；把这一段可复用处理封装起来。
def _conn():
    # 学习注释：返回调用versioning.current_snapshot().connect，结束本次函数调用。
    return versioning.current_snapshot().connect()


# 学习注释：线路停电次数：抽 n 条真实线路，独立参考算停电次数。
def gen_line_samples(n=8) -> list[dict]:
    """线路停电次数：抽 n 条真实线路，独立参考算停电次数。"""
    # 学习注释：调用_conn；保存到数据库连接（conn）。
    conn = _conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT 线路名称, COUNT(DISTINCT 事件序号) cnt FROM users "
                "WHERE 线路名称 != '' GROUP BY 线路名称 ORDER BY cnt DESC")
    # 学习注释：读取SQL剩余全部结果行；保存到查询或表格行（rows）。
    rows = cur.fetchall()
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 过滤掉「对模型不友好的假名」——含连续 9 的名字（如 线路999/线路9999）
    # 会被模型误判成占位值/越界测试值，导致 low_confidence。评测要测的是正常
    # 问数能力，不是模型对迷惑性名字的鲁棒性（那是另一个独立维度）。
    # 学习注释：函数_friendly接收r；把这一段可复用处理封装起来。
    def _friendly(r):
        # 学习注释：r中键/位置为'线路名称'的值；保存到name。
        name = r["线路名称"]
        # 学习注释：返回按and短路取第一个假值；都为真则取最后一个值（'999' not in name and '99' not in name），结束本次函数调用。
        return "999" not in name and "99" not in name
    # 学习注释：遍历查询或表格行（rows），只保留满足条件的项，将r收集为列表；保存到查询或表格行（rows）。
    rows = [r for r in rows if _friendly(r)]
    # 学习注释：只有len(rows)小于n才进入这一分支。
    if len(rows) < n:
        # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
        raise RuntimeError(f"过滤后线路不足 {n} 条（现 {len(rows)}），抽样失败")
    # 选覆盖不同量级的线路（高/中/低次数），让样例有区分度
    picks = [rows[0], rows[len(rows)//2], rows[-1]] + \
            random.sample(rows[1:len(rows)-1], n - 3)
    # 学习注释：空列表；保存到out。
    out = []
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、r。
    for i, r in enumerate(picks):
        # 学习注释：向out末尾添加包含id、type、query、expect_template、expect_status、ref_line_name的字典。
        out.append({
            "id": f"ext_line_{i:02d}",
            "type": "ask",
            "query": f"{r['线路名称']} 停了几次电",
            "expect_template": "line_outage_count",
            "expect_status": "ok",
            "ref_line_name": r["线路名称"],
            "ref_answer": r["cnt"],
        })
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：供服中心缺供电量：抽 n 个供服中心，Python 累加算标准答案。
def gen_supply_samples(n=8) -> list[dict]:
    """供服中心缺供电量：抽 n 个供服中心，Python 累加算标准答案。"""
    # 学习注释：调用_conn；保存到数据库连接（conn）。
    conn = _conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT 供服中心, SUM(缺供电量) s FROM users "
                "WHERE 供服中心 != '' GROUP BY 供服中心 ORDER BY s DESC")
    # 学习注释：读取SQL剩余全部结果行；保存到查询或表格行（rows）。
    rows = cur.fetchall()
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：计算 [rows[0], rows[len(rows) // 2], rows[-1]] + random.sample(rows[1:len(rows) - 1], n - 3)；保存到picks。
    picks = [rows[0], rows[len(rows)//2], rows[-1]] + \
            random.sample(rows[1:len(rows)-1], n - 3)
    # 学习注释：空列表；保存到out。
    out = []
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、r。
    for i, r in enumerate(picks):
        # 学习注释：向out末尾添加包含id、type、query、expect_template、expect_status、ref_supply_center的字典。
        out.append({
            "id": f"ext_supply_{i:02d}",
            "type": "ask",
            "query": f"{r['供服中心']} 的缺供电量是多少",
            "expect_template": "supply_center_energy",
            "expect_status": "ok",
            "ref_supply_center": r["供服中心"],
            "ref_answer": round(r["s"], 2),
        })
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：时间段事件数：抽 n 个月，独立参考算当月停电次数。
def gen_time_samples(n=8) -> list[dict]:
    """时间段事件数：抽 n 个月，独立参考算当月停电次数。"""
    # 学习注释：调用_conn；保存到数据库连接（conn）。
    conn = _conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT substr(停电起始时间,1,7) ym, COUNT(DISTINCT 事件序号) cnt "
                "FROM users WHERE 停电起始时间 != '' GROUP BY ym ORDER BY ym")
    # 学习注释：读取SQL剩余全部结果行；保存到查询或表格行（rows）。
    rows = cur.fetchall()
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：调用random.sample，处理查询或表格行（rows）；保存到picks。
    picks = random.sample(rows, min(n, len(rows)))
    # 学习注释：空列表；保存到out。
    out = []
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、r。
    for i, r in enumerate(picks):
        # 学习注释：r中键/位置为'ym'的值；保存到ym。
        ym = r["ym"]
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到start。
        start = f"{ym}-01"
        # 月末（用下月1号作半开区间上界，避免算每月天数）
        y, m = map(int, ym.split("-"))
        # 学习注释：只有m等于12才进入这一分支。
        if m == 12:
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到end。
            end = f"{y+1}-01-01"
        else:
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到end。
            end = f"{y}-{m+1:02d}-01"
        # 学习注释：向out末尾添加包含id、type、query、expect_template、expect_status、ref_start的字典。
        out.append({
            "id": f"ext_time_{i:02d}",
            "type": "ask",
            "query": f"{y}年{m}月停了多少次电",
            "expect_template": "time_range_outage_count",
            "expect_status": "ok",
            "ref_start": start,
            "ref_end": end,
            "ref_answer": r["cnt"],
        })
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：停电性质占比：换不同问法，独立参考算逐类次数与百分比（不只种类数）。
def gen_type_samples(n=2) -> list[dict]:
    """停电性质占比：换不同问法，独立参考算逐类次数与百分比（不只种类数）。"""
    # 学习注释：调用_conn；保存到数据库连接（conn）。
    conn = _conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT COUNT(DISTINCT 事件序号) FROM users")
    # 学习注释：cur.fetchone()中键/位置为0的值；保存到去重后的事件总数（total_events）。
    total_events = cur.fetchone()[0]
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT 停电性质, COUNT(DISTINCT 事件序号) cnt FROM users "
                "WHERE 停电性质 != '' GROUP BY 停电性质 ORDER BY cnt DESC")
    # 学习注释：读取SQL剩余全部结果行；保存到查询或表格行（rows）。
    rows = cur.fetchall()
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：遍历查询或表格行（rows），将{'停电性质': r['停电性质'], '停电次数': r['cnt'], '占比_pct': round(r['cnt'] * 100.…收集为列表；保存到ref_distribution。
    ref_distribution = [
        {"停电性质": r["停电性质"],
         "停电次数": r["cnt"],
         "占比_pct": round(r["cnt"] * 100.0 / total_events, 2)}
        for r in rows
    ]
    # 学习注释：3项列表：'停电性质都有哪些、各占多少'、'各种停电类型占比如何'、'停电类型分布'；保存到queries。
    queries = ["停电性质都有哪些、各占多少", "各种停电类型占比如何", "停电类型分布"]
    # 学习注释：空列表；保存到out。
    out = []
    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给i。
    for i in range(min(n, len(queries))):
        # 学习注释：向out末尾添加包含id、type、query、expect_template、expect_status、ref_answer的字典。
        out.append({
            "id": f"ext_type_{i:02d}",
            "type": "ask",
            "query": queries[i],
            "expect_template": "outage_type_ratio",
            "expect_status": "ok",
            "ref_answer": len(rows),
            "ref_distribution": ref_distribution,
        })
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：从当前库生成全部数字类样例（含独立参考值）。
def generate_samples() -> list[dict]:
    """从当前库生成全部数字类样例（含独立参考值）。

    独立可复用：既可供命令行单独跑，也可供评测脚本在跑之前调用——
    这样样例的参考值永远和当前数据一致，数据重建导致假名重排时，
    参考值跟着重算，不再需要手工同步（见二次审阅「评测样例硬编码假名」教训）。
    """
    # 学习注释：空列表；保存到samples。
    samples = []
    # 学习注释：用调用gen_line_samples，处理8更新samples；下方+=等写法把计算结果存回原变量。
    samples += gen_line_samples(8)
    # 学习注释：用调用gen_supply_samples，处理8更新samples；下方+=等写法把计算结果存回原变量。
    samples += gen_supply_samples(8)
    # 学习注释：用调用gen_time_samples，处理8更新samples；下方+=等写法把计算结果存回原变量。
    samples += gen_time_samples(8)
    # 学习注释：用调用gen_type_samples，处理2更新samples；下方+=等写法把计算结果存回原变量。
    samples += gen_type_samples(2)
    # 学习注释：返回samples，结束本次函数调用。
    return samples


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main():
    # 学习注释：调用generate_samples；保存到samples。
    samples = generate_samples()
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    OUT_PATH.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"生成 {len(samples)} 条数字类样例 -> {OUT_PATH}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  线路问数 {sum(1 for s in samples if s['id'].startswith('ext_line'))} 条")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  供服中心 {sum(1 for s in samples if s['id'].startswith('ext_supply'))} 条")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  时间段 {sum(1 for s in samples if s['id'].startswith('ext_time'))} 条")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  停电性质 {sum(1 for s in samples if s['id'].startswith('ext_type'))} 条")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用main。
    main()
