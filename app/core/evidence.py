# 阅读导航：统一证据结构
# 本文件作用：用Pydantic定义统计、案例和检索状态；生成回答、自检与API读取同一份证据。
# 建议读法：类声明字段，render_*负责格式化文本，transport同时返回文本和JSON兼容字典。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""统一证据对象、工具 artifact 与确定性渲染。生成、自检、API 共用同一份证据。"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从typing引入Any，用于类型提示，不负责网络调用或业务计算。
from typing import Any
# 学习注释：从pydantic引入BaseModel, ConfigDict, Field，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, ConfigDict, Field
# 学习注释：从app.core.scope导入TIME_LABELS，复用对应模块的实现。
from app.core.scope import TIME_LABELS

# 学习注释：'full'；保存到COVERAGE_FULL。
COVERAGE_FULL = "full"
# 学习注释：'partial'；保存到COVERAGE_PARTIAL。
COVERAGE_PARTIAL = "partial"
# 学习注释：'out'；保存到COVERAGE_OUT。
COVERAGE_OUT = "out"
# 学习注释：'none'；保存到COVERAGE_NONE。
COVERAGE_NONE = "none"
# 学习注释：'empty'；保存到SIGNAL_EMPTY。
SIGNAL_EMPTY = "empty"
# 学习注释：'out_of_scope'；保存到SIGNAL_OUT_OF_SCOPE。
SIGNAL_OUT_OF_SCOPE = "out_of_scope"
# 学习注释：'processing_error'；保存到SIGNAL_PROCESSING_ERROR。
SIGNAL_PROCESSING_ERROR = "processing_error"
# 学习注释：'insufficient_evidence'；保存到SIGNAL_INSUFFICIENT。
SIGNAL_INSUFFICIENT = "insufficient_evidence"


# 学习注释：EvidenceBase定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class EvidenceBase(BaseModel):
    # 学习注释：设置Pydantic的字段检查策略，例如拒绝未知字段；保存到model_config。
    model_config = ConfigDict(extra="forbid")
    # 学习注释：None（尚无值）；保存到本轮请求标识（request_id）。
    request_id: str | None = None
    # 学习注释：None（尚无值）；保存到数据版本号（dataset_version）。
    dataset_version: str | None = None
    # 学习注释：'legacy_unverified'；保存到整套产物校验状态（data_consistency）。
    data_consistency: str = "legacy_unverified"
    # 学习注释：保存用户要求的条件；与effective_filters对照可解释范围裁剪。
    requested_filters: dict[str, Any] = Field(default_factory=dict)
    # 学习注释：保存实际用于查询的条件，不能只展示原请求而隐藏数据未覆盖部分。
    effective_filters: dict[str, Any] = Field(default_factory=dict)
    # 学习注释：COVERAGE_NONE；保存到查询区间与数据范围的关系（coverage_status）。
    coverage_status: str = COVERAGE_NONE
    # 学习注释：None（尚无值）；保存到数据覆盖范围的声明依据（coverage_basis）。
    coverage_basis: str | None = None
    # 学习注释：'record_start'；保存到时间筛选口径（time_semantics）。
    time_semantics: str = "record_start"
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到需要随结果保留的说明（warnings）。
    warnings: list[str] = Field(default_factory=list)


# 学习注释：StatEvidence定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class StatEvidence(EvidenceBase):
    # 学习注释：None（尚无值）；保存到统计指标名称（metric）。
    metric: str | None = None
    # 学习注释：None（尚无值）；保存到计量单位（unit）。
    unit: str | None = None
    # 学习注释：None（尚无值）；保存到SQL模板标识（template_id）。
    template_id: str | None = None
    # 学习注释：None（尚无值）；保存到SQL模板用途（template_desc）。
    template_desc: str | None = None
    # 学习注释：None（尚无值）；保存到事件或用户等统计粒度（granularity）。
    granularity: str | None = None
    # 学习注释：None（尚无值）；保存到数据来源表（source_table）。
    source_table: str | None = None
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到SQL模板参数（query_params）。
    query_params: dict[str, Any] = Field(default_factory=dict)
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到SQL附加筛选参数（query_filters）。
    query_filters: dict[str, Any] = Field(default_factory=dict)
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到查询或表格行（rows）。
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # 学习注释：None（尚无值）；保存到去重后的事件总数（total_events）。
    total_events: int | None = None
    # 学习注释：None（尚无值）；保存到同时属于多个性质的事件数（multi_label_events）。
    multi_label_events: int | None = None


