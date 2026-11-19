# 阅读导航：上游故障恢复回归
# 本文件作用：模拟结构化输出、重排与检索服务异常，检查重试边界和失败恢复。
# 建议读法：故障由替身注入，不会实际关闭模型服务，也不能替代服务商真实故障演练。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""真实客户端协议解析 + 注入HTTP错误；不是服务商线上故障演练。"""
# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入httpx：发送HTTP请求。
import httpx
# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest
# 学习注释：从openai引入AsyncOpenAI，用于调用兼容OpenAI协议的服务，本项目用其异步Embedding客户端。
from openai import AsyncOpenAI
# 学习注释：从langchain_core.runnables引入RunnableLambda，用于组合可同步或异步调用的处理步骤。
from langchain_core.runnables import RunnableLambda

# 学习注释：从app.core导入agent, embeddings, faithfulness, rerank, similar, tools，复用对应模块的实现。
from app.core import agent, embeddings, faithfulness, rerank, similar, tools
# 学习注释：从app.core导入coref，复用对应模块的实现。
from app.core import coref


# 学习注释：本测试验证：指定临时HTTP/网络错误可以有限重试，并在服务恢复后成功。
@pytest.mark.parametrize("fault", ["rate_limit", "server_error", "timeout"])
async def test_rerank_protocol_retry_and_recovery(monkeypatch, fault):
    # 学习注释：0；保存到count。
    count = 0
    # 学习注释：函数handle接收request；把这一段可复用处理封装起来。
    def handle(request):
        nonlocal count
        # 学习注释：用1更新count；下方+=等写法把计算结果存回原变量。
        count += 1
        # 学习注释：只有count等于1才进入这一分支。
        if count == 1:
            # 学习注释：只有fault等于'timeout'才进入这一分支。
            if fault == "timeout":
                # 学习注释：以异常终止这一条路径：'injected timeout'；上层不能把它当作正常成功。
                raise httpx.ReadTimeout("injected timeout", request=request)
            # 学习注释：返回调用httpx.Response，处理fault等于'rate_limit'时取429，否则取503，结束本次函数调用。
            return httpx.Response(429 if fault == "rate_limit" else 503, json={"error": "injected"})
        # 学习注释：返回调用httpx.Response，处理200，结束本次函数调用。
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": .8}]})
    # 学习注释：httpx的AsyncClient属性（AsyncClient）；保存到original。
    original = httpx.AsyncClient
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(rerank.httpx, "AsyncClient", lambda: original(transport=httpx.MockTransport(handle)))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(rerank, "_BACKOFF", 0)
    # 学习注释：测试要求：await rerank.rerank('合成', ['合成案例'])等于1项列表：2项元组：0、0.8；不满足就失败。
    assert await rerank.rerank("合成", ["合成案例"]) == [(0, .8)]
    # 学习注释：测试要求：count等于2；不满足就失败。
    assert count == 2


# 学习注释：本测试验证：重排重试耗尽记为处理失败，后续新请求仍能恢复。
async def test_rerank_exhaustion_is_processing_error_then_recovers(dataset, fake_search, monkeypatch):
    # fake_search replaces rerank by default; restore the actual protocol adapter.
    monkeypatch.setattr(similar.rerank, "rerank", REAL_RERANK)
    # 学习注释：2项元组：False（不成立）、0；保存到healthy、count。
    healthy, count = False, 0
    # 学习注释：函数handle接收request；把这一段可复用处理封装起来。
    def handle(request):
        nonlocal count
        # 学习注释：用1更新count；下方+=等写法把计算结果存回原变量。
        count += 1
        # 学习注释：返回healthy为真/非空时取调用httpx.Response，处理200，否则取调用httpx.Response，处理503，结束本次函数调用。
        return (httpx.Response(200, json={"results": [{"index": 0, "relevance_score": .8}]})
                if healthy else httpx.Response(503, json={"error": "injected"}))
    # 学习注释：httpx的AsyncClient属性（AsyncClient）；保存到original。
    original = httpx.AsyncClient
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(rerank.httpx, "AsyncClient", lambda: original(transport=httpx.MockTransport(handle)))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(rerank, "_BACKOFF", 0)
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到failed。
    failed = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：count等于4，并且failed['signal'].signal等于'processing_error'；不满足就失败。
    assert count == 4 and failed["signal"].signal == "processing_error"
    # 学习注释：测试要求：failed['trace']['error_stage']等于'rerank'，并且failed['events']等于空列表；不满足就失败。
    assert failed["trace"]["error_stage"] == "rerank" and failed["events"] == []
    # 学习注释：True（成立）；保存到healthy。
    healthy = True
    # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到recovered。
    recovered = await similar.search_similar("合成", with_signal=True)
    # 学习注释：测试要求：recovered['events']为真/非空，并且recovered['signal'].signal尚未设置（None）；不满足就失败。
    assert recovered["events"] and recovered["signal"].signal is None


# 学习注释：rerank的rerank属性（rerank）；保存到REAL_RERANK。
REAL_RERANK = rerank.rerank
# 学习注释：embeddings的embed_query属性（embed_query）；保存到REAL_EMBED_QUERY。
REAL_EMBED_QUERY = embeddings.embed_query


