# 阅读导航：筛选范围回归
# 本文件作用：用跨月、跨线路的合成记录检查同一行关联、日期口径和检索错误处理。
# 建议读法：先看conftest的E1到E4，再看每个assert为何对应预期结果。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest
# 学习注释：从app.core导入scope, similar, tools, versioning，复用对应模块的实现。
from app.core import scope, similar, tools, versioning
# 学习注释：从app.core.query_templates导入execute_query，复用对应模块的实现。
from app.core.query_templates import execute_query


# 学习注释：本测试验证：非法筛选应在Embedding前被拒绝，避免多余付费调用。
@pytest.mark.parametrize("filters", [
    {"time_range": "2025年6月"}, {"time_range": "2025-06-01~"},
    {"time_range": {"start": "2025-02-30", "end": "2025-03-02"}},
    {"time_range": {"start": "2025-07-01", "end": "2025-06-01"}},
    {"time_range": {"start": "2025-06-01"}},
    {"unknown": "x"}, {"line": ""}, {"line": "不存在"},
    {"time_semantics": "guess"}, {"supply_center": "不存在"},
    [], "",
])
async def test_invalid_filters_never_embed(dataset, fake_search, filters):
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError):
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续。
        await similar.search_similar("合成故障", filters=filters, with_signal=True)
    # 学习注释：调用fake_search.embed.assert_not_awaited。
    fake_search.embed.assert_not_awaited()
    # 学习注释：测试要求：不满足（fake_search.calls为真/非空）；不满足就失败。
    assert not fake_search.calls


# 学习注释：本测试验证：统计与检索使用同一用户行组合条件，不能跨记录拼出假匹配。
async def test_stat_and_retrieval_use_same_record_set(dataset, fake_search):
    # 学习注释：包含line、time_range的字典；保存到筛选条件（filters）。
    filters = {"line": "线路1", "time_range": {"start": "2025-06-01", "end": "2025-07-01"}}
    # 学习注释：execute_query的执行结果；保存到统计证据或文件属性（stat）。
    stat = execute_query("line_outage_count", {"line_name": "线路1"},
                         {"time_range": {"start_time": "2025-06-01", "end_time": "2025-07-01"}})
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", filters=filters, with_signal=True)
    # 学习注释：测试要求：stat['rows'][0]['停电次数']等于1；不满足就失败。
    assert stat["rows"][0]["停电次数"] == 1
    # 学习注释：测试要求：[e['事件序号'] for e in result['events']]等于1项列表：'E2'；不满足就失败。
    assert [e["事件序号"] for e in result["events"]] == ["E2"]
    # 学习注释：测试要求：result['trace']['eligible_event_ids']等于1项列表：'E2'；不满足就失败。
    assert result["trace"]["eligible_event_ids"] == ["E2"]
    # 学习注释：测试要求：fake_search.calls[0]['collection']等于dataset.snapshot的Milvus集合名属性（collection）；不满足就失败。
    assert fake_search.calls[0]["collection"] == dataset.snapshot.collection


# 学习注释：本测试验证：显式event_overlap口径可包含上月开始、本月仍持续的事件。
async def test_explicit_overlap_includes_cross_month_event(dataset, fake_search):
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", filters={
        "time_range": {"start": "2025-06-01", "end": "2025-06-02"},
        "time_semantics": "event_overlap"}, with_signal=True)
    # 学习注释：测试要求：{e['事件序号'] for e in result['events']}等于3项集合：'E1'、'E2'、'E3'；不满足就失败。
    assert {e["事件序号"] for e in result["events"]} == {"E1", "E2", "E3"}
    # 学习注释：测试要求：result['signal'].time_semantics等于'event_overlap'；不满足就失败。
    assert result["signal"].time_semantics == "event_overlap"


# 学习注释：本测试验证：范围外或没有合格事件时提前返回，不调用模型检索服务。
async def test_out_of_scope_and_empty_do_not_call_upstreams(dataset, fake_search):
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到outside。
    outside = await similar.search_similar("合成", filters={
        "time_range": {"start": "2024-01-01", "end": "2024-02-01"}}, with_signal=True)
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到empty。
    empty = await similar.search_similar("合成", filters={
        "line": "线路1", "time_range": {"start": "2025-01-01", "end": "2025-01-02"}},
        with_signal=True)
    # 学习注释：测试要求：outside['signal'].signal等于'out_of_scope'；不满足就失败。
    assert outside["signal"].signal == "out_of_scope"
    # 学习注释：测试要求：outside['signal'].effective_filters等于空字典；不满足就失败。
    assert outside["signal"].effective_filters == {}
    # 学习注释：测试要求：empty['signal'].signal等于'empty'；不满足就失败。
    assert empty["signal"].signal == "empty"
    # 学习注释：调用fake_search.embed.assert_not_awaited。
    fake_search.embed.assert_not_awaited()


