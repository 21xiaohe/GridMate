# -*- coding: utf-8 -*-
# 阅读导航：统计计算内核
# 本文件作用：这里的SQL由开发者固定编写；模型只提供参数。事件数去重，缺供电量按用户行求和。
# 建议读法：TEMPLATES保存四种计算方式；execute_query只拼接白名单条件，并通过占位符传入参数。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：白名单 SQL 模板 + 参数映射（参数化 SQL 内核）

设计原则（防 JOIN 放大 + 口径正确）：
  - LLM/调用方只产出「有限意图参数」，不产 SQL。
  - 每个模板是写死的白名单 SQL，确定性代码按参数拼 WHERE/聚合。
  - 度量字段严格按统计粒度聚合：
      * 时户数/缺供电量/停电容量 = 用户级 → 只在 users 表聚合
      * 停电户数 = 线段级 → 只在 segments 表聚合
      * 停电性质/时间/事件序号 = 事件级 → 去重后取一
  - 绝不跨表 join 后再对用户级/线段级度量求和。

已支持模板：
  T1 某线路停电次数（事件去重计数）
  T2 某供服中心缺供电量总和（用户级度量，users 表内 SUM）
  T3 某时间段停电事件数（时间范围过滤）
  T4 某类停电占比（停电性质分类计数）

可选过滤（组合条件）：
  每个模板通过 optional_filters 声明额外支持的过滤维度，白名单内组合、
  值走 SQL 占位符（不拼接），未声明的过滤一律拒绝——杜绝「线路+时间」这种
  组合被静默忽略、只查了线路的错误。

用法：
    from app.core.query_templates import execute_query
    execute_query(template_id, params, filters=None) -> dict
