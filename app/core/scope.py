# 阅读导航：检索范围约束
# 本文件作用：把线路、中心、日期校验成有效范围，再从同一版SQLite计算允许检索的事件ID。
# 建议读法：record_start按用户记录开始时间筛选；event_overlap按整个事件的时间包络重叠筛选，两者口径不同。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""统计/检索共用范围约束：严格参数、显式覆盖，以及与统计相同的事件集合。"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re
# 学习注释：从datetime引入date，用于日期时间解析与计算。
from datetime import date
# 学习注释：从typing引入Literal，用于类型提示，不负责网络调用或业务计算。
from typing import Literal

# 学习注释：从pydantic引入BaseModel, ConfigDict, field_validator, model_validator，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning

# 学习注释：'record_start'；保存到TIME_RECORD_START。
TIME_RECORD_START = "record_start"
# 学习注释：'event_overlap'；保存到TIME_EVENT_OVERLAP。
TIME_EVENT_OVERLAP = "event_overlap"
# 学习注释：包含展开字段的字典；保存到TIME_LABELS。
TIME_LABELS = {
    TIME_RECORD_START: "按用户记录的停电起始时间筛选，左闭右开；聚合方式见指标口径",
    TIME_EVENT_OVERLAP: "整个事件时间包络与区间重叠（可含区间之前开始的事件）",
}


# 学习注释：DateRange定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class DateRange(BaseModel):
    # 学习注释：extra=forbid拒绝未声明的筛选键；strict=True减少隐式类型转换导致的歧义。
    model_config = ConfigDict(extra="forbid", strict=True)
    # 学习注释：声明start字段，类型为str；值由构造对象时提供。
    start: str
    # 学习注释：声明end字段，类型为str；值由构造对象时提供。
    end: str

    # 学习注释：函数valid_date接收value；把这一段可复用处理封装起来。
    # 学习注释：Pydantic字段校验装饰器：解析指定字段时调用下方函数检查或规范化。
    @field_validator("start", "end")
    # 学习注释：类方法接收cls（当前类）作为首参，不需要先创建一个实例。
    @classmethod
    def valid_date(cls, value: str) -> str:
        # 学习注释：只有不满足（调用re.fullmatch，处理'\\d{4}-\\d{2}-\\d{2}'）才进入这一分支。
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            # 学习注释：以异常终止这一条路径：'日期必须为 YYYY-MM-DD'；上层不能把它当作正常成功。
            raise ValueError("日期必须为 YYYY-MM-DD")
        # 学习注释：进一步校验日期实际存在；仅符合2025-02-30这样的字符串外形仍不够。
        date.fromisoformat(value)
        # 学习注释：返回value，结束本次函数调用。
        return value

    # 学习注释：函数ordered接收调用时的上下文；把这一段可复用处理封装起来。
    # 学习注释：Pydantic整对象校验：字段已解析后检查跨字段关系，例如开始日必须早于结束日。
    @model_validator(mode="after")
    def ordered(self):
        # 学习注释：起点必须早于终点；相等表示空区间，不是“一整天”。
        if self.start >= self.end:
            # 学习注释：以异常终止这一条路径：'起始日期必须早于结束日期'；上层不能把它当作正常成功。
            raise ValueError("起始日期必须早于结束日期")
        # 学习注释：返回self，结束本次函数调用。
        return self


# 学习注释：RetrievalFilters定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class RetrievalFilters(BaseModel):
    # 学习注释：extra=forbid拒绝未声明的筛选键；strict=True减少隐式类型转换导致的歧义。
    model_config = ConfigDict(extra="forbid", strict=True)
    # 学习注释：None（尚无值）；保存到line。
    line: str | None = None
    # 学习注释：None（尚无值）；保存到supply_center。
    supply_center: str | None = None
    # 学习注释：None（尚无值）；保存到time_range。
    time_range: DateRange | None = None
    # 学习注释：TIME_RECORD_START；保存到时间筛选口径（time_semantics）。
    time_semantics: Literal["record_start", "event_overlap"] = TIME_RECORD_START

    # 学习注释：函数nonempty接收value；把这一段可复用处理封装起来。
    # 学习注释：Pydantic字段校验装饰器：解析指定字段时调用下方函数检查或规范化。
    @field_validator("line", "supply_center")
    # 学习注释：类方法接收cls（当前类）作为首参，不需要先创建一个实例。
    @classmethod
    def nonempty(cls, value):
        # 学习注释：只有value已经有值（不是None），并且不满足（清理value首尾或指定一侧的字符）才进入这一分支。
        if value is not None and not value.strip():
            # 学习注释：以异常终止这一条路径：'实体条件不能为空'；上层不能把它当作正常成功。
            raise ValueError("实体条件不能为空")
        # 学习注释：返回value已经有值（不是None）时取清理value首尾或指定一侧的字符，否则取None（尚无值），结束本次函数调用。
        return value.strip() if value is not None else None

    # 学习注释：函数legacy_range接收value；把这一段可复用处理封装起来。
    # 学习注释：Pydantic字段校验装饰器：解析指定字段时调用下方函数检查或规范化。
    @field_validator("time_range", mode="before")
    # 学习注释：类方法接收cls（当前类）作为首参，不需要先创建一个实例。
    @classmethod
    def legacy_range(cls, value):
        # 保留规范 start~end 旧调用的兼容；解析失败不能变成无过滤。
        if isinstance(value, str):
            # 学习注释：只有value.count('~')不等于1才进入这一分支。
            if value.count("~") != 1:
                # 学习注释：以异常终止这一条路径：'时间范围需 start/end 对象或 YYYY-MM-DD~YYYY-MM-DD'；上层不能把它当作正常成功。
                raise ValueError("时间范围需 start/end 对象或 YYYY-MM-DD~YYYY-MM-DD")
            # 学习注释：按指定分隔符拆分字符串；保存到start、end。
            start, end = value.split("~")
            # 学习注释：返回包含start、end的字典，结束本次函数调用。
            return {"start": start.strip(), "end": end.strip()}
        # 学习注释：返回value，结束本次函数调用。
        return value


