# 阅读导航：会话与降级回归
# 本文件作用：复现失败半轮历史、会话锁、取消、清空及检查器失效，确保状态按预期处理。
# 建议读法：asyncio.Event用于控制并发步骤的先后，避免依靠随机延迟猜测请求顺序。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""用确定性故障和并发时序验证会话/降级，不消耗模型额度。"""
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入gc：供本文件调用的模块。
import gc
# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入httpx：发送HTTP请求。
import httpx
# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest
# 学习注释：从langchain_core.messages引入AIMessage, HumanMessage, ToolMessage，用于区分用户、助手和工具消息。
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# 学习注释：从app.core导入agent, evidence, faithfulness, versioning，复用对应模块的实现。
from app.core import agent, evidence, faithfulness, versioning
# 学习注释：从app.core.memory导入SessionStore，复用对应模块的实现。
from app.core.memory import SessionStore
# 学习注释：从app.main导入app，复用对应模块的实现。
from app.main import app


# 学习注释：函数isolated_sessions接收pytest临时对象替换工具（monkeypatch）；把这一段可复用处理封装起来。
@pytest.fixture(autouse=True)
def isolated_sessions(monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_SESSIONS", SessionStore(max_sessions=4))
    # Preserve the implementation's container type so the old leak is reproducible.
    monkeypatch.setattr(agent, "_session_locks", type(agent._session_locks)())


# 学习注释：函数good_result接收生成回答（answer）；把这一段可复用处理封装起来。
def good_result(answer="合成可信回答"):
    # 学习注释：返回包含answer、degraded、verification_status的字典，结束本次函数调用。
    return {"answer": answer, "degraded": False, "verification_status": "faithful"}


# 学习注释：本测试验证：请求失败不留下只有问题没有回答的半轮历史。
async def test_failed_request_does_not_leave_half_turn(monkeypatch):
    # 学习注释：创建可await的测试替身，模拟返回值或异常；保存到run。
    run = AsyncMock(side_effect=[RuntimeError("injected"), good_result()])
    # 学习注释：创建可await的测试替身，模拟返回值或异常；保存到resolve。
    resolve = AsyncMock(return_value="不应使用失败问题做历史")
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", run)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent.coref, "resolve_outage", resolve)
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(RuntimeError):
        # 学习注释：agent.chat的执行结果，await等待结果后再继续。
        await agent.chat("s", "失败问题")
    # 学习注释：agent.chat的执行结果，await等待结果后再继续。
    await agent.chat("s", "恢复后的新问题")
    # 学习注释：调用resolve.assert_not_awaited。
    resolve.assert_not_awaited()
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert [m.content for m in agent._SESSIONS.get("s")] == ["恢复后的新问题", "合成可信回答"]


# 学习注释：本测试验证：降级问答不写入历史，也不挤掉已有可信上下文。
async def test_degraded_turn_does_not_displace_verified_history(monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", AsyncMock(side_effect=[good_result(),
        {"answer": "原始工具事实", "degraded": True, "verification_status": "unverified"}]))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent.coref, "resolve_outage", AsyncMock(return_value="降级问题"))
    # 学习注释：agent.chat的执行结果，await等待结果后再继续。
    await agent.chat("s", "已完成问题")
    # 学习注释：读取指定会话消息，缺会话时得到空列表，并更新最近访问顺序。
    before = list(agent._SESSIONS.get("s"))
    # 学习注释：agent.chat的执行结果，await等待结果后再继续。
    await agent.chat("s", "降级问题")
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert agent._SESSIONS.get("s") == before