# 学习注释：本测试验证：范围部分覆盖时同时保留原请求与实际交集。
async def test_partial_scope_preserves_requested_and_effective(dataset, fake_search):
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", filters={
        "time_range": {"start": "2025-12-15", "end": "2026-01-15"}}, with_signal=True)
    # 学习注释：result中键/位置为'signal'的值；保存到检索状态对象（signal）。
    signal = result["signal"]
    # 学习注释：测试要求：signal.coverage_status等于'partial'；不满足就失败。
    assert signal.coverage_status == "partial"
    # 学习注释：测试要求：signal.requested_filters['time_range']['end']等于'2026-01-15'；不满足就失败。
    assert signal.requested_filters["time_range"]["end"] == "2026-01-15"
    # 学习注释：测试要求：signal.effective_filters['time_range']['end']等于'2026-01-01'；不满足就失败。
    assert signal.effective_filters["time_range"]["end"] == "2026-01-01"
    # 学习注释：测试要求：signal.warnings为真/非空；不满足就失败。
    assert signal.warnings


# 学习注释：本测试验证：重排返回非法索引、重复或无效分数时，明确报告处理错误。
@pytest.mark.parametrize("ranked", [[], [(-1, .7)], [(99, .7)], [(0, .7), (0, .6)], [(0, float("nan"))]])
async def test_invalid_rerank_is_processing_error(dataset, fake_search, monkeypatch, ranked):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.rerank, "rerank", AsyncMock(return_value=ranked))
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：result['signal'].signal等于'processing_error'；不满足就失败。
    assert result["signal"].signal == "processing_error"
    # 学习注释：测试要求：result['signal'].candidate_count等于4；不满足就失败。
    assert result["signal"].candidate_count == 4
    # 学习注释：测试要求：result['trace']['error_stage']等于'rerank'；不满足就失败。
    assert result["trace"]["error_stage"] == "rerank"
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(similar.RetrievalError):
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续。
        await similar.search_similar("合成")


# 学习注释：本测试验证：有召回但重排为空时，工具不能声称范围内没有事件。
async def test_empty_rerank_tool_does_not_claim_no_events(dataset, fake_search, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.rerank, "rerank", AsyncMock(return_value=[]))
    # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到msg。
    msg = await tools.search_similar_events.ainvoke({
        "type": "tool_call", "id": "c1", "name": "search_similar_events",
        "args": {"query": "合成"},
    })
    # 学习注释：测试要求：msg.artifact['status']等于'processing_error'；不满足就失败。
    assert msg.artifact["status"] == "processing_error"
    # 学习注释：测试要求：'未完成'包含在msg的content属性（content）；不满足就失败。
    assert "未完成" in msg.content
    # 学习注释：测试要求：'没有与描述相似'不包含在msg的content属性（content）；不满足就失败。
    assert "没有与描述相似" not in msg.content


# 学习注释：本测试验证：正常返回仍可带属性冲突说明，不能把冲突与服务失败混为一谈。
async def test_conflict_is_independent_of_success(dataset, fake_search):
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：result['signal'].signal尚未设置（None）；不满足就失败。
    assert result["signal"].signal is None
    # 学习注释：测试要求：result['signal'].has_conflict为真/非空；不满足就失败。
    assert result["signal"].has_conflict
    # 学习注释：测试要求：result['signal'].conflicts等于1项列表：包含event_id、fields的字典；不满足就失败。
    assert result["signal"].conflicts == [{"event_id": "E3", "fields": "停电性质、供服中心"}]


# 学习注释：本测试验证：空问题或查询/建库向量模型不匹配，都不能假装成正常空检索。
async def test_blank_query_and_model_mismatch_are_not_empty(dataset, fake_search, monkeypatch):
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError):
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续。
        await similar.search_similar(" ", with_signal=True)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.settings, "embed_model", "another-model")
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：result['signal'].signal等于'processing_error'；不满足就失败。
    assert result["signal"].signal == "processing_error"
    # 学习注释：调用fake_search.embed.assert_not_awaited。
    fake_search.embed.assert_not_awaited()