# 学习注释：校验筛选结构和实体，并把请求日期与台账覆盖区间求交集。
def prepare_scope(filters: dict | None, snapshot=None) -> dict:
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（snapshot or versioning.current_snapshot()）；保存到本次请求固定的数据版本（snapshot）。
    snapshot = snapshot or versioning.current_snapshot()
    # 学习注释：用严格字段模型校验条件；非法键、错误日期或空实体会被拒绝。
    model = RetrievalFilters.model_validate({} if filters is None else filters)
    # 学习注释：保存用户原本要求的范围，后续即便裁剪到数据区间也能告诉用户差异。
    requested = model.model_dump(exclude_none=True)
    # 学习注释：另存真正用于查询的条件，避免把原请求范围悄悄改掉而无记录。
    effective = model.model_dump(exclude_none=True)
    # 实体合法性用同一版本数据库校验，不能把未知线路当成“停电 0 次”。
    entities = ((model.line, "线路名称"), (model.supply_center, "供服中心"))
    # 学习注释：只有检查是否至少有一项条件成立才进入这一分支。
    if any(value is not None for value, _ in entities):
        # 学习注释：调用snapshot.connect；保存到数据库连接（conn）。
        conn = snapshot.connect()
        # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
        try:
            # 学习注释：遍历entities，每轮把当前项交给value、column。
            for value, column in entities:
                # 学习注释：只有value已经有值（不是None），并且conn.execute(f'SELECT 1 FROM users WHERE "{column}" = ? LIMIT 1', (value,)).fetchone()尚未设置（None）才进入这一分支。
                if value is not None and conn.execute(
                    f'SELECT 1 FROM users WHERE "{column}" = ? LIMIT 1', (value,)
                ).fetchone() is None:
                    # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
                    raise ValueError(f"{column}不在当前台账字典中")
        finally:
            # 学习注释：关闭conn占用的连接或资源。
            conn.close()
    # 学习注释：'none'；保存到coverage。
    coverage = "none"
    # 学习注释：空列表；保存到需要随结果保留的说明（warnings）。
    warnings = []
    # 学习注释：只有model.time_range为真/非空才进入这一分支。
    if model.time_range:
        # 学习注释：2项元组：model.time_range的start属性（start）、model.time_range的end属性（end）；保存到start、end。
        start, end = model.time_range.start, model.time_range.end
        # 学习注释：取两个时间区间的交集：起点取较晚者，终点取较早者。
        lo, hi = max(start, snapshot.coverage_start), min(end, snapshot.coverage_end)
        # 学习注释：两个区间没有交集；后面返回范围外，而不是在别的年份搜索。
        if lo >= hi:
            # 学习注释：'out'；保存到coverage。
            coverage = "out"
            # 不伪造一个不存在的有效区间。
            # 学习注释：另存真正用于查询的条件，避免把原请求范围悄悄改掉而无记录。
            effective = {}
        else:
            # 学习注释：lo、hi等于2项元组：start、end时取'full'，否则取'partial'；保存到coverage。
            coverage = "full" if (lo, hi) == (start, end) else "partial"
            # 学习注释：包含start、end的字典；保存到effective['time_range']。
            effective["time_range"] = {"start": lo, "end": hi}
            # 学习注释：只有coverage等于'partial'才进入这一分支。
            if coverage == "partial":
                # 学习注释：向需要随结果保留的说明（warnings）末尾添加把花括号内的变量填入f字符串，生成文字。
                warnings.append(f"仅查询数据范围内的 [{lo}, {hi})，其余区间没有覆盖")
    # 学习注释：返回包含requested_filters、effective_filters、coverage_status、warnings、time_semantics的字典，结束本次函数调用。
    return {
        "requested_filters": requested, "effective_filters": effective,
        "coverage_status": coverage, "warnings": warnings,
        "time_semantics": model.time_semantics,
    }