# 学习注释：本测试验证：历史裁剪后仍保留完整补全问题，避免丢失线路等实体。
async def test_resolved_context_survives_trimming(monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", AsyncMock(return_value=good_result("2次")))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent.coref, "resolve_outage", AsyncMock(return_value="线路1在2025年6月停电次数"))
    # 学习注释：agent.chat的执行结果，await等待结果后再继续。
    await agent.chat("s", "线路1在2025年停电次数")
    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给_。
    for _ in range(12):
        # 学习注释：agent.chat的执行结果，await等待结果后再继续。
        await agent.chat("s", "那6月呢")
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert len(agent._SESSIONS.get("s")) == 12
    # 学习注释：测试要求：'线路1'包含在agent._history_text的执行结果；不满足就失败。
    assert "线路1" in agent._history_text("s")
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert "那6月呢" == agent._SESSIONS.get("s")[-2].content


# 学习注释：本测试验证：结束且无等待者的会话锁可以回收，不无限积累。
async def test_finished_session_locks_do_not_accumulate(monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", AsyncMock(return_value=good_result()))
    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给i。
    for i in range(1200):
        # 学习注释：agent.chat的执行结果，await等待结果后再继续。
        await agent.chat(f"session-{i}", "独立问题")
    # 学习注释：调用gc.collect。
    gc.collect()
    # 学习注释：测试要求：len(agent._SESSIONS._sessions)等于4；不满足就失败。
    assert len(agent._SESSIONS._sessions) == 4
    # 学习注释：测试要求：len(agent._session_locks)等于0；不满足就失败。
    assert len(agent._session_locks) == 0


# 学习注释：本测试验证：同会话请求按序执行，清空会话等待当前写回完成。
async def test_same_session_serializes_and_clear_waits(monkeypatch):
    # 学习注释：2项元组：创建任务间的时序信号，控制测试中谁先继续、创建任务间的时序信号，控制测试中谁先继续；保存到entered、release。
    entered, release = asyncio.Event(), asyncio.Event()
    # 学习注释：空列表；保存到calls。
    calls = []
    # 学习注释：异步函数run接收用户查询文本（query）；把这一段可复用处理封装起来。
    async def run(query):
        # 学习注释：向calls末尾添加用户查询文本（query）。
        calls.append(query)
        # 学习注释：只有len(calls)等于1才进入这一分支。
        if len(calls) == 1:
            # 学习注释：调用entered.set。
            entered.set()
            # 学习注释：调用release.wait，await等待结果后再继续。
            await release.wait()
        # 学习注释：返回调用good_result，处理用户查询文本（query），结束本次函数调用。
        return good_result(query)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", run)
    # 学习注释：创建可await的测试替身，模拟返回值或异常；保存到resolve。
    resolve = AsyncMock(side_effect=lambda q, history: q)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent.coref, "resolve_outage", resolve)
    # 学习注释：调用asyncio.create_task，处理agent.chat的执行结果；保存到first。
    first = asyncio.create_task(agent.chat("s", "首问"))
    # 学习注释：在给定超时上限内执行异步操作，超时则抛出异常，await等待结果后再继续。
    await asyncio.wait_for(entered.wait(), 2)
    # 学习注释：调用asyncio.create_task，处理agent.chat的执行结果；保存到second。
    second = asyncio.create_task(agent.chat("s", "追问"))
    # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
    await asyncio.sleep(0)
    # 学习注释：调用asyncio.create_task，处理agent.clear_session的执行结果；保存到clearing。
    clearing = asyncio.create_task(agent.clear_session("s"))
    # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
    await asyncio.sleep(0)
    # 学习注释：测试要求：calls等于1项列表：'首问'，并且不满足（调用clearing.done）；不满足就失败。
    assert calls == ["首问"] and not clearing.done()
    # 学习注释：调用release.set。
    release.set()
    # 学习注释：在给定超时上限内执行异步操作，超时则抛出异常，await等待结果后再继续。
    await asyncio.wait_for(asyncio.gather(first, second, clearing), 3)
    # 学习注释：测试要求：'首问'包含在resolve.call_args.args中键/位置为1的值；不满足就失败。
    assert "首问" in resolve.call_args.args[1]
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert agent._SESSIONS.get("s") == []
    # 学习注释：agent.chat的执行结果，await等待结果后再继续。
    await agent.chat("s", "清空后新问题")
    # 学习注释：测试要求：resolve.await_count等于1；不满足就失败。
    assert resolve.await_count == 1


# 学习注释：本测试验证：取消等待者不破坏同会话锁，也不阻塞其他会话。
async def test_cancelled_waiter_does_not_break_lock_or_other_sessions(monkeypatch):
    # 学习注释：2项元组：创建任务间的时序信号，控制测试中谁先继续、创建任务间的时序信号，控制测试中谁先继续；保存到entered、release。
    entered, release = asyncio.Event(), asyncio.Event()
    # 学习注释：异步函数run接收用户查询文本（query）；把这一段可复用处理封装起来。
    async def run(query):
        # 学习注释：只有用户查询文本（query）等于'慢请求'才进入这一分支。
        if query == "慢请求":
            # 学习注释：调用entered.set。
            entered.set()
            # 学习注释：调用release.wait，await等待结果后再继续。
            await release.wait()
        # 学习注释：返回调用good_result，处理用户查询文本（query），结束本次函数调用。
        return good_result(query)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", run)
    # 学习注释：调用asyncio.create_task，处理agent.chat的执行结果；保存到first。
    first = asyncio.create_task(agent.chat("a", "慢请求"))
    # 学习注释：调用entered.wait，await等待结果后再继续。
    await entered.wait()
    # 学习注释：调用asyncio.create_task，处理agent.chat的执行结果；保存到waiter。
    waiter = asyncio.create_task(agent.chat("a", "取消请求"))
    # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
    await asyncio.sleep(0)
    # 学习注释：调用waiter.cancel。
    waiter.cancel()
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(asyncio.CancelledError):
        # 学习注释：waiter，await等待结果后再继续。
        await waiter
    # 学习注释：在给定超时上限内执行异步操作，超时则抛出异常，await等待结果后再继续。
    await asyncio.wait_for(agent.chat("b", "独立会话"), 2)
    # 学习注释：调用asyncio.create_task，处理agent.clear_session的执行结果；保存到clear。
    clear = asyncio.create_task(agent.clear_session("a"))
    # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
    await asyncio.sleep(0)
    # 学习注释：测试要求：不满足（调用clear.done）；不满足就失败。
    assert not clear.done()
    # 学习注释：调用release.set。
    release.set()
    # 学习注释：并发运行这些异步任务并收集结果，await等待结果后再继续。
    await asyncio.gather(first, clear)
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert not agent._SESSIONS.get("a")
    # 学习注释：测试要求：len(agent._session_locks)等于0；不满足就失败。
    assert len(agent._session_locks) == 0


# 学习注释：本测试验证：达到会话容量时淘汰最久未访问者，保留刚读取的会话。
def test_history_lru_keeps_recently_accessed_session():
    # 学习注释：调用SessionStore；保存到store。
    store = SessionStore(max_history=4, max_sessions=2)
    # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
    for sid in ("a", "b"):
        # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
        store.append(sid, HumanMessage(content=sid), AIMessage(content="answer"))
    # 学习注释：读取指定会话消息，缺会话时得到空列表，并更新最近访问顺序。
    store.get("a")
    # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
    store.append("c", HumanMessage(content="c"))
    # 学习注释：测试要求：set(store._sessions)等于2项集合：'a'、'c'；不满足就失败。
    assert set(store._sessions) == {"a", "c"}
    # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
    for i in range(6):
        # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
        store.append("a", HumanMessage(content=str(i)))
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert [m.content for m in store.get("a")] == ["2", "3", "4", "5"]


# 学习注释：函数scripted_agent接收pytest临时对象替换工具（monkeypatch）、answers；把这一段可复用处理封装起来。
def scripted_agent(monkeypatch, answers):
    # 学习注释：定义FakeAgent对象，集中保存下方属性和方法。
    class FakeAgent:
        # 学习注释：0；保存到calls。
        calls = 0
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：用1更新self.calls；下方+=等写法把计算结果存回原变量。
            self.calls += 1
            # 学习注释：answers中键/位置为min(self.calls - 1, len(answers) - 1)的值；保存到生成回答（answer）。
            answer = answers[min(self.calls-1, len(answers)-1)]
            # 学习注释：调用evidence.ToolEvidence；保存到一份完整工具证据（bundle）。
            bundle = evidence.ToolEvidence(**versioning.evidence_context(), message=f"事实{self.calls}")
            # 学习注释：evidence.transport的执行结果；保存到content、artifact。
            content, artifact = evidence.transport(bundle)
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [ToolMessage(content=content, artifact=artifact, name="query_outage_stat", tool_call_id=str(self.calls)), AIMessage(content=answer)]}
    # 学习注释：调用FakeAgent；保存到fake。
    fake = FakeAgent()
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: fake)
    # 学习注释：返回fake，结束本次函数调用。
    return fake


