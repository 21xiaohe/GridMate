# 阅读导航：模型与业务代码的接口
# 本文件作用：把统计和检索函数注册成模型可选的工具；模型决定调用，Python真正执行查询。
# 建议读法：每个工具返回两份同源内容：content给模型阅读，artifact给程序保留结构化证据。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""Agent 工具：文本用于生成，artifact 保留同一份结构化证据供自检和 API。"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：从typing引入Literal，用于类型提示，不负责网络调用或业务计算。
from typing import Literal
# 学习注释：从langchain_core.tools引入tool，用于将Python函数包装成模型可调用的工具。
from langchain_core.tools import tool
# 学习注释：从pydantic引入BaseModel, ConfigDict, Field, field_validator，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, ConfigDict, Field, field_validator

# 学习注释：从app.core导入ask并简称ask_mod，复用对应模块的实现。
from app.core import ask as ask_mod
# 学习注释：从app.core导入evidence并简称evidence_mod，复用对应模块的实现。
from app.core import evidence as evidence_mod
# 学习注释：从app.core导入similar, versioning, scope，复用对应模块的实现。
from app.core import similar, versioning, scope

# 学习注释：包含line_outage_count、supply_center_energy、time_range_outage_count、outage_type_ratio的字典；保存到_METRIC_BY_TEMPLATE。
_METRIC_BY_TEMPLATE = {
    "line_outage_count": "停电次数", "supply_center_energy": "缺供电量总和",
    "time_range_outage_count": "停电事件数", "outage_type_ratio": "停电性质占比",
}
# 学习注释：包含line_outage_count、supply_center_energy、time_range_outage_count、outage_type_ratio的字典；保存到_UNIT_BY_TEMPLATE。
_UNIT_BY_TEMPLATE = {
    "line_outage_count": "次", "supply_center_energy": "千瓦时",
    "time_range_outage_count": "次", "outage_type_ratio": "%",
}


# 学习注释：OutageStatInput定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class OutageStatInput(BaseModel):
    # 学习注释：设置Pydantic的字段检查策略，例如拒绝未知字段；保存到model_config。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # 学习注释：声明字段：完整统计问题，包含用户指定的线路、供服中心及时间；保存到完整统计问题（question）。
    question: str = Field(min_length=1, description="完整统计问题，包含用户指定的线路、供服中心及时间")


# 学习注释：注册统计工具，并声明参数结构；content_and_artifact让文本与结构化证据一起返回。
@tool(args_schema=OutageStatInput, response_format="content_and_artifact")
# 学习注释：装饰器：调用此函数时复用或固定当前数据快照，不在装饰时执行数据库查询。
@versioning.pinned_dataset
async def query_outage_stat(question: str) -> tuple[str, dict]:
    """查询停电次数、缺供电量或分类占比。用白名单 SQL 计算数值，返回统计口径与证据。"""
    # 学习注释：把完整统计问题交给解析与SQL模块；工具包装层本身不重新计算数字。
    result = await ask_mod.ask(question)
    # 学习注释：versioning.evidence_context的执行结果；保存到context。
    context = versioning.evidence_context()
    # 学习注释：只有result.get('status')不等于'ok'才进入这一分支。
    if result.get("status") != "ok":
        # 学习注释：返回evidence_mod.transport的执行结果，结束本次函数调用。
        return evidence_mod.transport(evidence_mod.ToolEvidence(
            **context, status=result.get("status", "error"),
            message=result.get("message", "未得出统计结果"),
        ))
    # 学习注释：2项元组：从result读取键'evidence'，缺少时取空字典、从result读取键'result'，缺少时取空字典；保存到ev、values。
    ev, values = result.get("evidence", {}), result.get("result", {})
    # 学习注释：只把StatEvidence声明过的字段传入，避免多余字段污染证据对象。
    data = {key: value for key, value in ev.items() if key in evidence_mod.StatEvidence.model_fields}
    # 学习注释：将新字段合并到data，同名字段会覆盖旧值。
    data.update(
        metric=_METRIC_BY_TEMPLATE.get(ev.get("template_id")),
        unit=_UNIT_BY_TEMPLATE.get(ev.get("template_id")),
        rows=values.get("rows", []), total_events=values.get("total_events"),
        multi_label_events=values.get("multi_label_events"),
    )
    # 学习注释：调用evidence_mod.StatEvidence；保存到统计证据或文件属性（stat）。
    stat = evidence_mod.StatEvidence(**data)
    # 学习注释：只有从ev读取键'note'，缺少时取None才进入这一分支。
    if ev.get("note"):
        # 学习注释：向stat.warnings末尾添加ev中键/位置为'note'的值。
        stat.warnings.append(ev["note"])
    # 学习注释：返回evidence_mod.transport的执行结果，结束本次函数调用。
    return evidence_mod.transport(evidence_mod.ToolEvidence(
        **context, message=result["message"], stat=stat,
        requested_filters=stat.requested_filters, effective_filters=stat.effective_filters,
        coverage_status=stat.coverage_status, time_semantics=stat.time_semantics,
    ))


