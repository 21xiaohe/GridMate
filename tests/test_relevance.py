# 阅读导航：依据核对回归
# 本文件作用：验证引用与索引合法性、保留正例、歧义扣留、失败信号及评测器误拒绝检查。
# 建议读法：注入的模型结果是确定性替身，验证程序规则，不评估真实模型理解能力。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest

# 学习注释：从app.core导入evidence, relevance, similar, tools，复用对应模块的实现。
from app.core import evidence, relevance, similar, tools


# 学习注释：函数event接收eid、文本（text）；把这一段可复用处理封装起来。
def event(eid="X", text="合成电缆故障引起停电"):
    # 学习注释：返回包含事件序号、事件文本的字典，结束本次函数调用。
    return {"事件序号": eid, "事件文本": text}


# 学习注释：函数decision接收index、status、模型提供的原文片段（quotes）；把这一段可复用处理封装起来。
def decision(index=0, status="supported", quotes=None):
    # 学习注释：返回包含index、decision、quotes、reason的字典，结束本次函数调用。
    return {"index": index, "decision": status, "quotes": quotes or ["电缆故障引起停电"], "reason": "合成判断"}


# 学习注释：本测试验证：模型核对返回索引遗漏、重复、越界或非法时，不得应用到候选。
@pytest.mark.parametrize("items", [[], [decision(1)], [decision(), decision()], [decision(-1)]])
def test_invalid_index_set_cannot_be_applied(items):
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError):
        # 学习注释：relevance.validate_review的执行结果。
        relevance.validate_review({"items": items}, [event()])


# 学习注释：本测试验证：缺引用、编造引用、过短引用或混入无效引用，都不能按supported放行。
@pytest.mark.parametrize("quotes", [[], ["不存在的故障依据"], ["故障"], ["电缆故障引起停电", "不存在的另一段"]])
def test_unsupported_quote_cannot_pass(quotes):
    # 学习注释：调用decision；保存到d。
    d = decision()
    # 学习注释：模型提供的原文片段（quotes）；保存到d['quotes']。
    d["quotes"] = quotes
    # 学习注释：relevance.validate_review的执行结果；保存到out。
    out = relevance.validate_review({"items": [d]}, [event()])
    # 学习注释：测试要求：out[0]['decision']等于'uncertain'，并且out[0]['quotes']等于空列表；不满足就失败。
    assert out[0]["decision"] == "uncertain" and out[0]["quotes"] == []


# 学习注释：本测试验证：合法引用和不确定判定保留，同时标明模型初审、未人工确认。
def test_valid_quote_and_uncertainty_preserve_provenance():
    # 学习注释：relevance.validate_review的执行结果；保存到out。
    out = relevance.validate_review({"items": [decision(), decision(1, "uncertain")]}, [event(), event("Y")])
    # 学习注释：测试要求：out[0]['decision']等于'supported'，并且out[1]['decision']等于'uncertain'；不满足就失败。
    assert out[0]["decision"] == "supported" and out[1]["decision"] == "uncertain"
    # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
    assert all(r["human_validated"] is False for r in out)


# 学习注释：本测试验证：范围内只有无关候选时返回依据不足，不为了凑数量扩大范围。
async def test_low_candidate_count_does_not_force_irrelevant_hit(dataset, fake_search, monkeypatch):
    # 学习注释：异步函数reject接收用户查询文本（query）、事件列表（events）；把这一段可复用处理封装起来。
    async def reject(query, events):
        # 学习注释：返回遍历事件列表（events），将{'event_id': e['事件序号'], 'decision': 'unsupported', 'reason': '只有施工', …收集为列表，结束本次函数调用。
        return [{"event_id": e["事件序号"], "decision": "unsupported", "reason": "只有施工", "quotes": []} for e in events]
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.relevance, "review_candidates", reject)
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("电缆故障", filters={"line": "线路1",
        "time_range": {"start": "2025-06-01", "end": "2025-07-01"}}, with_signal=True)
    # 学习注释：测试要求：result['events']等于空列表，并且result['signal'].signal等于'insufficient_evidence'；不满足就失败。
    assert result["events"] == [] and result["signal"].signal == "insufficient_evidence"
    # 学习注释：测试要求：result['signal'].candidate_count等于1，并且result['signal'].unsupported_count等于1；不满足就失败。
    assert result["signal"].candidate_count == 1 and result["signal"].unsupported_count == 1
    # 学习注释：测试要求：len(fake_search.calls)等于1；不满足就失败。
    assert len(fake_search.calls) == 1
    # 学习注释：测试要求：result['trace']['eligible_event_ids']等于1项列表：'E2'；不满足就失败。
    assert result["trace"]["eligible_event_ids"] == ["E2"]
    # 学习注释：测试要求：result['trace']['relevance_review'][0]['event_id']等于'E2'；不满足就失败。
    assert result["trace"]["relevance_review"][0]["event_id"] == "E2"