# 学习注释：CaseEvidence定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class CaseEvidence(EvidenceBase):
    # 学习注释：None（尚无值）；保存到事件标识（event_id）。
    event_id: str | None = None
    # 学习注释：None（尚无值）；保存到数据来源表（source_table）。
    source_table: str | None = None
    # 学习注释：保存案例对应的users原始行ID，用于追溯；列表长度不是停电事件数。
    source_row_ids: list[int] = Field(default_factory=list)
    # 学习注释：None（尚无值）；保存到文本（text）。
    text: str | None = None
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到事件结构化属性（attrs）。
    attrs: dict[str, Any] = Field(default_factory=dict)
    # 学习注释：None（尚无值）；保存到停电性质明细（outage_type_detail）。
    outage_type_detail: str | None = None
    # 学习注释：False（不成立）；保存到停电性质是否存在多种归类（type_conflict）。
    type_conflict: bool = False
    # 学习注释：None（尚无值）；保存到供服中心明细（supply_center_detail）。
    supply_center_detail: str | None = None
    # 学习注释：False（不成立）；保存到供服中心是否存在多种归类（center_conflict）。
    center_conflict: bool = False
    # 学习注释：None（尚无值）；保存到召回阶段分数（score）。
    score: float | None = None
    # 学习注释：None（尚无值）；保存到未经校准的重排分（rerank_score）。
    rerank_score: float | None = None
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到在原文中找到的支持片段（supporting_quotes）。
    supporting_quotes: list[str] = Field(default_factory=list)
    # 学习注释：None（尚无值）；保存到候选依据判定状态（relevance_status）。
    relevance_status: str | None = None


# 学习注释：RetrievalSignal定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class RetrievalSignal(EvidenceBase):
    # 学习注释：None（尚无值）；保存到检索状态对象（signal）。
    signal: str | None = None
    # 学习注释：False（不成立）；保存到has_conflict。
    has_conflict: bool = False
    # 学习注释：0；保存到重排前召回候选数量（candidate_count）。
    candidate_count: int = 0
    # 学习注释：None（尚无值）；保存到top_rerank_score。
    top_rerank_score: float | None = None
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到conflicts。
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    # 学习注释：None（尚无值）；保存到scope_detail。
    scope_detail: str | None = None
    # 学习注释：0；保存到实际核对的候选数量（reviewed_count）。
    reviewed_count: int = 0
    # 学习注释：0；保存到被判缺少支持的数量（unsupported_count）。
    unsupported_count: int = 0
    # 学习注释：0；保存到被判含义待确认的数量（uncertain_count）。
    uncertain_count: int = 0


# 学习注释：ToolEvidence定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class ToolEvidence(EvidenceBase):
    # 学习注释：'ok'；保存到status。
    status: str = "ok"
    # 学习注释：''；保存到message。
    message: str = ""
    # 学习注释：统计工具有值时放StatEvidence；纯检索工具可以为None。
    stat: StatEvidence | None = None
    # 学习注释：默认每个工具结果都创建独立案例列表，避免多个响应共享可变对象。
    cases: list[CaseEvidence] = Field(default_factory=list)
    # 学习注释：检索信号包含数量和失败/范围信息；统计工具可没有此字段。
    signal: RetrievalSignal | None = None
    # 学习注释：为每个新对象分别创建默认容器，避免共享可变列表/字典；保存到retrieval_trace。
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)