# 学习注释：本测试验证：Embedding SDK错误被正确处理，服务恢复后能继续检索。
async def test_embedding_sdk_error_and_recovery(dataset, fake_search, monkeypatch):
    # 学习注释：False（不成立）；保存到healthy。
    healthy = False
    # 学习注释：函数handle接收request；把这一段可复用处理封装起来。
    def handle(request):
        # 学习注释：只有healthy为空或为False才进入这一分支。
        if not healthy:
            # 学习注释：返回调用httpx.Response，处理429，结束本次函数调用。
            return httpx.Response(429, json={"error": {"message": "injected", "type": "rate_limit"}})
        # 学习注释：返回调用httpx.Response，处理200，结束本次函数调用。
        return httpx.Response(200, json={"object": "list", "model": "test-embed",
            "data": [{"object": "embedding", "index": 0, "embedding": [.1, .2]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1}})
    # 学习注释：创建兼容OpenAI协议的异步API客户端；with结束时调用退出逻辑，关闭资源或恢复上下文。
    async with AsyncOpenAI(api_key="test-only", base_url="http://invalid.test/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as client:
        # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
        monkeypatch.setattr(embeddings, "_CLIENT", client)
        # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
        monkeypatch.setattr(embeddings, "embed_query", REAL_EMBED_QUERY)
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到failed。
        failed = await similar.search_similar("合成", with_signal=True)
        # 学习注释：测试要求：failed['signal'].signal等于'processing_error'；不满足就失败。
        assert failed["signal"].signal == "processing_error"
        # 学习注释：测试要求：failed['trace']['error_stage']等于'embedding'；不满足就失败。
        assert failed["trace"]["error_stage"] == "embedding"
        # 学习注释：测试要求：不满足（fake_search.calls为真/非空）；不满足就失败。
        assert not fake_search.calls
        # 学习注释：True（成立）；保存到healthy。
        healthy = True
        # 学习注释：similar.search_similar的执行结果，await等待结果后再继续；保存到recovered。
        recovered = await similar.search_similar("合成", with_signal=True)
        # 学习注释：测试要求：recovered['events']为真/非空；不满足就失败。
        assert recovered["events"]


# 学习注释：本测试验证：通过真实检查函数注入网络失败，验证Agent走未验证降级分支。
async def test_checker_transport_failure_degrades_via_real_check_function(dataset, fake_search, monkeypatch):
    # 学习注释：从langchain_core.messages引入AIMessage，用于区分用户、助手和工具消息。
    from langchain_core.messages import AIMessage
    # 学习注释：定义ToolAgent对象，集中保存下方属性和方法。
    class ToolAgent:
        # 学习注释：异步函数ainvoke接收request；把这一段可复用处理封装起来。
        async def ainvoke(self, request):
            # 学习注释：异步执行tools.search_similar_events调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到msg。
            msg = await tools.search_similar_events.ainvoke({"type": "tool_call", "id": "t",
                "name": "search_similar_events", "args": {"query": "合成"}})
            # 学习注释：返回包含messages的字典，结束本次函数调用。
            return {"messages": [msg, AIMessage(content="待核实回答")]}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_get_agent", lambda: ToolAgent())
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(faithfulness, "_chain", lambda: AsyncMock(side_effect=None,
        ainvoke=AsyncMock(side_effect=httpx.ReadTimeout("injected"))))
    # 学习注释：agent.run的执行结果，await等待结果后再继续；保存到out。
    out = await agent.run("合成")
    # 学习注释：测试要求：out['degraded']为真/非空，并且out['verification_status']等于'unverified'；不满足就失败。
    assert out["degraded"] and out["verification_status"] == "unverified"
    # 学习注释：测试要求：out['evidence']为真/非空，并且'待核实回答'不包含在out中键/位置为'answer'的值；不满足就失败。
    assert out["evidence"] and "待核实回答" not in out["answer"]


# 学习注释：本测试验证：多轮补全模型失败时回退原问题，保持流程可继续。
async def test_coref_failure_falls_back_to_original_question(monkeypatch):
    # 学习注释：异步函数fail接收_；把这一段可复用处理封装起来。
    async def fail(_):
        # 学习注释：以异常终止这一条路径：'injected'；上层不能把它当作正常成功。
        raise httpx.ConnectError("injected")
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(coref, "get_chat_model", lambda: RunnableLambda(fail))
    # 学习注释：测试要求：await coref.resolve_outage('那6月呢', '线路1')等于'那6月呢'；不满足就失败。
    assert await coref.resolve_outage("那6月呢", "线路1") == "那6月呢"


# 学习注释：本测试验证：正在执行的聊天请求被取消时，不留下半轮历史。
async def test_cancelled_active_chat_leaves_no_partial_history(monkeypatch):
    # 学习注释：导入asyncio：异步任务、等待、锁和超时。
    import asyncio
    # 学习注释：从app.core.memory导入SessionStore，复用对应模块的实现。
    from app.core.memory import SessionStore
    # 学习注释：创建任务间的时序信号，控制测试中谁先继续；保存到entered。
    entered = asyncio.Event()
    # 学习注释：异步函数blocked接收_；把这一段可复用处理封装起来。
    async def blocked(_):
        # 学习注释：调用entered.set。
        entered.set()
        # 学习注释：调用asyncio.Event().wait，await等待结果后再继续。
        await asyncio.Event().wait()
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "_SESSIONS", SessionStore())
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(agent, "run", blocked)
    # 学习注释：调用asyncio.create_task，处理agent.chat的执行结果；保存到task。
    task = asyncio.create_task(agent.chat("cancelled", "尚未完成的问题"))
    # 学习注释：调用entered.wait，await等待结果后再继续。
    await entered.wait()
    # 学习注释：调用task.cancel。
    task.cancel()
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(asyncio.CancelledError):
        # 学习注释：task，await等待结果后再继续。
        await task
    # 学习注释：测试核对会话历史是否符合下方断言；SessionStore.get缺会话时返回空列表。
    assert not agent._SESSIONS.get("cancelled")
    # 学习注释：在给定超时上限内执行异步操作，超时则抛出异常，await等待结果后再继续。
    await asyncio.wait_for(agent.clear_session("cancelled"), 1)