# 学习注释：本测试验证：扣留不确定候选并提示数量，同时保留有支持案例。
async def test_uncertain_is_withheld_without_discarding_valid_cases(dataset, fake_search, monkeypatch):
    # 学习注释：异步函数review接收用户查询文本（query）、事件列表（events）；把这一段可复用处理封装起来。
    async def review(query, events):
        # 学习注释：返回遍历enumerate(events)，将{'event_id': e['事件序号'], 'decision': 'uncertain' if i else 'supported'…收集为列表，结束本次函数调用。
        return [{"event_id": e["事件序号"], "decision": "uncertain" if i else "supported",
                 "reason": "混合记录", "quotes": [e["事件文本"]]} for i, e in enumerate(events)]
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.relevance, "review_candidates", review)
    # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到msg。
    msg = await tools.search_similar_events.ainvoke({"type": "tool_call", "id": "x",
        "name": "search_similar_events", "args": {"query": "合成", "top_k": 4}})
    # 学习注释：测试要求：len(msg.artifact['cases'])等于1；不满足就失败。
    assert len(msg.artifact["cases"]) == 1
    # 学习注释：测试要求：msg.artifact['signal']['uncertain_count']等于3；不满足就失败。
    assert msg.artifact["signal"]["uncertain_count"] == 3
    # 学习注释：测试要求：'3条含义待确认'包含在msg的content属性（content）；不满足就失败。
    assert "3条含义待确认" in msg.content
    # 学习注释：测试要求：msg.artifact['cases'][0]['supporting_quotes']为真/非空；不满足就失败。
    assert msg.artifact["cases"][0]["supporting_quotes"]


# 学习注释：本测试验证：依据核对超时记为处理失败，不能当成没有相似事件。
async def test_review_failure_is_processing_error_not_no_match(dataset, fake_search, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.relevance, "review_candidates", AsyncMock(side_effect=TimeoutError()))
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：result['signal'].signal等于'processing_error'；不满足就失败。
    assert result["signal"].signal == "processing_error"
    # 学习注释：测试要求：result['trace']['error_stage']等于'relevance'；不满足就失败。
    assert result["trace"]["error_stage"] == "relevance"
    # 学习注释：测试要求：不满足（result['events']为真/非空）；不满足就失败。
    assert not result["events"]
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(similar.RetrievalError):
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续。
        await similar.search_similar("合成")


# 学习注释：本测试验证：全部候选有支持时保留重排顺序和原文，不改写证据。
async def test_all_supported_retains_order_and_original_text(dataset, fake_search):
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
    result = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：result['trace']['reranked_ids']等于result['trace']中键/位置为'returned_ids'的值；不满足就失败。
    assert result["trace"]["reranked_ids"] == result["trace"]["returned_ids"]
    # 学习注释：测试要求：result['events'][0]['事件文本']等于dataset.corpus[0]中键/位置为'事件文本'的值；不满足就失败。
    assert result["events"][0]["事件文本"] == dataset.corpus[0]["事件文本"]


# 学习注释：本测试验证：已知正例被错误拦下，评测必须失败，防止靠全部拒绝美化结果。
async def test_rejection_of_known_positive_fails_evaluation(dataset, fake_search, monkeypatch):
    # 学习注释：从scripts.query导入eval_fixed_set并简称evaluation，复用对应模块的实现。
    from scripts.query import eval_fixed_set as evaluation
    # 学习注释：异步函数reject接收用户查询文本（query）、事件列表（events）；把这一段可复用处理封装起来。
    async def reject(query, events):
        # 学习注释：返回遍历事件列表（events），将{'event_id': e['事件序号'], 'decision': 'unsupported', 'reason': '注入误拒', …收集为列表，结束本次函数调用。
        return [{"event_id": e["事件序号"], "decision": "unsupported", "reason": "注入误拒", "quotes": []} for e in events]
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.relevance, "review_candidates", reject)
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one({"id": "T", "query": "合成", "filters": None, "expect": "hit"},
        {"合成": {"E1": 1}})
    # 学习注释：测试要求：result['status']等于'fail'，并且result['known_positive_withheld']等于1项列表：'E1'；不满足就失败。
    assert result["status"] == "fail" and result["known_positive_withheld"] == ["E1"]


# 学习注释：本测试验证：依据不足题只有已知负例才通过；误拒正例失败，歧义标签待验证。
@pytest.mark.parametrize("label,expected", [(0,"pass"), (1,"fail"), (None,"unverified")])
async def test_no_support_expectation_requires_negative_labels(dataset, fake_search, monkeypatch, label, expected):
    # 学习注释：从scripts.query导入eval_fixed_set并简称evaluation，复用对应模块的实现。
    from scripts.query import eval_fixed_set as evaluation
    # 学习注释：异步函数reject接收用户查询文本（query）、事件列表（events）；把这一段可复用处理封装起来。
    async def reject(query, events):
        # 学习注释：返回遍历事件列表（events），将{'event_id': e['事件序号'], 'decision': 'unsupported', 'reason': '注入拒绝', …收集为列表，结束本次函数调用。
        return [{"event_id": e["事件序号"], "decision": "unsupported", "reason": "注入拒绝", "quotes": []} for e in events]
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.relevance, "review_candidates", reject)
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one({"id": "T", "query": "合成", "filters": None, "expect": "insufficient_evidence"},
        {"合成": {"E1": label}}, top_k=1)
    # 学习注释：测试要求：result['status']等于期望状态（expected）；不满足就失败。
    assert result["status"] == expected
