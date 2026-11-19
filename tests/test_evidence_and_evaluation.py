# 阅读导航：证据与评测回归
# 本文件作用：检查证据文本和结构化字段一致、生成失败处理、标签绑定与评测判定。
# 建议读法：assert是验收标准；测试通过证明这些约束成立，不代表任意模型回答都正确。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest
# 学习注释：从langchain_core.messages引入AIMessage，用于区分用户、助手和工具消息。
from langchain_core.messages import AIMessage
# 学习注释：从app.core导入agent, ask, evidence, faithfulness, similar, tools, versioning，复用对应模块的实现。
from app.core import agent, ask, evidence, faithfulness, similar, tools, versioning
# 学习注释：从scripts.query导入eval_fixed_set并简称evaluation，复用对应模块的实现。
from scripts.query import eval_fixed_set as evaluation


# 学习注释：函数intent_model接收pytest临时对象替换工具（monkeypatch）、intent；把这一段可复用处理封装起来。
def intent_model(monkeypatch, intent):
    # 学习注释：定义Model对象，集中保存下方属性和方法。
    class Model:
        # 学习注释：异步函数ainvoke接收prompt；把这一段可复用处理封装起来。
        async def ainvoke(self, prompt):
            # 学习注释：返回intent，结束本次函数调用。
            return intent
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(ask.llm, "structured", lambda *a, **kw: Model())


# 学习注释：本测试验证：统计与案例证据必须来自同一请求、同一数据版本。
async def test_stat_and_case_artifacts_share_request_and_version(dataset, fake_search, monkeypatch):
    # 学习注释：调用intent_model，处理pytest临时对象替换工具（monkeypatch）。
    intent_model(monkeypatch, ask._IntentParams(template="line_outage_count",
        line_name="线路1", start_time="2025-06-01", end_time="2025-07-01", confidence=1))
    # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
    with versioning.dataset_scope():
        # 学习注释：异步执行tools.query_outage_stat调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到统计证据或文件属性（stat）。
        stat = await tools.query_outage_stat.ainvoke({
            "type": "tool_call", "id": "s1", "name": "query_outage_stat",
            "args": {"question": "线路1 6月停了几次"}})
        # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到case。
        case = await tools.search_similar_events.ainvoke({
            "type": "tool_call", "id": "s2", "name": "search_similar_events",
            "args": {"query": "合成", "line": "线路1",
                     "time_range": {"start": "2025-06-01", "end": "2025-07-01"}}})
        # 学习注释：测试要求：stat.artifact['request_id']等于case.artifact中键/位置为'request_id'的值等于调用versioning.current_request_id；不满足就失败。
        assert stat.artifact["request_id"] == case.artifact["request_id"] == versioning.current_request_id()
    # 学习注释：测试要求：stat.artifact['dataset_version']等于case.artifact中键/位置为'dataset_version'的值等于'test_v1'；不满足就失败。
    assert stat.artifact["dataset_version"] == case.artifact["dataset_version"] == "test_v1"
    # 学习注释：测试要求：stat.artifact['stat']['rows']等于1项列表：包含停电次数的字典；不满足就失败。
    assert stat.artifact["stat"]["rows"] == [{"停电次数": 1}]
    # 学习注释：测试要求：stat.artifact['stat']['source_table']等于'users'；不满足就失败。
    assert stat.artifact["stat"]["source_table"] == "users"
    # 学习注释：测试要求：case.artifact['cases'][0]['event_id']等于'E2'；不满足就失败。
    assert case.artifact["cases"][0]["event_id"] == "E2"
    # 学习注释：测试要求：case.artifact['cases'][0]['source_row_ids']等于1项列表：2；不满足就失败。
    assert case.artifact["cases"][0]["source_row_ids"] == [2]
    # 学习注释：测试要求：case.artifact['cases'][0]['score']等于0.5；不满足就失败。
    assert case.artifact["cases"][0]["score"] == .5
    # 学习注释：测试要求：stat.content等于evidence.render_bundle的执行结果；不满足就失败。
    assert stat.content == evidence.render_bundle(stat.artifact)
    # 学习注释：测试要求：case.content等于evidence.render_bundle的执行结果；不满足就失败。
    assert case.content == evidence.render_bundle(case.artifact)
    # 学习注释：agent._extract_answer_and_tools的执行结果；保存到_、_、结果列表（results）。
    _, _, results = agent._extract_answer_and_tools([stat, case, AIMessage(content="合成回答")])
    # 学习注释：测试要求：results[0]['artifact']等于stat的artifact属性（artifact）；不满足就失败。
    assert results[0]["artifact"] == stat.artifact