# 学习注释：SimilarEventsInput定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class SimilarEventsInput(BaseModel):
    # 学习注释：设置Pydantic的字段检查策略，例如拒绝未知字段；保存到model_config。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # 学习注释：声明字段：描述需要查找的停电现象或故障；保存到用户查询文本（query）。
    query: str = Field(min_length=1, description="描述需要查找的停电现象或故障")
    # 学习注释：模型工具默认最多展示3条，允许1～10；底层检索函数的上限是50，两个入口约束不同。
    top_k: int = Field(default=3, ge=1, le=10)
    # 学习注释：声明字段：用户指定的线路假名；保存到line。
    line: str | None = Field(default=None, description="用户指定的线路假名")
    # 学习注释：声明字段：用户指定的供服中心假名；保存到supply_center。
    supply_center: str | None = Field(default=None, description="用户指定的供服中心假名")
    # 学习注释：声明字段：起止日期 start/end，YYYY-MM-DD，左闭右开；缺任一端则不合法；保存到time_range。
    time_range: scope.DateRange | None = Field(
        default=None, description="起止日期 start/end，YYYY-MM-DD，左闭右开；缺任一端则不合法")
    # 学习注释：声明字段：record_start 与统计一致；仅查询跨区间持续的事件时显式使用 event_overlap；保存到时间筛选口径（time_semantics）。
    time_semantics: Literal["record_start", "event_overlap"] = Field(
        default="record_start",
        description="record_start 与统计一致；仅查询跨区间持续的事件时显式使用 event_overlap")

    # 学习注释：函数parse_legacy_range接收value；把这一段可复用处理封装起来。
    # 学习注释：Pydantic字段校验装饰器：解析指定字段时调用下方函数检查或规范化。
    @field_validator("time_range", mode="before")
    # 学习注释：类方法接收cls（当前类）作为首参，不需要先创建一个实例。
    @classmethod
    def parse_legacy_range(cls, value):
        # 学习注释：返回调用scope.RetrievalFilters.legacy_range，处理value，结束本次函数调用。
        return scope.RetrievalFilters.legacy_range(value)