# 学习注释：函数_json接收value；把这一段可复用处理封装起来。
def _json(value) -> str:
    # 学习注释：返回把Python对象编码成JSON文本，结束本次函数调用。
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# 学习注释：函数_common接收ev；把这一段可复用处理封装起来。
def _common(ev: EvidenceBase) -> list[str]:
    # 学习注释：空列表；保存到parts。
    parts = []
    # 学习注释：遍历4项元组：2项元组：'请求标识'、ev的本轮请求标识属性（request_id）、2项元组：'数据版本'、ev的数据版本号属性（dataset_version）、2项元组：'版本校验'、ev的整套产物校验状态…，每轮把当前项交给label、value。
    for label, value in (("请求标识", ev.request_id), ("数据版本", ev.dataset_version),
                         ("版本校验", ev.data_consistency), ("数据范围依据", ev.coverage_basis)):
        # 学习注释：只有value为真/非空才进入这一分支。
        if value:
            # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
            parts.append(f"[{label}] {value}")
    # 学习注释：只有ev.requested_filters为真/非空才进入这一分支。
    if ev.requested_filters:
        # 学习注释：向parts末尾添加计算 '[请求范围] ' + _json(ev.requested_filters)。
        parts.append("[请求范围] " + _json(ev.requested_filters))
    # 学习注释：只有ev.effective_filters为真/非空才进入这一分支。
    if ev.effective_filters:
        # 学习注释：向parts末尾添加计算 '[实际范围] ' + _json(ev.effective_filters)。
        parts.append("[实际范围] " + _json(ev.effective_filters))
    # 学习注释：只有'time_range'包含在ev的数据实际覆盖的范围属性（effective_filters）才进入这一分支。
    if "time_range" in ev.effective_filters:
        # 学习注释：向parts末尾添加计算 '[时间口径] ' + TIME_LABELS.get(ev.time_semantics, ev.time_semantics)。
        parts.append("[时间口径] " + TIME_LABELS.get(ev.time_semantics, ev.time_semantics))
    # 学习注释：只有ev.coverage_status不等于COVERAGE_OUT才进入这一分支。
    elif ev.coverage_status != COVERAGE_OUT:
        # 学习注释：向parts末尾添加'[时间过滤] 未指定时间条件，查询当前台账范围内的记录'。
        parts.append("[时间过滤] 未指定时间条件，查询当前台账范围内的记录")
    # 学习注释：只有ev.coverage_status不等于COVERAGE_NONE才进入这一分支。
    if ev.coverage_status != COVERAGE_NONE:
        # 学习注释：向parts末尾添加计算 '[覆盖状态] ' + ev.coverage_status。
        parts.append("[覆盖状态] " + ev.coverage_status)
    # 学习注释：把遍历ev.warnings，将'[告警] ' + warning收集为生成器的各元素追加到parts。
    parts.extend("[告警] " + warning for warning in ev.warnings)
    # 学习注释：返回parts，结束本次函数调用。
    return parts


# 学习注释：渲染统计值并明确来源、单位、分母和聚合粒度。
def render_stat(ev: StatEvidence) -> str:
    # 学习注释：调用_common，处理ev；保存到parts。
    parts = _common(ev)
    # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
    parts.append(f"[来源] {ev.source_table or '未知'}；模板={ev.template_id or '未知'}")
    # 学习注释：只有ev.metric为真/非空才进入这一分支。
    if ev.metric:
        # 学习注释：向parts末尾添加计算 f'[指标] {ev.metric}' + (f'（单位：{ev.unit}）' if ev.unit else '')。
        parts.append(f"[指标] {ev.metric}" + (f"（单位：{ev.unit}）" if ev.unit else ""))
    # 学习注释：只有ev.granularity为真/非空才进入这一分支。
    if ev.granularity:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[口径] {ev.granularity}")
    # 学习注释：只有ev.template_desc为真/非空才进入这一分支。
    if ev.template_desc:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[模板] {ev.template_desc}")
    # 学习注释：只有ev.template_id等于'supply_center_energy'才进入这一分支。
    if ev.template_id == "supply_center_energy":
        # 学习注释：向parts末尾添加'[聚合规则] 筛选后直接累加 users 用户行的缺供电量，不做事件去重；不能描述成事件去重后求和'。
        parts.append("[聚合规则] 筛选后直接累加 users 用户行的缺供电量，不做事件去重；不能描述成事件去重后求和")
    # 学习注释：只有ev.template_id等于'outage_type_ratio'才进入这一分支。
    if ev.template_id == "outage_type_ratio":
        # 学习注释：向parts末尾添加'[能力范围] 本模板只支持当前台账全量事件的多标签占比，不支持单标签重算或按线路/供服中心拆分占比'。
        parts.append("[能力范围] 本模板只支持当前台账全量事件的多标签占比，不支持单标签重算或按线路/供服中心拆分占比")
    # 学习注释：只有ev.total_events已经有值（不是None）才进入这一分支。
    if ev.total_events is not None:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[分母] 事件总数={ev.total_events}")
    # 学习注释：只有ev.multi_label_events已经有值（不是None）才进入这一分支。
    if ev.multi_label_events is not None:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[多标签事件数] {ev.multi_label_events}")
    # 学习注释：向parts末尾添加'[统计结果]'。
    parts.append("[统计结果]")
    # 学习注释：把遍历ev.rows，将' ' + _json(row)收集为生成器的各元素追加到parts。
    parts.extend("  " + _json(row) for row in ev.rows)
    # 学习注释：返回用指定分隔符连接各段文本，结束本次函数调用。
    return "\n".join(parts)