# 学习注释：本测试验证：部分超出覆盖范围时，只统计交集并保留原请求范围。
async def test_partial_stat_executes_clipped_range(dataset, monkeypatch):
    # 学习注释：调用intent_model，处理pytest临时对象替换工具（monkeypatch）。
    intent_model(monkeypatch, ask._IntentParams(template="time_range_outage_count",
        start_time="2025-12-15", end_time="2026-01-15", confidence=1))
    # 学习注释：ask.ask的执行结果，await等待结果后再继续；保存到result。
    result = await ask.ask("跨年停电次数")
    # 学习注释：result中键/位置为'evidence'的值；保存到ev。
    ev = result["evidence"]
    # 学习注释：测试要求：ev['coverage_status']等于'partial'；不满足就失败。
    assert ev["coverage_status"] == "partial"
    # 学习注释：测试要求：ev['requested_filters']['time_range']['end_time']等于'2026-01-15'；不满足就失败。
    assert ev["requested_filters"]["time_range"]["end_time"] == "2026-01-15"
    # 学习注释：测试要求：ev['effective_filters']['time_range']['end_time']等于'2026-01-01'；不满足就失败。
    assert ev["effective_filters"]["time_range"]["end_time"] == "2026-01-01"
    # 学习注释：测试要求：ev['query_params']['end_time']等于'2026-01-01'；不满足就失败。
    assert ev["query_params"]["end_time"] == "2026-01-01"
    # 学习注释：测试要求：result['result']['rows']等于1项列表：包含停电次数的字典；不满足就失败。
    assert result["result"]["rows"] == [{"停电次数": 1}]


# 学习注释：本测试验证：工具文本与结构化证据不一致时，检查器不能判为已验证。
async def test_faithfulness_rejects_text_artifact_divergence(dataset, monkeypatch):
    # 学习注释：创建可await的测试替身，模拟返回值或异常；保存到chain。
    chain = AsyncMock()
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(faithfulness, "_chain", lambda: chain)
    # 学习注释：调用evidence.ToolEvidence；保存到一份完整工具证据（bundle）。
    bundle = evidence.ToolEvidence(**versioning.evidence_context(), message="合成事实")
    # 学习注释：faithfulness.check_faithful的执行结果，await等待结果后再继续；保存到result。
    result = await faithfulness.check_faithful("回答", [
        {"content": "篡改的内容", "artifact": bundle.model_dump()}])
    # 学习注释：测试要求：result['status']等于'unverified'；不满足就失败。
    assert result["status"] == "unverified"
    # 学习注释：调用chain.ainvoke.assert_not_awaited。
    chain.ainvoke.assert_not_awaited()


# 学习注释：本测试验证：Agent最终响应必须保留工具的结构化证据。
async def test_agent_result_keeps_artifact(dataset, fake_search, monkeypatch):
    # 学习注释：定义FakeAgent对象，集中保存下方属性和方法。
    class FakeAgent:
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到msg。
            msg = await tools.search_similar_events.ainvoke({
                "type": "tool_call", "id": "t1", "name": "search_similar_events",
                "args": {"query": "合成"}})
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [msg, AIMessage(content="合成回答")]}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: FakeAgent())
    # 学习注释：agent.run的执行结果，await等待结果后再继续；保存到result。
    result = await agent.run("合成", self_check=False)
    # 学习注释：测试要求：result['evidence'][0]['request_id']等于result中键/位置为'request_id'的值；不满足就失败。
    assert result["evidence"][0]["request_id"] == result["request_id"]
    # 学习注释：测试要求：result['evidence'][0]['dataset_version']等于result中键/位置为'dataset_version'的值；不满足就失败。
    assert result["evidence"][0]["dataset_version"] == result["dataset_version"]
    # 学习注释：测试要求：result['tool_results'][0]['artifact']等于result['evidence']中键/位置为0的值；不满足就失败。
    assert result["tool_results"][0]["artifact"] == result["evidence"][0]