# 学习注释：注册检索工具，模型必须按schema传入描述、数量及用户指定的筛选条件。
@tool(args_schema=SimilarEventsInput, response_format="content_and_artifact")
# 学习注释：装饰器：调用此函数时复用或固定当前数据快照，不在装饰时执行数据库查询。
@versioning.pinned_dataset
async def search_similar_events(query: str, top_k: int = 3, line: str | None = None,
                                time_range: scope.DateRange | None = None,
                                supply_center: str | None = None,
                                time_semantics: str = "record_start") -> tuple[str, dict]:
    """查找相似停电案例。用户给出的线路、供服中心和时间必须传入过滤参数。

    给统计结果举例时使用相同过滤和默认 record_start；不能用少量相似案例推算统计次数。
    """
    # 学习注释：包含time_semantics的字典；保存到筛选条件（filters）。
    filters = {"time_semantics": time_semantics}
    # 学习注释：只有line已经有值（不是None）才进入这一分支。
    if line is not None:
        # 学习注释：line；保存到filters['line']。
        filters["line"] = line
    # 学习注释：只有supply_center已经有值（不是None）才进入这一分支。
    if supply_center is not None:
        # 学习注释：supply_center；保存到filters['supply_center']。
        filters["supply_center"] = supply_center
    # 学习注释：只有time_range已经有值（不是None）才进入这一分支。
    if time_range is not None:
        # 学习注释：检查time_range是否属于scope.DateRange类型时取把time_range转换为普通字典；mode=json时使用可序列化值，否则取time_range；保存到filters['time_range']。
        filters["time_range"] = (time_range.model_dump() if isinstance(time_range, scope.DateRange)
                                 else time_range)
    # 学习注释：versioning.evidence_context的执行结果；保存到context。
    context = versioning.evidence_context()
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：执行范围过滤、混合召回、重排与依据核对，并取回signal和trace。
        result = await similar.search_similar(query, top_k, filters, with_signal=True)
    except ValueError as exc:
        # 学习注释：返回evidence_mod.transport的执行结果，结束本次函数调用。
        return evidence_mod.transport(evidence_mod.ToolEvidence(
            **context, status="invalid_filters", message=str(exc), requested_filters=filters,
        ))
    # 学习注释：result中键/位置为'signal'的值；保存到检索状态对象（signal）。
    signal = result["signal"]
    # 学习注释：空列表；保存到案例列表（cases）。
    cases = []
    # 学习注释：遍历result中键/位置为'events'的值，每轮把当前项交给当前事件（event）。
    for event in result["events"]:
        # 学习注释：把event中键/位置为'事件序号'的值转换为字符串；保存到eid。
        eid = str(event["事件序号"])
        # 学习注释：scope.source_rows的执行结果；保存到ids。
        ids = scope.source_rows(eid)
        # 学习注释：只有ids为空或为False才进入这一分支。
        if not ids:
            # 学习注释：返回evidence_mod.transport的执行结果，结束本次函数调用。
            return evidence_mod.transport(evidence_mod.ToolEvidence(
                **context, status="processing_error", message="检索事件在当前统计版本中没有来源记录，无法核实",
            ))
        # 学习注释：向案例列表（cases）末尾添加调用evidence_mod.CaseEvidence。
        cases.append(evidence_mod.CaseEvidence(
            **context, event_id=eid, source_table="users", source_row_ids=ids,
            requested_filters=signal.requested_filters, effective_filters=signal.effective_filters,
            coverage_status=signal.coverage_status, time_semantics=signal.time_semantics,
            attrs={"停电性质": event.get("停电性质"), "电压等级": event.get("电压等级"),
                   "线路": event.get("线路名称"), "供服中心": event.get("供服中心"),
                   "开始时间": event.get("起始时间"), "结束时间": event.get("终止时间")},
            text=event.get("事件文本", ""),
            outage_type_detail=event.get("停电性质明细"),
            type_conflict=bool(event.get("停电性质冲突")),
            supply_center_detail=event.get("供服中心明细"),
            center_conflict=bool(event.get("供服中心冲突")),
            score=event.get("_score"), rerank_score=event.get("_rerank_score"),
            # 学习注释：把核对阶段确认在正文存在的片段放进案例证据，不重新编写引用。
            supporting_quotes=event.get("_relevance", {}).get("quotes", []),
            relevance_status=event.get("_relevance", {}).get("decision"),
        ))
        # 学习注释：转为可逐项访问的列表：signal的需要随结果保留的说明属性（warnings）；保存到cases[-1].warnings。
        cases[-1].warnings = list(signal.warnings)
    # 学习注释：返回evidence_mod.transport的执行结果，结束本次函数调用。
    return evidence_mod.transport(evidence_mod.ToolEvidence(
        **context, status=signal.signal or "ok", cases=cases, signal=signal,
        requested_filters=signal.requested_filters, effective_filters=signal.effective_filters,
        coverage_status=signal.coverage_status, time_semantics=signal.time_semantics,
        retrieval_trace=result["trace"],
        message=("以下案例经模型初审且保留支持原文；分数不等于相关概率，初审不替代人工确认。" if cases
                 else signal.scope_detail or "本次未得到可核实的案例。"),
    ))


# 学习注释：'检索参数格式错误。请提供完整有效的日期范围、非空描述和合法返回条数；本次未执行检索。'；保存到search_similar_events.handle_validation_error。
search_similar_events.handle_validation_error = (
    "检索参数格式错误。请提供完整有效的日期范围、非空描述和合法返回条数；本次未执行检索。"
)
# 学习注释：2项列表：query_outage_stat、search_similar_events；保存到AGENT_TOOLS。
AGENT_TOOLS = [query_outage_stat, search_similar_events]