# 学习注释：用同一条用户记录上的组合条件求合格事件ID，作为向量检索硬过滤。
def eligible_event_ids(filters: dict, snapshot=None) -> list[str]:
    """先在同一 SQLite 快照求合格事件 ID，再作为向量检索硬过滤。

    record_start 的线路/时间条件落在同一用户记录，与 COUNT DISTINCT 模板一致；
    event_overlap 则明确定义为整个事件包络重叠。
    """
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（snapshot or versioning.current_snapshot()）；保存到本次请求固定的数据版本（snapshot）。
    snapshot = snapshot or versioning.current_snapshot()
    # 学习注释：用严格字段模型校验条件；非法键、错误日期或空实体会被拒绝。
    model = RetrievalFilters.model_validate(filters)
    # 学习注释：clauses保存固定SQL条件片段，params保存用户值，两者分开避免SQL注入。
    clauses, params = [], []
    # 学习注释：遍历2项元组：2项元组：model的line属性（line）、'线路名称'、2项元组：model的supply_center属性（supply_center）、'供服中心'，每轮把当前项交给value、column。
    for value, column in ((model.line, "线路名称"), (model.supply_center, "供服中心")):
        # 学习注释：只有value已经有值（不是None）才进入这一分支。
        if value is not None:
            # 学习注释：向待拼接的白名单条件（clauses）末尾添加把花括号内的变量填入f字符串，生成文字。
            clauses.append(f'u."{column}" = ?')
            # 学习注释：向参数字典（params）末尾添加value。
            params.append(value)
    # 学习注释：只有model.time_range为真/非空才进入这一分支。
    if model.time_range:
        # 学习注释：model的time_range属性（time_range）；保存到tr。
        tr = model.time_range
        # 学习注释：只有model.time_semantics等于TIME_RECORD_START才进入这一分支。
        if model.time_semantics == TIME_RECORD_START:
            # 学习注释：线路、中心和日期都作用在同一users行u上，避免同一事件不同记录被拼成假匹配。
            clauses.append("u.停电起始时间 >= ? AND u.停电起始时间 < ?")
            # 学习注释：把2项列表：tr的start属性（start）、tr的end属性（end）的各元素追加到参数字典（params）。
            params.extend([tr.start, tr.end])
        else:
            # 学习注释：向待拼接的白名单条件（clauses）末尾添加'u.事件序号 IN (SELECT 事件序号 FROM users GROUP BY 事件序号 HAVING…'。
            clauses.append(
                "u.事件序号 IN (SELECT 事件序号 FROM users GROUP BY 事件序号 "
                "HAVING MIN(NULLIF(停电起始时间, '')) < ? "
                "AND MAX(NULLIF(停电终止时间, '')) > ?)"
            )
            # 学习注释：把2项列表：tr的end属性（end）、tr的start属性（start）的各元素追加到参数字典（params）。
            params.extend([tr.end, tr.start])
    # 学习注释：最终只返回满足全部条件的唯一事件ID；向量检索随后用这些ID硬过滤。
    sql = "SELECT DISTINCT u.事件序号 FROM users u"
    # 学习注释：只有待拼接的白名单条件（clauses）为真/非空才进入这一分支。
    if clauses:
        # 学习注释：用计算 ' WHERE ' + ' AND '.join(clauses)更新待执行的SQL文本（sql）；下方+=等写法把计算结果存回原变量。
        sql += " WHERE " + " AND ".join(clauses)
    # 学习注释：用'ORDER BY u.事件序号'更新待执行的SQL文本（sql）；下方+=等写法把计算结果存回原变量。
    sql += " ORDER BY u.事件序号"
    # 学习注释：调用snapshot.connect；保存到数据库连接（conn）。
    conn = snapshot.connect()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：按已绑定条件读取合格事件ID，统一为字符串以匹配Milvus的event_id。
        return [str(row[0]) for row in conn.execute(sql, params)]
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()


# 学习注释：查找该事件对应的用户表行ID，建立从案例回到原表的路径。
def source_rows(event_id: str, snapshot=None) -> list[int]:
    # 学习注释：调用(snapshot or versioning.current_snapshot()).connect；保存到数据库连接（conn）。
    conn = (snapshot or versioning.current_snapshot()).connect()
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：为一个案例提取原始users行ID，供来源追溯；这些ID不是停电次数。
        return [int(row[0]) for row in conn.execute(
            "SELECT id FROM users WHERE 事件序号 = ? ORDER BY id", (event_id,)
        )]
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()