# 学习注释：本测试验证：重试失败后不能把新回答配上旧一轮证据。
async def test_failed_retry_does_not_reuse_previous_evidence(dataset, monkeypatch):
    # 学习注释：定义FakeAgent对象，集中保存下方属性和方法。
    class FakeAgent:
        # 学习注释：0；保存到index。
        index = 0
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：从langchain_core.messages引入ToolMessage，用于区分用户、助手和工具消息。
            from langchain_core.messages import ToolMessage
            # 学习注释：用1更新self.index；下方+=等写法把计算结果存回原变量。
            self.index += 1
            # 学习注释：调用evidence.ToolEvidence；保存到一份完整工具证据（bundle）。
            bundle = evidence.ToolEvidence(**versioning.evidence_context(), message=f"事实{self.index}")
            # 学习注释：evidence.transport的执行结果；保存到content、artifact。
            content, artifact = evidence.transport(bundle)
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [
                ToolMessage(content=content, artifact=artifact, name="query_outage_stat", tool_call_id=str(self.index)),
                AIMessage(content=f"回答{self.index}")]}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: FakeAgent())
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(faithfulness, "check_faithful", AsyncMock(side_effect=[
        {"status": "unfaithful", "reason": "一次"}, {"status": "faithful", "reason": "二次"}]))
    # 学习注释：agent.run的执行结果，await等待结果后再继续；保存到result。
    result = await agent.run("合成")
    # 学习注释：测试要求：result['answer']等于'回答2'；不满足就失败。
    assert result["answer"] == "回答2"
    # 学习注释：测试要求：result['evidence'][0]['message']等于'事实2'；不满足就失败。
    assert result["evidence"][0]["message"] == "事实2"


# 学习注释：本测试验证：候选与返回来自同次检索；召回后被重排挤出的正例不能记成召回漏检。
async def test_eval_uses_one_trace_and_does_not_mislabel_ranked_out_positive(dataset, fake_search):
    # 学习注释：包含id、query、filters、expect的字典；保存到item。
    item = {"id": "T", "query": "合成", "filters": None, "expect": "hit"}
    # 学习注释：包含合成的字典；保存到参考标签映射（gt）。
    gt = {"合成": {"E1": 1, "E2": 1, "E3": 1, "E4": 1}}
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(item, gt, top_k=1)
    # 学习注释：测试要求：len(fake_search.calls)等于1；不满足就失败。
    assert len(fake_search.calls) == 1
    # 学习注释：测试要求：result['candidate_count']等于4；不满足就失败。
    assert result["candidate_count"] == 4
    # 学习注释：测试要求：result['returned_count']等于1；不满足就失败。
    assert result["returned_count"] == 1
    # 学习注释：测试要求：result['known_positive_misses']等于空列表；不满足就失败。
    assert result["known_positive_misses"] == []
    # 学习注释：测试要求：result['status']等于'pass'；不满足就失败。
    assert result["status"] == "pass"


# 学习注释：本测试验证：日期筛选与来源核对必须使用完整的既定口径。
async def test_eval_same_day_range_and_source_check(dataset, fake_search):
    # 学习注释：包含id、query、filters、expect的字典；保存到item。
    item = {"id": "D", "query": "合成", "filters": {
        "line": "线路1", "time_range": {"start": "2025-06-01", "end": "2025-06-02"}}, "expect": "hit"}
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(item, {"合成": {"E2": 1}})
    # 学习注释：测试要求：result['scope_ok']是同一对象/空值True（成立）；不满足就失败。
    assert result["scope_ok"] is True
    # 学习注释：测试要求：result['source_match']是同一对象/空值True（成立）；不满足就失败。
    assert result["source_match"] is True
    # 学习注释：测试要求：result['status']等于'pass'；不满足就失败。
    assert result["status"] == "pass"
    # 学习注释：调用evaluation._load_sources；保存到sources。
    sources = evaluation._load_sources()
    # 学习注释：创建或浅复制字典：sources中键/位置为'E2'的值；保存到当前事件（event）。
    event = dict(sources["E2"])
    # 学习注释：'篡改的合成正文'；保存到event['事件文本']。
    event["事件文本"] = "篡改的合成正文"
    # 学习注释：测试要求：不满足（evaluation._source_matches的执行结果）；不满足就失败。
    assert not evaluation._source_matches(event, sources)