"""
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning


# ============================================================
# 白名单 SQL 模板
# 每个模板：
#   sql             带 :param 占位符的基础 SQL（不含可选过滤）
#   desc            描述
#   metric_granularity  度量口径
#   optional_filters 可选过滤维度 -> {where: SQL 片段模板(含 :param), 说明}
#                    过滤片段会在 WHERE 后以 AND 追加；仅支持单表 users 内的
#                    确定性过滤（不 join），保证组合后口径仍正确。
# ============================================================

# 学习注释：包含line_outage_count、supply_center_energy、time_range_outage_count、outage_type_ratio的字典；保存到TEMPLATES。
TEMPLATES = {
    # T1：某线路停电次数（事件去重计数——线路名 -> 涉及的事件序号 distinct 计数）
    # 学习注释：模板T1：在users中按线路筛选后COUNT DISTINCT事件ID，多条用户记录只算一次事件。
    "line_outage_count": {
        "sql": """
            SELECT COUNT(DISTINCT 事件序号) AS 停电次数
            FROM users
            WHERE 线路名称 = :line_name
        """,
        "desc": "某线路停电次数（按事件去重）",
        "metric_granularity": "事件级（COUNT DISTINCT 事件序号）",
        "optional_filters": {
            # 「线路1 今年6月停了几次电」：线路 + 时间组合
            "time_range": {
                "where": "AND 停电起始时间 >= :start_time AND 停电起始时间 < :end_time",
                "params": ("start_time", "end_time"),
                "desc": "时间范围过滤",
            },
        },
    },

    # T2：某供服中心缺供电量总和（用户级度量，只在 users 表 SUM，绝不 join segments）
    # 学习注释：模板T2：按供服中心筛选后直接SUM用户行缺供电量；不能先按事件随意删掉用户行。
    "supply_center_energy": {
        "sql": """
            SELECT COALESCE(SUM(缺供电量), 0) AS 缺供电量总和
            FROM users
            WHERE 供服中心 = :supply_center
        """,
        "desc": "某供服中心缺供电量总和",
        "metric_granularity": "用户级（users 表内 SUM，不 join segments 防放大）",
        "optional_filters": {
            "time_range": {
                "where": "AND 停电起始时间 >= :start_time AND 停电起始时间 < :end_time",
                "params": ("start_time", "end_time"),
                "desc": "时间范围过滤",
            },
        },
    },

    # T3：某时间段停电事件数（时间范围过滤，事件去重）
    # 学习注释：模板T3：日期左闭右开，再按事件ID去重计数，可追加允许的线路/中心条件。
    "time_range_outage_count": {
        "sql": """
            SELECT COUNT(DISTINCT 事件序号) AS 停电次数
            FROM users
            WHERE 停电起始时间 >= :start_time
              AND 停电起始时间 <  :end_time
        """,
        "desc": "某时间段停电事件数",
        "metric_granularity": "事件级（COUNT DISTINCT 事件序号）",
        "optional_filters": {
            "line": {
                "where": "AND 线路名称 = :line_name",
                "params": ("line_name",),
                "desc": "线路过滤",
            },
            "supply_center": {
                "where": "AND 供服中心 = :supply_center",
                "params": ("supply_center",),
                "desc": "供服中心过滤",
            },
        },
    },

    # T4：某类停电占比（按停电性质分类计数）
    # 口径说明（关键）：停电性质是「多标签」维度——一个事件可同时属多个性质
    # （如既是「内部故障」又是「计划检修」）。因此 GROUP BY 停电性质各自去重计数后，
    # 各类「停电次数」之和会 > 事件总数，占比之和也会 > 100%，这是多标签的正常现象，
    # 不是 bug。结果里用 multi_label_events 明确给出「跨多个性质的事件数」，
    # 让回答能如实说明「占比为多标签口径、各类可重叠」，而不是让用户误以为是互斥分布。
    # 学习注释：模板T4：每个性质分别去重计数，除以全量事件数；多标签重叠时占比之和可能超过100%。
    "outage_type_ratio": {
        "sql": """
            SELECT 停电性质,
                   COUNT(DISTINCT 事件序号) AS 停电次数,
                   ROUND(COUNT(DISTINCT 事件序号) * 100.0 / :total_events, 2) AS 占比_pct
            FROM users
            WHERE 停电性质 != ''
            GROUP BY 停电性质
            ORDER BY 停电次数 DESC
        """,
        "desc": "各类停电性质占比（多标签口径，各类可重叠）",
        "metric_granularity": "事件级（按停电性质分类，事件去重计数；多标签事件计入多个性质）",
        "optional_filters": {},
    },
}


# 学习注释：打开当前数据快照的只读SQLite连接；调用方负责关闭。
def get_conn():
    # 学习注释：返回调用versioning.current_snapshot().connect，结束本次函数调用。
    return versioning.current_snapshot().connect()


# 学习注释：选择固定模板，仅追加允许的条件，再用参数绑定执行SQLite查询。
def execute_query(template_id, params, filters=None):
    """执行白名单 SQL 模板。

    参数:
        template_id: TEMPLATES 的 key（如 'line_outage_count'）
        params: dict，模板所需参数（必填项）
        filters: dict，可选过滤条件，形如 {"time_range": {"start_time": ..., "end_time": ...}}
            每个 key 必须是该模板 optional_filters 里声明的；未声明的 key 抛 ValueError。

    返回:
        dict: {模板, 描述, 粒度, 列名列表, 行数据}
    """
    # 学习注释：只有SQL模板标识（template_id）不包含在TEMPLATES才进入这一分支。
    if template_id not in TEMPLATES:
        # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
        raise ValueError(f"未知模板: {template_id}，可用模板: {list(TEMPLATES.keys())}")

    # 学习注释：从固定字典中取SQL及允许的过滤字段；用户不能提供任意SQL模板。
    tpl = TEMPLATES[template_id]
    # 学习注释：tpl中键/位置为'sql'的值；保存到待执行的SQL文本（sql）。
    sql = tpl["sql"]
    # 学习注释：复制主参数，后续合并附加条件，不修改调用方传入的原字典。
    merged_params = dict(params or {})

    # 拼接可选过滤（白名单内，值走占位符）
    filters = filters or {}
    # 学习注释：遍历读取filters的键和值配对，每轮把当前项交给fkey、fval。
    for fkey, fval in filters.items():
        # 学习注释：只有fkey不包含在从tpl读取键'optional_filters'，缺少时取空字典才进入这一分支。
        if fkey not in tpl.get("optional_filters", {}):
            # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
            raise ValueError(
                f"模板 {template_id} 不支持过滤条件 {fkey}，"
                f"支持: {list(tpl.get('optional_filters', {}).keys())}")
        # 学习注释：tpl['optional_filters']中键/位置为fkey的值；保存到fspec。
        fspec = tpl["optional_filters"][fkey]
        # 学习注释：只把开发者预先定义的where片段追加到SQL；具体名称和日期仍放在参数字典。
        sql = sql.rstrip() + "\n            " + fspec["where"]
        # 学习注释：遍历fspec中键/位置为'params'的值，每轮把当前项交给p。
        for p in fspec["params"]:
            # 学习注释：按模板声明的参数名取值；之后由数据库占位符绑定，避免把值拼成SQL语法。
            merged_params[p] = fval[p]

    # 学习注释：get_conn的执行结果；保存到数据库连接（conn）。
    conn = get_conn()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
        cur = conn.cursor()
        # 学习注释：执行参数化SQL：sql是固定结构，merged_params提供线路、日期等值。
        cur.execute(sql, merged_params)
        # 学习注释：把sqlite3.Row转为普通字典，后续可以通过中文列名读取，也便于JSON返回。
        rows = [dict(r) for r in cur.fetchall()]
        # 学习注释：从游标元数据取得输出列名；如果查询没有列描述就使用空列表。
        cols = [d[0] for d in cur.description] if cur.description else []
        # 学习注释：返回包含template、desc、granularity、columns、rows的字典，结束本次函数调用。
        return {
            "template": template_id,
            "desc": tpl["desc"],
            "granularity": tpl["metric_granularity"],
            "columns": cols,
            "rows": rows,
        }
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 自测：跑几个典型问数，验证结果
    print("=== 问数内核自测 ===\n")

    # 先取一个真实存在的线路名和供服中心
    conn = get_conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT 线路名称 FROM users LIMIT 1")
    # 学习注释：cur.fetchone()中键/位置为'线路名称'的值；保存到sample_line。
    sample_line = cur.fetchone()["线路名称"]
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT 供服中心 FROM users WHERE 供服中心 != '' LIMIT 1")
    # 学习注释：cur.fetchone()中键/位置为'供服中心'的值；保存到sample_supply。
    sample_supply = cur.fetchone()["供服中心"]
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"样本线路名={sample_line}, 样本供服中心={sample_supply}\n")

    # T1 某线路停电次数
    r1 = execute_query("line_outage_count", {"line_name": sample_line})
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"[T1] {r1['desc']} (线路={sample_line})")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    粒度={r1['granularity']}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    结果={r1['rows']}\n")

    # T2 某供服中心缺供电量
    r2 = execute_query("supply_center_energy", {"supply_center": sample_supply})
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"[T2] {r2['desc']} (供服中心={sample_supply})")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    粒度={r2['granularity']}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    结果={r2['rows']}\n")

    # T3 时间段
    r3 = execute_query("time_range_outage_count", {"start_time": "2025-11-01", "end_time": "2025-12-01"})
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"[T3] {r3['desc']} (2025-11)")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    粒度={r3['granularity']}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    结果={r3['rows']}\n")

    # T4 停电性质占比（先取总事件数）
    conn = get_conn()
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("SELECT COUNT(DISTINCT 事件序号) FROM users")
    # 学习注释：cur.fetchone()中键/位置为0的值；保存到total。
    total = cur.fetchone()[0]
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：execute_query的执行结果；保存到r4。
    r4 = execute_query("outage_type_ratio", {"total_events": total})
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"[T4] {r4['desc']} (总事件={total})")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"    粒度={r4['granularity']}")
    # 学习注释：遍历r4['rows']中键/位置为:5的值，每轮把当前项交给row。
    for row in r4["rows"][:5]:
        # 学习注释：在命令行输出本步骤结果或进度。
        print(f"    {row}")