# 学习注释：本测试验证：HTTP降级响应展示最新工具事实，但不把降级轮次写入历史。
@pytest.mark.parametrize("statuses,calls,final", [
    (["unfaithful", "unfaithful", "unfaithful"], 3, "unfaithful"),
    (["unverified"], 1, "unverified"),
    (["unfaithful", "unverified", "faithful"], 2, "unverified"),
])
async def test_degradation_http_keeps_latest_facts_out_of_history(dataset, monkeypatch, statuses, calls, final):
    # 学习注释：调用scripted_agent，处理pytest临时对象替换工具（monkeypatch）；保存到fake。
    fake = scripted_agent(monkeypatch, ["不可信答案"])
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(faithfulness, "check_faithful", AsyncMock(side_effect=[
        {"status": s, "reason": "injected"} for s in statuses]))
    # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
        response = await client.post("/api/chat", json={"session_id": "s", "query": "问题"})
    # 学习注释：把HTTP响应正文解析为JSON对象；保存到body。
    body = response.json()
    # 学习注释：测试要求：response.status_code等于200；不满足就失败。
    assert response.status_code == 200
    # 学习注释：测试要求：fake.calls等于calls；不满足就失败。
    assert fake.calls == calls
    # 学习注释：测试要求：body['verification_status']等于final，并且body['degraded']为真/非空；不满足就失败。
    assert body["verification_status"] == final and body["degraded"]
    # 学习注释：测试要求：f'事实{calls}'包含在body中键/位置为'answer'的值，并且'不可信答案'不包含在body中键/位置为'answer'的值；不满足就失败。
    assert f"事实{calls}" in body["answer"] and "不可信答案" not in body["answer"]
    # 学习注释：测试要求：body['evidence'][0]['message']等于把花括号内的变量填入f字符串，生成文字；不满足就失败。
    assert body["evidence"][0]["message"] == f"事实{calls}"
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert agent._SESSIONS.get("s") == []