# 学习注释：本测试验证：重排失败需记录为处理失败，不能变成正常空结果。
async def test_eval_rerank_failure_is_recorded(dataset, fake_search, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.rerank, "rerank", AsyncMock(side_effect=RuntimeError("failed")))
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(
        {"id": "T", "query": "合成", "filters": None, "expect": "hit"}, {})
    # 学习注释：测试要求：result['status']等于'fail'；不满足就失败。
    assert result["status"] == "fail"
    # 学习注释：测试要求：result['signal']等于'processing_error'；不满足就失败。
    assert result["signal"] == "processing_error"
    # 学习注释：测试要求：result['trace']['error_stage']等于'rerank'；不满足就失败。
    assert result["trace"]["error_stage"] == "rerank"


# 学习注释：本测试验证：评测全部失败仍保存完整报告，并返回非零退出码。
async def test_all_failed_eval_returns_nonzero_and_writes_report(dataset, tmp_path, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar, "search_similar", AsyncMock(side_effect=RuntimeError("injected")))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "FIXED_SET", [
        {"id": str(i), "query": "合成", "filters": None, "expect": "hit"} for i in range(7)])
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "_load_ground_truth", lambda: {})
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "_code_metadata", lambda: {"test": True})
    # 学习注释：测试要求：await evaluation.main(output_dir=tmp_path / 'eval')等于1；不满足就失败。
    assert await evaluation.main(output_dir=tmp_path / "eval") == 1
    # 学习注释：把JSON文本解析为Python对象；保存到待保存的验收报告（report）。
    report = json.loads(next((tmp_path / "eval").glob("*/result.json")).read_text(encoding="utf-8"))
    # 学习注释：测试要求：len(report['results'])等于7；不满足就失败。
    assert len(report["results"]) == 7
    # 学习注释：测试要求：检查是否每一项条件都成立；不满足就失败。
    assert all(result["status"] == "fail" for result in report["results"])
    # 学习注释：测试要求：调用next((tmp_path / 'eval').glob('*/report.md')).is_file；不满足就失败。
    assert next((tmp_path / "eval").glob("*/report.md")).is_file()


# 学习注释：本测试验证：缺标签只能记待验证；不同运行的报告不能互相覆盖。
async def test_unlabeled_is_unverified_and_retained_runs(dataset, fake_search, tmp_path, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "FIXED_SET", [
        {"id": "T", "query": "合成", "filters": None, "expect": "hit"}])
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "_load_ground_truth", lambda: {})
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "_code_metadata", lambda: {"test": True})
    # 学习注释：测试要求：await evaluation.main(output_dir=tmp_path / 'eval')等于2；不满足就失败。
    assert await evaluation.main(output_dir=tmp_path / "eval") == 2
    # 学习注释：调用next，处理调用(tmp_path / 'eval').glob，处理'*/result.json'；保存到baseline。
    baseline = next((tmp_path / "eval").glob("*/result.json"))
    # 学习注释：测试要求：await evaluation.main(output_dir=tmp_path / 'eval', baseline=baseline)等于2；不满足就失败。
    assert await evaluation.main(output_dir=tmp_path / "eval", baseline=baseline) == 2
    # 学习注释：测试要求：len(list((tmp_path / 'eval').glob('*/result.json')))等于2；不满足就失败。
    assert len(list((tmp_path / "eval").glob("*/result.json"))) == 2