# 学习注释：渲染事件正文、源行ID、属性冲突、分数及支持片段。
def render_case(ev: CaseEvidence) -> str:
    # 学习注释：调用_common，处理ev；保存到parts。
    parts = _common(ev)
    # 学习注释：向parts末尾添加计算 f"[事件] {ev.event_id or '?'} | " + _json(ev.attrs)。
    parts.append(f"[事件] {ev.event_id or '?'} | " + _json(ev.attrs))
    # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
    parts.append(f"[来源] {ev.source_table or '未知'}；源记录ID={ev.source_row_ids}")
    # 学习注释：只有ev.text为真/非空才进入这一分支。
    if ev.text:
        # 学习注释：向parts末尾添加计算 '正文：' + ev.text。
        parts.append("正文：" + ev.text)
    # 学习注释：只有ev.outage_type_detail为真/非空才进入这一分支。
    if ev.outage_type_detail:
        # 学习注释：向parts末尾添加计算 '性质明细：' + ev.outage_type_detail。
        parts.append("性质明细：" + ev.outage_type_detail)
    # 学习注释：只有ev.supply_center_detail为真/非空才进入这一分支。
    if ev.supply_center_detail:
        # 学习注释：向parts末尾添加计算 '供服中心明细：' + ev.supply_center_detail。
        parts.append("供服中心明细：" + ev.supply_center_detail)
    # 学习注释：只有ev.type_conflict为真/非空才进入这一分支。
    if ev.type_conflict:
        # 学习注释：向parts末尾添加'[属性冲突] 停电性质归类存在冲突'。
        parts.append("[属性冲突] 停电性质归类存在冲突")
    # 学习注释：只有ev.center_conflict为真/非空才进入这一分支。
    if ev.center_conflict:
        # 学习注释：向parts末尾添加'[属性冲突] 供服中心归类存在冲突'。
        parts.append("[属性冲突] 供服中心归类存在冲突")
    # 学习注释：只有ev.rerank_score已经有值（不是None）才进入这一分支。
    if ev.rerank_score is not None:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[重排分] {ev.rerank_score:.3f}（未经校准，不等于概率）")
    # 学习注释：只有ev.relevance_status为真/非空才进入这一分支。
    if ev.relevance_status:
        # 学习注释：向parts末尾添加计算 '[相关性初审] ' + ev.relevance_status + '（模型判断，未经人工确认）'。
        parts.append("[相关性初审] " + ev.relevance_status + "（模型判断，未经人工确认）")
        # 学习注释：把遍历ev.supporting_quotes，将'[支持原文] ' + quote收集为生成器的各元素追加到parts。
        parts.extend("[支持原文] " + quote for quote in ev.supporting_quotes)
    # 学习注释：返回用指定分隔符连接各段文本，结束本次函数调用。
    return "\n".join(parts)