# 学习注释：本测试验证：模型返回空回答时应降级，不算正常成功。
async def test_empty_generation_is_not_success(dataset, monkeypatch):
    # 学习注释：调用scripted_agent，处理pytest临时对象替换工具（monkeypatch）。
    scripted_agent(monkeypatch, [""])
    # 学习注释：agent.chat的执行结果，await等待结果后再继续；保存到result。
    result = await agent.chat("s", "问题")
    # 学习注释：测试要求：result['degraded']为真/非空，并且result['verification_status']等于'unverified'；不满足就失败。
    assert result["degraded"] and result["verification_status"] == "unverified"
    # 学习注释：测试要求：'事实1'包含在result中键/位置为'answer'的值；不满足就失败。
    assert "事实1" in result["answer"]
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert not agent._SESSIONS.get("s")


# 学习注释：本测试验证：API失败响应使用固定说明，不回显可能含敏感内容的上游错误。
async def test_api_failure_does_not_echo_upstream_response(dataset, monkeypatch):
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", AsyncMock(side_effect=RuntimeError("private-upstream-response")))
    # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到response。
        response = await client.post("/api/ask", json={"query": "问题"})
    # 学习注释：测试要求：response.status_code等于500；不满足就失败。
    assert response.status_code == 500
    # 学习注释：测试要求：'private-upstream-response'不包含在response的文本属性（text）；不满足就失败。
    assert "private-upstream-response" not in response.text


# 学习注释：本测试验证：模型停在工具调用计划时，不能退回前面的计划文字冒充最终答案。
def test_unfinished_generation_does_not_reuse_tool_plan():
    # 学习注释：调用AIMessage；保存到plan。
    plan = AIMessage(content="我先查询数据", tool_calls=[{"name": "query_outage_stat", "args": {}, "id": "x"}])
    # 学习注释：测试要求：agent._extract_answer_and_tools([plan, AIMessage(content='')])[0]等于''；不满足就失败。
    assert agent._extract_answer_and_tools([plan, AIMessage(content="")])[0] == ""
    # 学习注释：测试要求：agent._extract_answer_and_tools([plan])[0]等于''；不满足就失败。
    assert agent._extract_answer_and_tools([plan])[0] == ""
    # 学习注释：测试要求：agent._extract_answer_and_tools([AIMessage(content=[{'type': 'text', 'text': '文本回答'}])])[0]等于'文本回答'；不满足就失败。
    assert agent._extract_answer_and_tools([AIMessage(content=[{"type": "text", "text": "文本回答"}])])[0] == "文本回答"