# 学习注释：本测试验证：HTTP响应对外提供结构化证据，前端可用于来源展示。
async def test_api_exposes_structured_evidence(dataset, fake_search, monkeypatch):
    # 学习注释：导入httpx：发送HTTP请求。
    import httpx
    # 学习注释：从app.main导入app，复用对应模块的实现。
    from app.main import app
    # 学习注释：定义FakeAgent对象，集中保存下方属性和方法。
    class FakeAgent:
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到msg。
            msg = await tools.search_similar_events.ainvoke({
                "type": "tool_call", "id": "api1", "name": "search_similar_events",
                "args": {"query": "合成"}})
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [msg, AIMessage(content="合成回答")]}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: FakeAgent())
    # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
        response = await client.post("/api/ask", json={"query": "合成", "self_check": False})
    # 学习注释：测试要求：response.status_code等于200；不满足就失败。
    assert response.status_code == 200
    # 学习注释：把HTTP响应正文解析为JSON对象；保存到body。
    body = response.json()
    # 学习注释：测试要求：body['evidence'][0]['cases'][0]['source_row_ids']为真/非空；不满足就失败。
    assert body["evidence"][0]["cases"][0]["source_row_ids"]
    # 学习注释：测试要求：body['evidence'][0]['request_id']等于body中键/位置为'request_id'的值；不满足就失败。
    assert body["evidence"][0]["request_id"] == body["request_id"]


# 学习注释：本测试验证：真实Agent图执行机制要保留请求上下文和artifact；模型本身使用替身。
async def test_real_agent_graph_preserves_context_and_artifacts(dataset, fake_search, monkeypatch):
    # 学习注释：从langchain.agents引入create_agent，用于创建模型选择工具并循环执行的Agent。
    from langchain.agents import create_agent
    # 学习注释：从langchain_core.language_models.fake_chat_models导入FakeMessagesListChatModel，复用对应模块的实现。
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    # 学习注释：定义ToolModel对象，集中保存下方属性和方法。
    class ToolModel(FakeMessagesListChatModel):
        # 学习注释：函数bind_tools接收tools；把这一段可复用处理封装起来。
        def bind_tools(self, tools, **kwargs):
            # 学习注释：返回self，结束本次函数调用。
            return self

    # 学习注释：调用ToolModel；保存到模型调用对象或模型名（model）。
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "search_similar_events", "args": {"query": "合成", "line": "线路1"}, "id": "parallel1"},
            {"name": "search_similar_events", "args": {"query": "合成", "line": "线路2"}, "id": "parallel2"},
        ]),
        AIMessage(content="合成回答"),
    ])
    # 学习注释：组装聊天模型、工具列表和系统规则，构造Agent执行器；保存到graph。
    graph = create_agent(model, tools=tools.AGENT_TOOLS)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: graph)
    # 学习注释：agent.run的执行结果，await等待结果后再继续；保存到result。
    result = await agent.run("比较两个线路", self_check=False)
    # 学习注释：测试要求：len(result['evidence'])等于2；不满足就失败。
    assert len(result["evidence"]) == 2
    # 学习注释：测试要求：{e['request_id'] for e in result['evidence']}等于1项集合：result中键/位置为'request_id'的值；不满足就失败。
    assert {e["request_id"] for e in result["evidence"]} == {result["request_id"]}
    # 学习注释：测试要求：{e['dataset_version'] for e in result['evidence']}等于1项集合：'test_v1'；不满足就失败。
    assert {e["dataset_version"] for e in result["evidence"]} == {"test_v1"}


# 学习注释：本测试验证：两个并发请求保持各自请求ID和版本上下文，不相互污染。
async def test_concurrent_requests_keep_distinct_context(dataset, fake_search):
    # 学习注释：导入asyncio：异步任务、等待、锁和超时。
    import asyncio
    # 学习注释：2项元组：创建任务间的时序信号，控制测试中谁先继续、创建任务间的时序信号，控制测试中谁先继续；保存到ready、release。
    ready, release = asyncio.Event(), asyncio.Event()
    # 学习注释：空列表；保存到seen。
    seen = []

    # 学习注释：异步函数call接收调用时的上下文；把这一段可复用处理封装起来。
    async def call():
        # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
        with versioning.dataset_scope():
            # 学习注释：调用versioning.current_request_id；保存到request。
            request = versioning.current_request_id()
            # 学习注释：向seen末尾添加request。
            seen.append(request)
            # 学习注释：只有len(seen)等于2才进入这一分支。
            if len(seen) == 2:
                # 学习注释：调用ready.set。
                ready.set()
            # 学习注释：调用release.wait，await等待结果后再继续。
            await release.wait()
            # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到result。
            result = await similar.search_similar("合成", with_signal=True)
            # 学习注释：测试要求：result['trace']['request_id']等于request；不满足就失败。
            assert result["trace"]["request_id"] == request

    # 学习注释：2项元组：调用asyncio.create_task，处理调用call、调用asyncio.create_task，处理调用call；保存到first、second。
    first, second = asyncio.create_task(call()), asyncio.create_task(call())
    # 学习注释：在给定超时上限内执行异步操作，超时则抛出异常，await等待结果后再继续。
    await asyncio.wait_for(ready.wait(), timeout=3)
    # 学习注释：调用release.set。
    release.set()
    # 学习注释：并发运行这些异步任务并收集结果，await等待结果后再继续。
    await asyncio.gather(first, second)
    # 学习注释：测试要求：len(set(seen))等于2；不满足就失败。
    assert len(set(seen)) == 2