# 学习注释：渲染无候选/范围外/处理失败/依据不足，并显示候选与核对数量。
def render_signal(sig: RetrievalSignal) -> str:
    # 学习注释：调用_common，处理sig；保存到parts。
    parts = _common(sig)
    # 学习注释：包含展开字段的字典；保存到labels。
    labels = {SIGNAL_EMPTY: "无候选", SIGNAL_OUT_OF_SCOPE: "范围外",
              SIGNAL_PROCESSING_ERROR: "处理失败", SIGNAL_INSUFFICIENT: "依据不足"}
    # 学习注释：只有sig.signal为真/非空才进入这一分支。
    if sig.signal:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[{labels.get(sig.signal, sig.signal)}] {sig.scope_detail or ''}")
    # 学习注释：只有sig.has_conflict为真/非空才进入这一分支。
    if sig.has_conflict:
        # 学习注释：向parts末尾添加计算 '[属性冲突] ' + _json(sig.conflicts)。
        parts.append("[属性冲突] " + _json(sig.conflicts))
    # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
    parts.append(f"[召回候选数] {sig.candidate_count}")
    # 学习注释：只有sig.reviewed_count为真/非空才进入这一分支。
    if sig.reviewed_count:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[本次核对] {sig.reviewed_count}条；缺少支持{sig.unsupported_count}条；待确认{sig.uncertain_count}条")
    # 学习注释：只有sig.top_rerank_score已经有值（不是None）才进入这一分支。
    if sig.top_rerank_score is not None:
        # 学习注释：向parts末尾添加把花括号内的变量填入f字符串，生成文字。
        parts.append(f"[最高重排分] {sig.top_rerank_score:.3f}（未经校准，不据此硬判）")
    # 学习注释：返回用指定分隔符连接各段文本，结束本次函数调用。
    return "\n".join(parts)


# 学习注释：把状态、统计、案例和检索信号拼成模型能阅读的完整证据。
def render_bundle(bundle: ToolEvidence | dict) -> str:
    # 学习注释：按ToolEvidence的字段规则解析并校验输入对象；保存到一份完整工具证据（bundle）。
    bundle = ToolEvidence.model_validate(bundle)
    # 学习注释：2项列表：把花括号内的变量填入f字符串，生成文字、bundle的message属性（message）；保存到parts。
    parts = [f"[工具状态] {bundle.status}", bundle.message]
    # 学习注释：只有bundle.stat为真/非空才进入这一分支。
    if bundle.stat:
        # 学习注释：向parts末尾添加render_stat的执行结果。
        parts.append(render_stat(bundle.stat))
    # 学习注释：只有bundle.signal为真/非空才进入这一分支。
    if bundle.signal:
        # 学习注释：明确区分当前返回案例数、召回候选数与SQL统计总次数，避免模型混用。
        parts.append(f"[最终展示案例数] {len(bundle.cases)}（仅本次返回数量，不是召回候选数，也不是范围内停电总次数）")
    # 学习注释：把遍历bundle.cases，将render_case(case)收集为生成器的各元素追加到parts。
    parts.extend(render_case(case) for case in bundle.cases)
    # 学习注释：只有bundle.signal为真/非空才进入这一分支。
    if bundle.signal:
        # 学习注释：向parts末尾添加render_signal的执行结果。
        parts.append(render_signal(bundle.signal))
    # 学习注释：只有不满足（bundle.stat为真/非空），并且不满足（bundle.cases为真/非空），并且不满足（bundle.signal为真/非空）才进入这一分支。
    if not bundle.stat and not bundle.cases and not bundle.signal:
        # 学习注释：把调用_common，处理一份完整工具证据（bundle）的各元素追加到parts。
        parts.extend(_common(bundle))
    # 学习注释：返回用指定分隔符连接各段文本，结束本次函数调用。
    return "\n".join(part for part in parts if part)


# 学习注释：从同一证据对象同时生成可读文本和JSON兼容字典，减少两套内容漂移。
def transport(bundle: ToolEvidence) -> tuple[str, dict]:
    # 学习注释：同一对象先渲染给模型，再转字典给API；两份表达共享相同事实来源。
    return render_bundle(bundle), bundle.model_dump(mode="json")
