# -*- coding: utf-8 -*-
# 阅读导航：统计问题解析
# 本文件作用：输入自然语言，模型提取受限参数，程序校验参数后执行白名单SQL，输出数字与统计依据。
# 建议读法：阅读顺序：_IntentParams → ask → _validate → _to_params → query_templates.execute_query。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：下方原有文档中的倍数和“几乎必然”等描述来自历史笔记，本轮没有重新验证这些数字，不应背成当前项目成绩。
# 当前保证边界：固定SQL减少自由生成SQL的风险，但模板、参数识别或原始数据出错仍可能导致错误数字；不能理解成统计绝不会错。

"""停电台账问数：自然语言 → 意图参数 → 白名单 SQL 模板（LLM 意图识别层）

这是「参数化 SQL 内核」之上的意图识别层。设计目标与边界：

**为什么不让 LLM 直接生成 SQL，而要它只吐「受限意图参数」？**
  这是本项目对「跨表重复统计 + 指标口径歧义」这个核心痛点的解法。
  台账是三类粒度的记录（事件级 / 用户级 / 线段级），一条事件会被拆成 3.5 个用户、
  2 个线段（实测），谁要是一手 join 再 SUM，缺供电量就虚高 5.36 倍。让 LLM 写 SQL，
  等于把口径风险交给一个每次输出都有随机性的模型——它几乎必然踩这个坑。
  所以：**口径和 SQL 由确定性代码写死（白名单模板），LLM 只负责「听懂人话」，
  产出有限枚举的意图参数**。模型错，最多是参数错（查错线路、时间写反），
  不会错在「数字本身是虚高的」。

**LLM 输出的是受限枚举，不是自由文本：**
  - template 只能是 4 个白名单模板之一（Literal 枚举）
  - line_name / supply_center 必须来自词典（数据里真实存在的假名）
  - 时间只接受 YYYY-MM-DD，且 start < end

**置信度闸：**
  模型判断把握低于阈值时（默认 0.6），不在「猜错口径」上硬答，
  而是把候选意图和拿不准的点如实回给用户，让用户确认——问数场景下
  答错一个数比答「我不确定」危害大得多。

依赖：
  - app/core/query_templates.py：白名单 SQL 模板（确定性内核）
  - app/core/llm.py：结构化输出（function calling，避开 json_schema 的思考正文坑）
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入logging：分级日志，便于排查运行失败。
import logging
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re

# 学习注释：从pydantic引入BaseModel, Field，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, Field
# 学习注释：从typing引入Literal，用于类型提示，不负责网络调用或业务计算。
from typing import Literal

# 学习注释：从app.core导入llm，复用对应模块的实现。
from app.core import llm
# 学习注释：从app.core导入evidence并简称evidence_mod，复用对应模块的实现。
from app.core import evidence as evidence_mod
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning
# 学习注释：从app.core.query_templates导入get_conn, execute_query, TEMPLATES，复用对应模块的实现。
from app.core.query_templates import get_conn, execute_query, TEMPLATES

# 学习注释：创建以当前模块名标识的日志记录器；保存到logger。
logger = logging.getLogger(__name__)

# 模板 id 只允许这四个（Literal 枚举，LLM 吐别的就直接报错）
_TEMPLATE_IDS = Literal["line_outage_count", "supply_center_energy",
                        "time_range_outage_count", "outage_type_ratio"]

# 模板到所需参数的映射（每类模板的参数名 + 说明，供 prompt 和校验共用）
_TEMPLATE_PARAMS = {
    "line_outage_count": {"line_name": "线路名称（假名，如「线路1」）"},
    "supply_center_energy": {"supply_center": "供服中心（假名，如「供服中心1」）"},
    "time_range_outage_count": {"start_time": "起始时间 YYYY-MM-DD", "end_time": "结束时间 YYYY-MM-DD"},
    "outage_type_ratio": {},  # 无额外参数，total_events 由代码算好填入
}

# 可选供服中心 / 线路名的合法取值（从库里动态加载，模型吐库外值直接拦）
# 用缓存避免每次都查库。


# 学习注释：_IntentParams定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class _IntentParams(BaseModel):
    """LLM 要吐出的结构化意图参数。全部受限，杜绝自由发挥。

    四个「指标」模板 + 四个「过滤」维度分开建模：
      - template 决定算什么（指标）
      - line_name / supply_center / start_time / end_time 是可选过滤，
        可以在模板允许的范围内叠加（如 line_outage_count + 时间过滤）。
        但叠加必须是白名单模板 optional_filters 里声明的组合，
        未声明的组合（如占比 + 线路）会被 _validate 拒绝，不静默忽略。
    """
    # 学习注释：声明字段：4 个白名单模板之一；保存到template。
    template: _TEMPLATE_IDS = Field(description="4 个白名单模板之一")
    # 学习注释：声明字段：线路名称过滤（可选，若问句提到某线路就填）；保存到line_name。
    line_name: str | None = Field(default=None, description="线路名称过滤（可选，若问句提到某线路就填）")
    # 学习注释：声明字段：供服中心过滤（可选，若问句提到某供服中心就填）；保存到supply_center。
    supply_center: str | None = Field(default=None, description="供服中心过滤（可选，若问句提到某供服中心就填）")
    # 学习注释：声明字段：起始时间 YYYY-MM-DD（可选，若问句提到时间就填）；保存到start_time。
    start_time: str | None = Field(default=None, description="起始时间 YYYY-MM-DD（可选，若问句提到时间就填）")
    # 学习注释：声明字段：结束时间 YYYY-MM-DD（可选，左闭右开，填范围结束次日）；保存到end_time。
    end_time: str | None = Field(default=None, description="结束时间 YYYY-MM-DD（可选，左闭右开，填范围结束次日）")
    # 学习注释：模型自报把握值，约束在0～1；它不是经过统计校准的正确率。
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="判断把握 0-1")


# 词典缓存（懒加载，首次问数才查库）。
# 缓存绑定数据库文件版本（mtime）：建库脚本在另一进程用 os.replace 换掉 gridmate.db 后，
# 文件 mtime 变化，下一次 _load_dict 会检测到并重载，而不是一直复用旧词典/旧分母。
# （三次审阅问题 4：否则新线路被当成不存在、占比分母仍来自旧库。）
# 学习注释：缓存合法实体和总事件数，减少每次提问重复读取数据库。
_DICT_CACHE: dict | None = None
# 学习注释：None（尚无值）；保存到_DICT_CACHE_MTIME。
_DICT_CACHE_MTIME: tuple | None = None
# 学习注释：调用re.compile，处理'^\\d{4}-\\d{2}-\\d{2}$'；保存到_DATE_RE。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# 学习注释：既检查YYYY-MM-DD外形，也检查日期实际存在，例如拒绝2月30日。
def _is_real_date(s: str) -> bool:
    """严格校验 YYYY-MM-DD 是真实存在的日期（拒绝 2025-02-30 这类非法值）。"""
    # 学习注释：只有不满足（调用_DATE_RE.match，处理s）才进入这一分支。
    if not _DATE_RE.match(s):
        # 学习注释：返回False（不成立），结束本次函数调用。
        return False
    # 学习注释：从datetime引入datetime，用于日期时间解析与计算。
    from datetime import datetime
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：调用datetime.strptime，处理s。
        datetime.strptime(s, "%Y-%m-%d")
        # 学习注释：返回True（成立），结束本次函数调用。
        return True
    except ValueError:
        # 学习注释：返回False（不成立），结束本次函数调用。
        return False


# 学习注释：从当前版本数据库读取合法线路、中心和事件总数，并按文件路径/时间/大小缓存。
def _load_dict() -> dict:
    """从 gridmate.db 加载合法取值词典：{line_names: [...], supply_centers: [...]}。"""
    # 学习注释：声明下方修改的是模块级共享变量_DICT_CACHE, _DICT_CACHE_MTIME，而非函数局部同名变量。
    global _DICT_CACHE, _DICT_CACHE_MTIME
    # 学习注释：versioning.current_snapshot()的SQLite文件路径属性（db_path）；保存到SQLite文件路径（db_path）。
    db_path = versioning.current_snapshot().db_path
    # 学习注释：调用db_path.stat；保存到统计证据或文件属性（stat）。
    stat = db_path.stat()
    # 学习注释：缓存键包含数据库绝对路径、纳秒修改时间和文件大小，版本变化时不复用旧词典。
    mtime = (str(db_path.resolve()), stat.st_mtime_ns, stat.st_size)
    # 学习注释：缓存存在且指向相同文件状态时直接复用；否则重新查当前版本。
    if _DICT_CACHE is not None and mtime is not None and mtime == _DICT_CACHE_MTIME:
        # 学习注释：返回_DICT_CACHE，结束本次函数调用。
        return _DICT_CACHE
    # 学习注释：get_conn的执行结果；保存到数据库连接（conn）。
    conn = get_conn()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
        cur = conn.cursor()
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT DISTINCT 线路名称 FROM users WHERE 线路名称 != '' ORDER BY 线路名称")
        # 学习注释：遍历cur.fetchall()，将r[0]收集为列表；保存到line_names。
        line_names = [r[0] for r in cur.fetchall()]
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT DISTINCT 供服中心 FROM users WHERE 供服中心 != '' ORDER BY 供服中心")
        # 学习注释：遍历cur.fetchall()，将r[0]收集为列表；保存到supply_centers。
        supply_centers = [r[0] for r in cur.fetchall()]
        # 顺手把总事件数也算了缓存（outage_type_ratio 模板的分母），
        # 避免每次问「占比」都单独再连一次库 COUNT。
        cur.execute("SELECT COUNT(DISTINCT 事件序号) FROM users")
        # 学习注释：cur.fetchone()中键/位置为0的值；保存到去重后的事件总数（total_events）。
        total_events = cur.fetchone()[0]
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()
    # 学习注释：包含line_names、supply_centers、total_events的字典；保存到_DICT_CACHE。
    _DICT_CACHE = {
        "line_names": line_names,
        "supply_centers": supply_centers,
        "total_events": total_events,
    }
    # 学习注释：mtime；保存到_DICT_CACHE_MTIME。
    _DICT_CACHE_MTIME = mtime
    # 学习注释：返回_DICT_CACHE，结束本次函数调用。
    return _DICT_CACHE


# 覆盖范围从请求快照读取。无年份月份默认用数据起始年份；
# 今年/去年/上个月依据当前日期，不篡改用户明确的年份。


# 学习注释：把模板、合法实体示例、当前日期和数据范围写入模型任务说明。
def _build_prompt(query: str) -> str:
    """构造意图识别提示词：把 4 个模板 + 合法词典喂给模型，让它做受限填空。"""
    # 学习注释：从datetime引入date，用于日期时间解析与计算。
    from datetime import date
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：2项元组：snapshot的数据范围起始日属性（coverage_start）、snapshot的数据范围结束边界属性（coverage_end）；保存到_DATA_START、_DATA_END。
    _DATA_START, _DATA_END = snapshot.coverage_start, snapshot.coverage_end
    # 学习注释：_DATA_START中键/位置为:4的值；保存到_DATA_YEAR。
    _DATA_YEAR = _DATA_START[:4]
    # 学习注释：调用date.today；保存到today。
    today = date.today()
    # 学习注释：_load_dict的执行结果；保存到d。
    d = _load_dict()
    # 词典太长会撑爆 prompt，只展示少量样例 + 总数，模型凭样例理解假名格式即可。
    # 学习注释：提示词只放前15个线路名称示例；完整合法性检查仍使用数据库里的全部名称。
    line_sample = "、".join(d["line_names"][:15])
    # 学习注释：供服中心同样只展示少量格式示例，避免提示词无限增长。
    supply_sample = "、".join(d["supply_centers"][:15])
    # 学习注释：返回调用f"你是停电台账「问数助手」的意图解析器。用户用自然语言问停电相关的统计问题，你要把它解析成**受限的结构化参数**，交给确定性 SQL 模板执行。\n\n## 可选模板（只能选这 4 个之一，不要自创）\n1. line_outage_count：某线路停电次数（需要 line_name，可选叠加 start_time+end_time 时间过滤）\n2. supply_center_energy：某供服中心缺供电量总和（需要 sup…
    return (
        "你是停电台账「问数助手」的意图解析器。用户用自然语言问停电相关的统计问题，"
        "你要把它解析成**受限的结构化参数**，交给确定性 SQL 模板执行。\n\n"
        "## 可选模板（只能选这 4 个之一，不要自创）\n"
        "1. line_outage_count：某线路停电次数（需要 line_name，可选叠加 start_time+end_time 时间过滤）\n"
        "2. supply_center_energy：某供服中心缺供电量总和（需要 supply_center，可选叠加时间过滤）\n"
        "3. time_range_outage_count：某时间段停电事件数（需要 start_time + end_time，可选叠加 line_name 或 supply_center 过滤）\n"
        "4. outage_type_ratio：各类停电性质占比（无额外参数，**不支持**叠加任何过滤）\n\n"
        f"## 合法线路名（假名，共 {len(d['line_names'])} 个，样例）：{line_sample}\n"
        f"## 合法供服中心（假名，共 {len(d['supply_centers'])} 个，样例）：{supply_sample}\n\n"
        "## 规则\n"
        "- **指标与过滤可以组合**：问「线路1 2025年6月停了几次电」→ template=line_outage_count，"
        "  line_name=线路1，同时填 start_time/end_time 表示 6 月范围；问「2025年6月线路1停了几次」"
        "  → template=time_range_outage_count，同时填 line_name。两个问法都要把线路和时间都填上，"
        "  不能只填一个（否则会漏掉条件）。\n"
        "- 但组合只能在上面每个模板标注的「可选叠加」范围内；outage_type_ratio 不能叠加任何过滤。\n"
        "- line_name / supply_center 必须从上面词典里选，用户说出的名称若对不上，"
        "  选最接近的那个假名，并降低 confidence；完全对不上就填 null、confidence 设 0。\n"
        f"- 时间只填 YYYY-MM-DD，且必须是真实存在的日期（不能是 2月30日 这种）。\n"
        f"- **区间是「左闭右开」**：end_time 填的是「范围结束后的第一天」，不是最后一天。\n"
        f"- 当前日期是 {today.isoformat()}；台账范围为 [{_DATA_START}, {_DATA_END})（抽样台账）。\n"
        f"    · 用户说「6月」这种**不带年份**的月份，锚定到 {_DATA_YEAR} 年（如 {_DATA_YEAR}-06-01 ~ {_DATA_YEAR}-07-01）；\n"
        f"    · 「去年」是 {today.year - 1} 年；「今年」是 {today.year} 年；「全年」未带年份时默认数据年份 {_DATA_YEAR}。\n"
        f"    · 用户明确说「今年」或某个**数据没有覆盖的年份**时，"
        f"      **不要擅自改成 {_DATA_YEAR}**——如实按用户说的年份解析，并显著降低 confidence"
        f"      （系统会据此判断「超出数据范围」）。\n"
        f"    · 「上个月」从当前日期 {today.isoformat()} 往前推一整月，end 是当前月首日。\n"
        f"- 用户说「3月15日」这种**单日**问数时，start_time=当天、end_time=次日"
        f"  （如 3月15日 → start={_DATA_YEAR}-03-15, end={_DATA_YEAR}-03-16），不要 start 和 end 填同一天。\n"
        "- 拿不准用户到底要查什么模板时，选最贴切的，并如实降低 confidence。\n"
        "- **「停电性质」和「停电原因」不是一回事**：台账里的字段叫「停电性质」"
        "（内部故障/计划施工/计划检修/计划调电等分类标签），这才是 outage_type_ratio 统计的维度；"
        "而「停电原因」指的是备注里描述的具体故障原因（开关跳闸、电缆故障等），"
        "那不是 SQL 聚合的维度。用户问「停电原因有哪些」若指向分类占比，"
        "按 outage_type_ratio 处理；若指向具体故障现象，如实降低 confidence（那属于相似事件检索，不在本模板范围）。\n"
        "- 只输出结构化参数，不要任何解释文字。\n\n"
        "用户问题：{query}"
    ).format(query=query)


# 学习注释：区分完全覆盖、部分覆盖和范围外；数据缺失不能解释成事件数为零。
def _check_coverage(r: _IntentParams) -> dict | None:
    """检测查询时间区间与台账数据覆盖范围的关系，返回覆盖状态。

    返回 None 表示区间完全落在覆盖范围内（含无时间过滤）；返回 dict 表示需要特殊处理：
      - {"status": "out_of_coverage", ...}：完全超范围，SQL 会命中 0 行，须拦下说明；
      - {"status": "partial", "requested_range": ..., "effective_range": ...}：
        部分重叠，SQL 能跑、但结果只覆盖区间里落在数据范围内的那段，须在回答里提示缺口。

    为什么不能把「部分重叠」静默放行：
      SQL 天然只命中已有记录，若查 [2025-12-15, 2026-01-15)，会只命中 2025 那 16 天，
      但用户可能把结果当成整个跨年区间的完整统计——「数据没有 2026」被误读成「2026 停了 0 次」。
      所以部分重叠也要如实标出有效范围，把「查了多少」和「数据到哪」分开讲。
    """
    # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.current_snapshot()
    # 学习注释：2项元组：snapshot的数据范围起始日属性（coverage_start）、snapshot的数据范围结束边界属性（coverage_end）；保存到_DATA_START、_DATA_END。
    _DATA_START, _DATA_END = snapshot.coverage_start, snapshot.coverage_end
    # 学习注释：r的start_time属性（start_time）；保存到start。
    start = r.start_time
    # 学习注释：r的end_time属性（end_time）；保存到end。
    end = r.end_time
    # 学习注释：只有start尚未设置（None），并且end尚未设置（None）才进入这一分支。
    if start is None and end is None:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 只要填了时间，成对校验已在 _validate 通过，这里二者都非空
    if start is None or end is None:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 完全超范围：区间与 [数据起, 数据止) 无交集
    # 学习注释：查询区间完全落在台账范围外时，不执行一个容易被误读为“0次”的统计。
    if end <= _DATA_START or start >= _DATA_END:
        # 学习注释：返回包含status、message的字典，结束本次函数调用。
        return {
            "status": "out_of_coverage",
            "message": (
                f"台账数据范围为 [{_DATA_START}, {_DATA_END})（抽样台账），"
                f"你要查的 [{start}, {end}) 超出了这个范围，查不到数据。"
                "请在台账覆盖范围内查询。"
            ),
        }
    # 部分重叠：区间与数据范围有交集但不完全被覆盖，标出有效区间
    # 学习注释：实际起点取请求起点和数据起点中较晚的一个，求两个区间的交集。
    eff_start = start if start >= _DATA_START else _DATA_START
    # 学习注释：实际终点取请求终点和数据终点中较早的一个；结束边界不包含在区间内。
    eff_end = end if end <= _DATA_END else _DATA_END
    # 学习注释：只有start、end不等于2项元组：eff_start、eff_end才进入这一分支。
    if (start, end) != (eff_start, eff_end):
        # 学习注释：返回包含status、requested_range、effective_range的字典，结束本次函数调用。
        return {
            "status": "partial",
            "requested_range": (start, end),
            "effective_range": (eff_start, eff_end),
        }
    # 学习注释：返回None（尚无值），结束本次函数调用。
    return None


# 学习注释：调用结构化模型提取指标和条件，校验后执行固定SQL，再返回数字和证据。
@versioning.pinned_dataset
async def ask(query: str, conf_threshold: float = 0.6) -> dict:
    """自然语言问数主入口。

    返回 dict：
      - status: "ok" | "low_confidence" | "error"
      - intent: 解析出的参数（含 template 和取值）
      - result: 仅 status=ok 时存在，是 query_templates.execute_query 的结果
      - message: 面向用户的说明（low_confidence / error 时是解释，ok 时是一句话结论）

    置信度低于 conf_threshold 时不硬答，返回 low_confidence 让用户确认。
    """
    # 学习注释：创建受_IntentParams约束的模型调用链，要求返回模板、实体、日期和把握值。
    model = llm.structured(_IntentParams, temperature=0)
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：异步请求模型解析用户问题；返回对象仍需后续业务校验，格式正确不等于理解正确。
        r: _IntentParams = await model.ainvoke(_build_prompt(query))
    except Exception as e:
        # 学习注释：记录warning级别日志，用于定位运行状态。
        logger.warning("问数意图解析失败 query=%r: %s", query, e, exc_info=True)
        # 学习注释：返回包含status、message的字典，结束本次函数调用。
        return {"status": "error", "message": "我暂时没听懂这个问题，请换个说法，或直接说线路名/供服中心/时间段。"}

    # 参数合法性校验（即使模型吐了，也要过代码这一关，防口径漂移）
    # 学习注释：先检查实体、日期、必填项与过滤组合；不能因为模型自称有把握就跳过规则。
    err = _validate(r)
    # 学习注释：只有err为真/非空才进入这一分支。
    if err:
        # 学习注释：返回包含status、intent、message的字典，结束本次函数调用。
        return {"status": "low_confidence", "intent": r.model_dump(), "message": err}

    # 范围检测：完全超范围如实返回 out_of_coverage，不把「数据没有这一年」误报成「停了 0 次」；
    # 部分重叠则正常查数，但把 effective_range 带进结果和证据，让回答能提示数据缺口。
    # 学习注释：把用户时间范围与当前数据范围比较，区分全部覆盖、部分覆盖和完全超出。
    coverage = _check_coverage(r)
    # 学习注释：只有coverage为真/非空，并且coverage['status']等于'out_of_coverage'才进入这一分支。
    if coverage and coverage["status"] == "out_of_coverage":
        # 学习注释：返回包含status、intent、message的字典，结束本次函数调用。
        return {"status": "out_of_coverage", "intent": r.model_dump(), "message": coverage["message"]}

    # 学习注释：把握值低于阈值时暂不执行统计；此阈值是当前策略，不是可靠性概率保证。
    if r.confidence < conf_threshold:
        # 学习注释：返回包含status、intent、message的字典，结束本次函数调用。
        return {
            "status": "low_confidence",
            "intent": r.model_dump(),
            "message": f"我理解你想查「{TEMPLATES[r.template]['desc']}」，但把握不高（{r.confidence:.0%}）。"
                       f"能帮我确认一下吗？",
        }

    # 组装模板参数 + 可选过滤
    execution_intent = r
    # 学习注释：只有coverage为真/非空，并且coverage['status']等于'partial'才进入这一分支。
    if coverage and coverage["status"] == "partial":
        # 学习注释：coverage中键/位置为'effective_range'的值；保存到eff_s、eff_e。
        eff_s, eff_e = coverage["effective_range"]
        # 学习注释：调用r.model_copy；保存到execution_intent。
        execution_intent = r.model_copy(update={"start_time": eff_s, "end_time": eff_e})
    # 学习注释：把同一意图拆成模板主参数和附加过滤参数，交给固定SQL。
    params, filters = _to_params(execution_intent)
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：用白名单SQL和参数计算数字；此处的次数、和、占比不是由模型心算。
        result = execute_query(r.template, params, filters=filters)
    except Exception as e:
        # 学习注释：记录warning级别日志，用于定位运行状态。
        logger.warning("问数执行失败 template=%r: %s", r.template, e, exc_info=True)
        # 学习注释：返回包含status、message的字典，结束本次函数调用。
        return {"status": "error", "message": "查询执行出错了，请稍后重试。"}

    # 占比模板补充「多标签事件数」，让回答能如实说明口径（见 _enrich_result）
    result = _enrich_result(r.template, result)

    # 部分重叠：把有效范围带进结果和证据，回答里如实提示「只覆盖到某日」，
    # 不让用户把跨年区间的部分统计误读成完整区间。
    if coverage and coverage["status"] == "partial":
        # 学习注释：coverage中键/位置为'effective_range'的值；保存到eff_s、eff_e。
        eff_s, eff_e = coverage["effective_range"]
        # 学习注释：包含status、requested_range、effective_range的字典；保存到result['coverage']。
        result["coverage"] = {
            "status": "partial",
            "requested_range": coverage["requested_range"],
            "effective_range": coverage["effective_range"],
        }
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到_partial_note。
        _partial_note = f"注意：仅统计台账范围内的 [{eff_s}, {eff_e})，请求区间的其余部分没有覆盖。"
    else:
        # 学习注释：''；保存到_partial_note。
        _partial_note = ""

    # 学习注释：_evidence的执行结果；保存到evidence。
    evidence = _evidence(execution_intent, result)
    # 学习注释：调用_requested_filters，处理r；保存到evidence['requested_filters']。
    evidence["requested_filters"] = _requested_filters(r)
    # 学习注释：参数字典（params）；保存到evidence['query_params']。
    evidence["query_params"] = params
    # 学习注释：筛选条件（filters）；保存到evidence['query_filters']。
    evidence["query_filters"] = filters
    # 学习注释：只有coverage为真/非空，并且coverage['status']等于'partial'才进入这一分支。
    if coverage and coverage["status"] == "partial":
        # 学习注释：coverage中键/位置为'effective_range'的值；保存到eff_s、eff_e。
        eff_s, eff_e = coverage["effective_range"]
        # 学习注释：'partial'；保存到evidence['coverage_status']。
        evidence["coverage_status"] = "partial"
        # 学习注释：coverage中键/位置为'effective_range'的值；保存到evidence['effective_range']。
        evidence["effective_range"] = coverage["effective_range"]
        # 学习注释：coverage中键/位置为'requested_range'的值；保存到evidence['requested_range']。
        evidence["requested_range"] = coverage["requested_range"]
        # 实际执行的时间范围被裁到数据范围内，effective_filters 要如实反映
        if "time_range" in evidence["effective_filters"]:
            # 学习注释：包含start_time、end_time的字典；保存到evidence['effective_filters']['time_range']。
            evidence["effective_filters"]["time_range"] = {"start_time": eff_s, "end_time": eff_e}
        # 学习注释：向evidence['warnings']末尾添加把花括号内的变量填入f字符串，生成文字。
        evidence["warnings"].append(
            f"查询区间超出数据覆盖范围，实际只统计了 [{eff_s}, {eff_e}) 这段"
        )
    # 学习注释：只有coverage为真/非空，并且coverage['status']等于'out_of_coverage'才进入这一分支。
    elif coverage and coverage["status"] == "out_of_coverage":
        # 不会走到这（前面已 return），保留分支以防未来结构变化
        evidence["coverage_status"] = "out"
    # 学习注释：只有r.start_time为真/非空，或者r.end_time为真/非空才进入这一分支。
    elif r.start_time or r.end_time:
        # 学习注释：'full'；保存到evidence['coverage_status']。
        evidence["coverage_status"] = "full"

    # 学习注释：返回包含status、intent、result、message、evidence的字典，结束本次函数调用。
    return {
        "status": "ok",
        "intent": r.model_dump(),
        "result": result,
        "message": _summarize(result) + _partial_note,
        "evidence": evidence,
    }


# 学习注释：补充事件总数、多标签信息等解释数据，避免误解占比口径。
def _enrich_result(template: str, result: dict) -> dict:
    """给结果补充「口径说明」所需的数据，不改动原始查询结果。

    占比（outage_type_ratio）是多标签口径：一个事件可属多个停电性质，
    各类次数之和会超过事件总数。这里额外查一次「跨多个性质的事件数」，
    让 _summarize 能如实提示用户，而不是让用户把占比误读成互斥分布。
    """
    # 学习注释：只有template不等于'outage_type_ratio'才进入这一分支。
    if template != "outage_type_ratio":
        # 学习注释：返回result，结束本次函数调用。
        return result
    # 学习注释：get_conn的执行结果；保存到数据库连接（conn）。
    conn = get_conn()
    # 学习注释：执行可能失败的步骤；except处理异常，finally无论成功失败都负责清理。
    try:
        # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
        cur = conn.cursor()
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT 事件序号 FROM users WHERE 停电性质 != '' "
            "  GROUP BY 事件序号 HAVING COUNT(DISTINCT 停电性质) > 1)"
        )
        # 学习注释：cur.fetchone()中键/位置为0的值；保存到result['multi_label_events']。
        result["multi_label_events"] = cur.fetchone()[0]
    except Exception as e:
        # 学习注释：记录warning级别日志，用于定位运行状态。
        logger.warning("计算多标签事件数失败: %s", e)
        # 学习注释：0；保存到result['multi_label_events']。
        result["multi_label_events"] = 0
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()  # 无论成功与否都关连接，避免异常路径泄漏（与 _load_dict 写法一致）
    # total_events 已在 params 里，但结果里也补一份方便 _summarize 引用
    result["total_events"] = _load_dict()["total_events"]
    # 学习注释：返回result，结束本次函数调用。
    return result


# 学习注释：把意图参数转成「用户请求的条件」字典（阶段1：统一证据的 requested_filters）。
def _requested_filters(r: _IntentParams) -> dict:
    """把意图参数转成「用户请求的条件」字典（阶段1：统一证据的 requested_filters）。

    记录用户问句里**明说**的条件，作为追溯「这次查了什么」的第一手依据。
    """
    # 学习注释：空字典；保存到cond。
    cond = {}
    # 学习注释：只有r.line_name为真/非空才进入这一分支。
    if r.line_name:
        # 学习注释：r的line_name属性（line_name）；保存到cond['line_name']。
        cond["line_name"] = r.line_name
    # 学习注释：只有r.supply_center为真/非空才进入这一分支。
    if r.supply_center:
        # 学习注释：r的supply_center属性（supply_center）；保存到cond['supply_center']。
        cond["supply_center"] = r.supply_center
    # 学习注释：只有r.start_time为真/非空，或者r.end_time为真/非空才进入这一分支。
    if r.start_time or r.end_time:
        # 学习注释：包含start_time、end_time的字典；保存到cond['time_range']。
        cond["time_range"] = {"start_time": r.start_time, "end_time": r.end_time}
    # 学习注释：返回cond，结束本次函数调用。
    return cond


# 学习注释：把意图参数转成「实际执行的条件」字典（阶段1：统一证据的 effective_filters）。
def _effective_filters(r: _IntentParams) -> dict:
    """把意图参数转成「实际执行的条件」字典（阶段1：统一证据的 effective_filters）。

    与 requested_filters 的区别：部分覆盖时实际时间范围会被裁到数据范围内。
    本函数在 _check_coverage 之后调用，用已裁减的有效区间。这里先按原始意图返回，
    由 ask() 在 partial 分支里用 effective_range 覆盖 time_range。
    """
    # 学习注释：返回调用_requested_filters，处理r，结束本次函数调用。
    return _requested_filters(r)


# 学习注释：记录统计采用的模板、来源、参数、范围和粒度，供后续展示与核对。
def _evidence(r: _IntentParams, result: dict) -> dict:
    """生成数字结论的溯源依据：来源模板 + 口径 + 过滤条件。

    这是「可追溯回答」里"数字附 SQL 依据"的落点——不是让 LLM 事后回忆引用了什么，
    而是确定性代码在产出数字时就带上「这个数从哪张表、按什么口径、过了什么过滤」算出来的。
    """
    # 学习注释：TEMPLATES中键/位置为r.template的值；保存到选中的SQL模板定义（tpl）。
    tpl = TEMPLATES[r.template]
    # 学习注释：空列表；保存到cond。
    cond = []
    # 学习注释：只有r.template等于'line_outage_count'才进入这一分支。
    if r.template == "line_outage_count":
        # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
        cond.append(f"线路名称 = {r.line_name}")
        # 学习注释：只有r.start_time为真/非空，或者r.end_time为真/非空才进入这一分支。
        if r.start_time or r.end_time:
            # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
            cond.append(f"起始时间 ∈ [{r.start_time}, {r.end_time})")
    # 学习注释：只有r.template等于'supply_center_energy'才进入这一分支。
    elif r.template == "supply_center_energy":
        # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
        cond.append(f"供服中心 = {r.supply_center}")
        # 学习注释：只有r.start_time为真/非空，或者r.end_time为真/非空才进入这一分支。
        if r.start_time or r.end_time:
            # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
            cond.append(f"起始时间 ∈ [{r.start_time}, {r.end_time})")
    # 学习注释：只有r.template等于'time_range_outage_count'才进入这一分支。
    elif r.template == "time_range_outage_count":
        # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
        cond.append(f"起始时间 ∈ [{r.start_time}, {r.end_time})")
        # 学习注释：只有r.line_name为真/非空才进入这一分支。
        if r.line_name:
            # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
            cond.append(f"线路名称 = {r.line_name}")
        # 学习注释：只有r.supply_center为真/非空才进入这一分支。
        if r.supply_center:
            # 学习注释：向cond末尾添加把花括号内的变量填入f字符串，生成文字。
            cond.append(f"供服中心 = {r.supply_center}")
    # 学习注释：只有r.template等于'outage_type_ratio'才进入这一分支。
    elif r.template == "outage_type_ratio":
        # 学习注释：向cond末尾添加'全量事件（按停电性质分组）'。
        cond.append("全量事件（按停电性质分组）")
    # 学习注释：包含template_id、template_desc、granularity、filters、source_table、time_semantics的字典；保存到evidence。
    evidence = {
        "template_id": r.template,
        "template_desc": tpl["desc"],
        "granularity": tpl["metric_granularity"],  # 口径（事件级/用户级/线段级）
        "filters": cond,
        "source_table": "users",  # 本阶段四个模板都在 users 表聚合（口径正确：单表内 SUM）
        # 统一证据公共字段（借鉴 Quarry，阶段1/2）：数据版本 + 请求/实际条件 + 覆盖状态
        # dataset_version 从版本清单读（阶段2），统计与检索共用同一版本标识
        **versioning.evidence_context(),
        "time_semantics": "record_start",
        "requested_filters": _requested_filters(r),
        "effective_filters": _effective_filters(r),
        "coverage_status": "none",
    }
    # 占比是多标签口径，显式声明，避免下游（Agent/忠实度评审）误读成互斥分布
    if r.template == "outage_type_ratio":
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到evidence['note']。
        evidence["note"] = (
            f"多标签口径：停电性质可重叠，{result.get('multi_label_events', 0)} 个事件"
            "同时属于多个性质，各类占比之和可能超过 100%。"
        )
    # 学习注释：返回evidence，结束本次函数调用。
    return evidence


# 学习注释：检查模型参数的必填项、合法实体、日期及支持的过滤组合。
def _validate(r: _IntentParams) -> str | None:
    """校验模型吐出的参数是否合法，返回错误说明；合法返回 None。

    除了必填参数校验，还校验「额外过滤」是否是该模板白名单允许的组合——
    模型若给 line_outage_count 附带了 time 过滤、或给占比附带了线路过滤，
    要么支持（白名单内），要么拒绝（明确告知），绝不静默忽略。
    """
    # 学习注释：只有r.template不包含在TEMPLATES才进入这一分支。
    if r.template not in TEMPLATES:
        # 学习注释：返回'这个统计口径我不支持，请换个问法。'，结束本次函数调用。
        return "这个统计口径我不支持，请换个问法。"
    # 学习注释：_load_dict的执行结果；保存到d。
    d = _load_dict()

    # 必填参数校验（各模板自己的主键）
    if r.template == "line_outage_count":
        # 学习注释：只有不满足（r.line_name为真/非空）才进入这一分支。
        if not r.line_name:
            # 学习注释：返回'请告诉我具体是哪条线路。'，结束本次函数调用。
            return "请告诉我具体是哪条线路。"
        # 学习注释：只有r.line_name不包含在d中键/位置为'line_names'的值才进入这一分支。
        if r.line_name not in d["line_names"]:
            # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
            return f"线路「{r.line_name}」在台账里查不到，请换个线路名。"
    # 学习注释：只有r.template等于'supply_center_energy'才进入这一分支。
    if r.template == "supply_center_energy":
        # 学习注释：只有不满足（r.supply_center为真/非空）才进入这一分支。
        if not r.supply_center:
            # 学习注释：返回'请告诉我具体是哪个供服中心。'，结束本次函数调用。
            return "请告诉我具体是哪个供服中心。"
        # 学习注释：只有r.supply_center不包含在d中键/位置为'supply_centers'的值才进入这一分支。
        if r.supply_center not in d["supply_centers"]:
            # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
            return f"供服中心「{r.supply_center}」在台账里查不到，请换个名称。"
    # 学习注释：只有r.template等于'time_range_outage_count'才进入这一分支。
    if r.template == "time_range_outage_count":
        # 学习注释：只有不满足（r.start_time为真/非空），或者不满足（r.end_time为真/非空）才进入这一分支。
        if not r.start_time or not r.end_time:
            # 学习注释：返回'请给出明确的起止时间。'，结束本次函数调用。
            return "请给出明确的起止时间。"
        # 学习注释：只有不满足（_is_real_date的执行结果，并且_is_real_date的执行结果）才进入这一分支。
        if not (_is_real_date(r.start_time) and _is_real_date(r.end_time)):
            # 学习注释：返回'时间必须是真实存在的日期（YYYY-MM-DD）。'，结束本次函数调用。
            return "时间必须是真实存在的日期（YYYY-MM-DD）。"
        # 学习注释：只有r.start_time不小于r的end_time属性（end_time）才进入这一分支。
        if r.start_time >= r.end_time:
            # 学习注释：返回'起始时间要早于结束时间。'，结束本次函数调用。
            return "起始时间要早于结束时间。"

    # 额外过滤校验：模型多填的字段必须是该模板支持的组合
    err = _validate_filters(r, d)
    # 学习注释：只有err为真/非空才进入这一分支。
    if err:
        # 学习注释：返回err，结束本次函数调用。
        return err
    # 学习注释：返回None（尚无值），结束本次函数调用。
    return None


# 各模板的「主键字段」：这些字段是模板本身要的，不算「额外过滤」。
# 其余非空字段才是「额外过滤」，需要进白名单校验。
_TEMPLATE_PRIMARY = {
    "line_outage_count": {"line_name"},
    "supply_center_energy": {"supply_center"},
    "time_range_outage_count": {"start_time", "end_time"},
    "outage_type_ratio": set(),
}


# 学习注释：检查额外过滤是否被当前模板允许；不能悄悄忽略用户条件。
def _validate_filters(r: _IntentParams, d: dict) -> str | None:
    """校验「额外过滤」字段是否是该模板支持的组合；不支持的返回说明，支持返回 None。

    判断规则：统一枚举所有「非空的可选维度」，减去该模板的主键字段，
    剩下的每个额外过滤都要在白名单 optional_filters 里；不在就明确拒绝。
    不再按模板手写漏项分支——之前的实现漏掉了
    「line 模板带 supply_center」「energy 模板带 line_name」这两条，
    导致它们被静默丢弃后按全年/全中心统计。
    """
    # 学习注释：TEMPLATES中键/位置为r.template的值；保存到选中的SQL模板定义（tpl）。
    tpl = TEMPLATES[r.template]
    # 学习注释：从tpl读取键'optional_filters'，缺少时取空字典；保存到allowed。
    allowed = tpl.get("optional_filters", {})

    # 统一枚举：所有非空的额外维度（相对主键之外）
    # 学习注释：找出模型额外填写的维度，以便逐个核对当前模板是否支持。
    extra = _collect_filters(r)

    # 每个额外过滤都要能被白名单识别；识别不到就是「不支持的组合」
    for fkey in extra:
        # 学习注释：某条件不被模板支持时明确返回说明，不能丢掉该条件再报一个更大范围的数字。
        if fkey not in allowed:
            # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
            return (f"「{tpl['desc']}」暂不支持叠加「{_FILTER_CN.get(fkey, fkey)}」条件，"
                    f"请分开问，或换个问法。")

    # 时间过滤要成对出现 + 日期合法性（不管它是不是主键，只要填了时间就得合法）
    if r.start_time is not None or r.end_time is not None:
        # 学习注释：异或式检查：只填起点或只填终点都不合法，两个日期必须一起出现。
        if (r.start_time is None) != (r.end_time is None):
            # 学习注释：返回'时间需要同时给出起始和结束日期。'，结束本次函数调用。
            return "时间需要同时给出起始和结束日期。"
        # 学习注释：只有不满足（_is_real_date的执行结果，并且_is_real_date的执行结果）才进入这一分支。
        if not (_is_real_date(r.start_time) and _is_real_date(r.end_time)):
            # 学习注释：返回'时间必须是真实存在的日期（YYYY-MM-DD）。'，结束本次函数调用。
            return "时间必须是真实存在的日期（YYYY-MM-DD）。"
        # 学习注释：只有r.start_time不小于r的end_time属性（end_time）才进入这一分支。
        if r.start_time >= r.end_time:
            # 学习注释：返回'起始时间要早于结束时间。'，结束本次函数调用。
            return "起始时间要早于结束时间。"

    # 实体（线路/供服中心）无论作主键还是可选过滤，统一做词典校验——
    # 库外值（如「线路99999」）必须报「查不到」，不能混同「确实没有事件（0次）」。
    if r.line_name is not None and r.line_name not in d["line_names"]:
        # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
        return f"线路「{r.line_name}」在台账里查不到，请换个线路名。"
    # 学习注释：只有r.supply_center已经有值（不是None），并且r.supply_center不包含在d中键/位置为'supply_centers'的值才进入这一分支。
    if r.supply_center is not None and r.supply_center not in d["supply_centers"]:
        # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
        return f"供服中心「{r.supply_center}」在台账里查不到，请换个名称。"
    # 学习注释：返回None（尚无值），结束本次函数调用。
    return None


# 额外过滤字段 key -> 中文名（用于「不支持组合」的人话提示）
_FILTER_CN = {
    "time_range": "时间范围",
    "line": "线路",
    "supply_center": "供服中心",
}


# 学习注释：找出模板主参数以外的条件，让统一的白名单逻辑检查它们。
def _collect_filters(r: _IntentParams) -> list[str]:
    """统一枚举模型「额外填写」的过滤维度（相对该模板主键之外的维度）。

    规则：对四个可选维度做统一判断，与模板主键集合做差集，
    剩下的就是「额外过滤」。不按模板手写分支，避免漏项。
    """
    # 学习注释：取当前模板本来必需的字段，剩余非空字段才属于附加过滤。
    primary = _TEMPLATE_PRIMARY[r.template]
    # 学习注释：空列表；保存到extra。
    extra = []
    # 线路：只要填了且不是主键，就是额外过滤
    if r.line_name is not None and "line_name" not in primary:
        # 学习注释：向extra末尾添加'line'。
        extra.append("line")
    # 供服中心：同上
    if r.supply_center is not None and "supply_center" not in primary:
        # 学习注释：向extra末尾添加'supply_center'。
        extra.append("supply_center")
    # 时间：只要填了任一（start 或 end）且时间不是主键，就是额外过滤。
    # 注意：单边时间（只填一个）也会被算进来，交给 _validate_filters 报「需要成对」。
    if (r.start_time is not None or r.end_time is not None) and "start_time" not in primary:
        # 学习注释：向extra末尾添加'time_range'。
        extra.append("time_range")
    # 学习注释：返回extra，结束本次函数调用。
    return extra


# 学习注释：把模型字段转换为SQL主参数和附加过滤两个字典；占比分母由数据库计算。
def _to_params(r: _IntentParams) -> tuple[dict, dict]:
    """把意图参数转成 (params, filters) 两元组。

    params 是模板主键参数；filters 是额外过滤（key 对齐 optional_filters）。
    """
    # 学习注释：空字典；保存到筛选条件（filters）。
    filters = {}
    # 学习注释：只有r.template等于'line_outage_count'才进入这一分支。
    if r.template == "line_outage_count":
        # 学习注释：包含line_name的字典；保存到参数字典（params）。
        params = {"line_name": r.line_name}
        # 学习注释：只有r.start_time已经有值（不是None），或者r.end_time已经有值（不是None）才进入这一分支。
        if r.start_time is not None or r.end_time is not None:
            # 学习注释：包含start_time、end_time的字典；保存到filters['time_range']。
            filters["time_range"] = {"start_time": r.start_time, "end_time": r.end_time}
    # 学习注释：只有r.template等于'supply_center_energy'才进入这一分支。
    elif r.template == "supply_center_energy":
        # 学习注释：包含supply_center的字典；保存到参数字典（params）。
        params = {"supply_center": r.supply_center}
        # 学习注释：只有r.start_time已经有值（不是None），或者r.end_time已经有值（不是None）才进入这一分支。
        if r.start_time is not None or r.end_time is not None:
            # 学习注释：包含start_time、end_time的字典；保存到filters['time_range']。
            filters["time_range"] = {"start_time": r.start_time, "end_time": r.end_time}
    # 学习注释：只有r.template等于'time_range_outage_count'才进入这一分支。
    elif r.template == "time_range_outage_count":
        # 学习注释：包含start_time、end_time的字典；保存到参数字典（params）。
        params = {"start_time": r.start_time, "end_time": r.end_time}
        # 学习注释：只有r.line_name为真/非空才进入这一分支。
        if r.line_name:
            # 学习注释：包含line_name的字典；保存到filters['line']。
            filters["line"] = {"line_name": r.line_name}
        # 学习注释：只有r.supply_center为真/非空才进入这一分支。
        if r.supply_center:
            # 学习注释：包含supply_center的字典；保存到filters['supply_center']。
            filters["supply_center"] = {"supply_center": r.supply_center}
    # 学习注释：只有r.template等于'outage_type_ratio'才进入这一分支。
    elif r.template == "outage_type_ratio":
        # total_events 由代码算好（词典缓存里已带），不让模型参与
        # 学习注释：占比分母读取代码缓存的数据库去重事件总数，不接受模型编出的分母。
        params = {"total_events": _load_dict()["total_events"]}
    else:
        # 学习注释：空字典；保存到参数字典（params）。
        params = {}
    # 学习注释：返回2项元组：参数字典（params）、筛选条件（filters），结束本次函数调用。
    return params, filters


# 学习注释：用程序将SQL结果整理成说明文字；这里不是再次请求模型做计算。
def _summarize(result: dict) -> str:
    """把查询结果拼成一句人话，作为给用户的直接回答。"""
    # 学习注释：从result读取键'rows'，缺少时取空列表；保存到查询或表格行（rows）。
    rows = result.get("rows", [])
    # 学习注释：从result读取键'template'，缺少时取''；保存到选中的SQL模板定义（tpl）。
    tpl = result.get("template", "")
    # 学习注释：只有选中的SQL模板定义（tpl）等于'outage_type_ratio'才进入这一分支。
    if tpl == "outage_type_ratio":
        # 学习注释：只有查询或表格行（rows）为空或为False才进入这一分支。
        if not rows:
            # 学习注释：返回'台账里没有停电记录。'，结束本次函数调用。
            return "台账里没有停电记录。"
        # 学习注释：rows中键/位置为0的值；保存到top。
        top = rows[0]
        # 多标签口径说明：占比各类可重叠，总和会超过 100%
        total = result.get("total_events")
        # 学习注释：从result读取键'multi_label_events'，缺少时取0；保存到multi。
        multi = result.get("multi_label_events", 0)
        # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到base。
        base = (f"各类停电性质共 {len(rows)} 种，最多的是「{top.get('停电性质')}」"
                f"（{top.get('停电次数')} 次，占 {top.get('占比_pct')}%）。")
        # 学习注释：只有multi为真/非空才进入这一分支。
        if multi:
            # 学习注释：用把花括号内的变量填入f字符串，生成文字更新base；下方+=等写法把计算结果存回原变量。
            base += (f"注意：这是「多标签」口径——有 {multi} 个事件同时属于多个性质，"
                     f"所以各类次数之和（大于 {total} 个事件）会超过事件总数，"
                     f"占比之和也会超过 100%，这不是错误，是停电性质本身可重叠。")
        # 学习注释：返回base，结束本次函数调用。
        return base
    # 学习注释：只有查询或表格行（rows）为空或为False才进入这一分支。
    if not rows:
        # 学习注释：返回'没有查到符合条件的数据。'，结束本次函数调用。
        return "没有查到符合条件的数据。"
    # 学习注释：rows中键/位置为0的值；保存到row。
    row = rows[0]
    # 学习注释：有明确列名就取第一列；否则从首行字典取得列名，用于简单统计结果的文字展示。
    col = result["columns"][0] if result.get("columns") else next(iter(row))
    # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
    return f"{col}：{row[col]}"


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：导入asyncio：异步任务、等待、锁和超时。
    import asyncio

    # 学习注释：异步函数_demo接收调用时的上下文；把这一段可复用处理封装起来。
    async def _demo():
        # 几个典型自然语言问数（真调模型）
        for q in [
            "线路1 去年停了几次电",
            "供服中心3 的缺供电量是多少",
            "今年6月一共停了多少次电",
            "停电原因都有哪些、各占多少",
            "帮我查一下天气",  # 越界问题，应 low_confidence / error
        ]:
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"\n=== Q: {q} ===")
            # 学习注释：ask的执行结果，await等待结果后再继续；保存到r。
            r = await ask(q)
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"status={r['status']}")
            # 学习注释：只有'intent'包含在r才进入这一分支。
            if "intent" in r:
                # 学习注释：在命令行输出本步骤结果或进度。
                print(f"intent={r['intent']}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"message={r['message']}")

    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(_demo())