# 学习注释：本测试验证：事件ID正确但正文不一致，来源检查也必须失败。
async def test_unknown_case_content_is_not_verified_by_matching_id(dataset, fake_search):
    # 学习注释：调用evaluation._load_sources；保存到sources。
    sources = evaluation._load_sources()
    # 学习注释：'与向量库不同的合成文本'；保存到sources['E1']['事件文本']。
    sources["E1"]["事件文本"] = "与向量库不同的合成文本"
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(
        {"id": "T", "query": "合成", "filters": None, "expect": "hit"},
        {"合成": {eid: 1 for eid in sources}}, sources=sources)
    # 学习注释：测试要求：result['status']等于'fail'；不满足就失败。
    assert result["status"] == "fail"
    # 学习注释：测试要求：result['source_match']是同一对象/空值False（不成立）；不满足就失败。
    assert result["source_match"] is False


# 学习注释：本测试验证：缺供电量证据应说明用户行求和，不能因筛选时间就声称事件去重。
def test_time_filter_does_not_claim_event_dedup_for_energy():
    # 学习注释：调用evidence.StatEvidence；保存到统计证据或文件属性（stat）。
    stat = evidence.StatEvidence(
        template_id="supply_center_energy", granularity="用户级 SUM",
        effective_filters={"time_range": {"start_time": "2025-01-01", "end_time": "2026-01-01"}},
        rows=[{"缺供电量总和": 12.5}],
    )
    # 学习注释：evidence.render_stat的执行结果；保存到文本（text）。
    text = evidence.render_stat(stat)
    # 学习注释：测试要求：'再按事件去重'不包含在文本（text）；不满足就失败。
    assert "再按事件去重" not in text
    # 学习注释：测试要求：'直接累加 users 用户行'包含在文本（text）；不满足就失败。
    assert "直接累加 users 用户行" in text
    # 学习注释：测试要求：'不做事件去重'包含在文本（text）；不满足就失败。
    assert "不做事件去重" in text


# 学习注释：本测试验证：占比证据说明实际过滤及支持范围，不承诺未实现的筛选。
def test_ratio_evidence_states_no_time_filter_and_supported_scope():
    # 学习注释：evidence.render_stat的执行结果；保存到文本（text）。
    text = evidence.render_stat(evidence.StatEvidence(template_id="outage_type_ratio"))
    # 学习注释：测试要求：'未指定时间条件'包含在文本（text）；不满足就失败。
    assert "未指定时间条件" in text
    # 学习注释：测试要求：'[时间口径]'不包含在文本（text）；不满足就失败。
    assert "[时间口径]" not in text
    # 学习注释：测试要求：'不支持单标签重算'包含在文本（text）；不满足就失败。
    assert "不支持单标签重算" in text


# 学习注释：本测试验证：补充标签的数据版本或问题范围不匹配时拒绝套用。
def test_review_rejects_stale_data_and_changed_scope(dataset, tmp_path, monkeypatch):
    # 学习注释：包含id、query、filters、expect的字典；保存到case。
    case = {"id": "T", "query": "合成", "filters": None, "expect": "hit"}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "FIXED_SET", [case])
    # 学习注释：按目录层级拼接文件路径 tmp_path / 'review.json'；保存到path。
    path = tmp_path / "review.json"
    # 学习注释：包含dataset_version、corpus_sha256、cases的字典；保存到review。
    review = {"dataset_version": "old", "corpus_sha256": versioning.file_sha256(dataset.corpus_path),
              "cases": [{**case, "labels": []}]}
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    path.write_text(json.dumps(review), encoding="utf-8")
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError, match="版本"):
        # 学习注释：evaluation._apply_review的执行结果。
        evaluation._apply_review(path, {})
    # 学习注释：'test_v1'；保存到review['dataset_version']。
    review["dataset_version"] = "test_v1"
    # 学习注释：包含line的字典；保存到review['cases'][0]['filters']。
    review["cases"][0]["filters"] = {"line": "线路2"}
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    path.write_text(json.dumps(review), encoding="utf-8")
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError, match="筛选"):
        # 学习注释：evaluation._apply_review的执行结果。
        evaluation._apply_review(path, {})


# 学习注释：本测试验证：新null标签覆盖旧正例；返回已知负例应使评测失败。
async def test_review_null_overrides_positive_and_negative_fails(dataset, fake_search, tmp_path, monkeypatch):
    # 学习注释：包含id、query、filters、expect的字典；保存到case。
    case = {"id": "T", "query": "合成", "filters": None, "expect": "hit"}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(evaluation, "FIXED_SET", [case])
    # 学习注释：按目录层级拼接文件路径 tmp_path / 'review.json'；保存到path。
    path = tmp_path / "review.json"
    # 学习注释：包含dataset_version、corpus_sha256、cases的字典；保存到review。
    review = {"dataset_version": "test_v1", "corpus_sha256": versioning.file_sha256(dataset.corpus_path),
              "cases": [{**case, "labels": [{"event_id": "E1", "similar": None}]}]}
    # 学习注释：包含合成的字典；保存到base。
    base = {"合成": {eid: 1 for eid in ("E1", "E2", "E3", "E4")}}
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    path.write_text(json.dumps(review), encoding="utf-8")
    # 学习注释：evaluation._apply_review的执行结果；保存到参考标签映射（gt）。
    gt = evaluation._apply_review(path, base)
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(case, gt)
    # 学习注释：测试要求：result['status']等于'unverified'；不满足就失败。
    assert result["status"] == "unverified"
    # 学习注释：测试要求：result['ambiguous_count']等于1，并且result['missing_label_count']等于0；不满足就失败。
    assert result["ambiguous_count"] == 1 and result["missing_label_count"] == 0
    # 学习注释：测试要求：base['合成']['E1']等于1；不满足就失败。
    assert base["合成"]["E1"] == 1
    # 学习注释：0；保存到review['cases'][0]['labels'][0]['similar']。
    review["cases"][0]["labels"][0]["similar"] = 0
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    path.write_text(json.dumps(review), encoding="utf-8")
    # 学习注释：evaluation._run_one的执行结果，await等待结果后再继续；保存到result。
    result = await evaluation._run_one(case, evaluation._apply_review(path, base))
    # 学习注释：测试要求：result['status']等于'fail'；不满足就失败。
    assert result["status"] == "fail"
    # 学习注释：测试要求：result['known_irrelevant_returned']等于1项列表：'E1'；不满足就失败。
    assert result["known_irrelevant_returned"] == ["E1"]


# 学习注释：本测试验证：展示案例数与召回候选数必须分别表达。
async def test_case_count_is_distinct_from_recall_count(dataset, fake_search):
    # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到message。
    message = await tools.search_similar_events.ainvoke({"type": "tool_call", "id": "count-test",
        "name": "search_similar_events", "args": {"query": "合成", "top_k": 1}})
    # 学习注释：测试要求：len(message.artifact['cases'])等于1；不满足就失败。
    assert len(message.artifact["cases"]) == 1
    # 学习注释：测试要求：message.artifact['signal']['candidate_count']等于4；不满足就失败。
    assert message.artifact["signal"]["candidate_count"] == 4
    # 学习注释：测试要求：'[最终展示案例数] 1'包含在message的content属性（content）；不满足就失败。
    assert "[最终展示案例数] 1" in message.content
    # 学习注释：测试要求：'[召回候选数] 4'包含在message的content属性（content）；不满足就失败。
    assert "[召回候选数] 4" in message.content
    # 学习注释：测试要求：'不是范围内停电总次数'包含在message的content属性（content）；不满足就失败。
    assert "不是范围内停电总次数" in message.content
