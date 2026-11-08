import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.graph import nodes


@pytest.mark.asyncio
async def test_classify_intent_writes_confidence_and_route(monkeypatch):
    async def fake_classify(query, history=""):
        return {"intent": "电费退费", "confidence": 0.83}
    monkeypatch.setattr(nodes.intent_mod, "classify", fake_classify)
    out = await nodes.classify_intent({"messages": [HumanMessage("这个能退吗")],
                                       "resolved_query": "智能电表还能申请退费吗"})
    assert out["intent"] == "电费退费"
    assert out["intent_confidence"] == 0.83
    assert out["route"] == "refund_flow"                 # 电费退费 → refund_flow
    assert out["trace"]["route"] == "refund_flow"
    assert out["trace"]["intent_confidence"] == 0.83     # 不覆盖 confidence_check 的 confidence 键


def test_get_chat_model_honors_model_override():
    from app.core.llm import get_chat_model
    m = get_chat_model(model="glm-4-flash")
    assert m.model_name == "glm-4-flash"
    d = get_chat_model()
    from app.config import settings
    assert d.model_name == settings.chat_model


@pytest.mark.asyncio
async def test_resolve_reference_passthrough_when_complete(monkeypatch):
    async def fake_resolve(q, history=""):
        return q  # 已完整,原样
    monkeypatch.setattr(nodes.coref, "resolve", fake_resolve)
    out = await nodes.resolve_reference({"messages": [HumanMessage("智能电表的换表维护期多久")]})
    assert out["resolved_query"] == "智能电表的换表维护期多久"
    assert out["trace"]["coref"] == "passthrough"


@pytest.mark.asyncio
async def test_resolve_reference_rewrites_with_history(monkeypatch):
    async def fake_resolve(q, history=""):
        return "智能电表还能申请退费吗"
    monkeypatch.setattr(nodes.coref, "resolve", fake_resolve)
    out = await nodes.resolve_reference({"messages": [
        HumanMessage("智能电表什么时候到"), AIMessage("预计明天"), HumanMessage("这个能退吗")]})
    assert out["resolved_query"] == "智能电表还能申请退费吗"
    assert out["trace"]["coref"] == "rewrite"


# ---- Task 7: fetch_workorder + list_user_workorders + 缺单 interrupt ----

def test_extract_order_id():
    assert nodes._extract_order_id("工单1001的报修") == "1001"
    assert nodes._extract_order_id("尾号 20260701 那单") == "20260701"
    assert nodes._extract_order_id("我要退费") is None


@pytest.mark.asyncio
async def test_fetch_order_uses_id_in_query():
    uid = "u1"
    mine = nodes.business.list_user_workorders(uid)[0]["workorder_no"]   # 话里报的得是他自己的单
    out = await nodes.fetch_workorder({"resolved_query": f"工单{mine}能退吗", "user_id": uid})
    assert out["workorder_no"] == mine
    assert out["workorder_data"]["workorder_no"] == mine            # workorder_snapshot 同源
    assert out["trace"]["fetch_workorder"]["workorder_no"] == mine


@pytest.mark.asyncio
async def test_fetch_order_interrupts_when_missing(monkeypatch):
    # interrupt() 只能在编译图内跑(Task 1 冒烟 D:图外直接调是 RuntimeError),
    # 故缺单路径经最小编译图 + InMemorySaver ainvoke 测真实中断 surface。
    monkeypatch.setattr(nodes.business, "list_user_workorders",
                        lambda uid: [{"workorder_no": "1001", "business_type": "电能表", "status": "已完工", "amount": 99}])
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from app.graph.state import ConversationState
    b = StateGraph(ConversationState)
    b.add_node("fetch_workorder", nodes.fetch_workorder)
    b.add_edge(START, "fetch_workorder")
    b.add_edge("fetch_workorder", END)
    graph = b.compile(checkpointer=InMemorySaver())
    out = await graph.ainvoke({"resolved_query": "我要退费", "user_id": "u1"},
                              {"configurable": {"thread_id": "t-fetch"}})
    payload = out["__interrupt__"][0].value      # Task 1 冒烟 A/C 钉死此取法
    assert payload["type"] == "select_workorder"
    assert payload["workorders"][0]["workorder_no"] == "1001"


@pytest.mark.asyncio
async def test_fetch_order_报别人的单号也弹选择器(monkeypatch):
    # 退费子流程是确定性节点、不经 agent_tools,工具内那道归属校验够不着它。
    # 用户随口报一个别人的单号,不能照查,得跟「没给单号」一样落回选择器。
    monkeypatch.setattr(nodes.business, "list_user_workorders",
                        lambda uid: [{"workorder_no": "1001", "business_type": "电能表", "status": "已完工", "amount": 99}])
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from app.graph.state import ConversationState
    b = StateGraph(ConversationState)
    b.add_node("fetch_workorder", nodes.fetch_workorder)
    b.add_edge(START, "fetch_workorder")
    b.add_edge("fetch_workorder", END)
    graph = b.compile(checkpointer=InMemorySaver())
    out = await graph.ainvoke({"resolved_query": "工单 8888 我要退费", "user_id": "u1"},
                              {"configurable": {"thread_id": "t-foreign"}})
    payload = out["__interrupt__"][0].value
    assert payload["type"] == "select_workorder"                 # 没有照着 8888 查下去
    assert [o["workorder_no"] for o in payload["workorders"]] == ["1001"]


def test_list_user_orders_stable_and_queryable():
    from app.tools import business
    a = business.list_user_workorders("u-42")
    b = business.list_user_workorders("u-42")
    assert a == b and len(a) >= 2                            # 同 user 稳定
    snap = business.workorder_snapshot(a[0]["workorder_no"])
    assert snap["workorder_no"] == a[0]["workorder_no"] and snap["business_type"] == a[0]["business_type"]


# ---- Task 8: retrieve_policy 扩写多查 + 去重合并 ----

@pytest.mark.asyncio
async def test_retrieve_policy_expands_dedups_merges(monkeypatch):
    async def fake_expand(q):
        return ["退费政策", "无理由退费", "退费时限"]
    # 三条 query 各自召回:id=1 在两条里出现(取高分 0.9),id=2 只在一条
    per_query = {
        "退费政策": [{"id": 1, "question": "退费", "answer": "7天无理由", "rerank_score": 0.7,
                     "section_path": "政策/退费", "content_type": "policy"}],
        "无理由退费": [{"id": 1, "question": "退费", "answer": "7天无理由", "rerank_score": 0.9,
                       "section_path": "政策/退费", "content_type": "policy"},
                      {"id": 2, "question": "违约金", "answer": "计量故障商家承担", "rerank_score": 0.6,
                       "section_path": "政策/违约金", "content_type": "policy"}],
        "退费时限": [],
    }
    async def fake_search(q, **k):
        return per_query.get(q, [])
    monkeypatch.setattr(nodes.query_understanding, "expand_queries", fake_expand)
    monkeypatch.setattr(nodes.retrieval, "search_knowledge", fake_search)
    monkeypatch.setattr(nodes.retrieval, "arrange_head_tail", lambda h: h)

    out = await nodes.retrieve_policy({"resolved_query": "这个工单能退吗",
                                       "workorder_data": {"status": "已完工"}})
    assert len(out["citations"]) == 2                    # id=1 去重(取 0.9)+ id=2
    ids = [c["id"] for c in out["citations"]]
    assert ids == [1, 2]                                 # 按 rerank_score 降序(0.9, 0.6)
    assert "[1]" in out["evidence"] and "[2]" in out["evidence"]
    assert out["trace"]["retrieve_policy"]["hits"] == 2
    assert out["trace"]["retrieve_policy"]["queries"] == ["退费政策", "无理由退费", "退费时限"]


# ---- Task 9: submit_refund 拦截 + _agent_messages refund 适配 ----

@pytest.mark.asyncio
async def test_agent_tools_intercepts_submit_refund():
    uid = "u1"
    mine = nodes.business.list_user_workorders(uid)[0]["workorder_no"]
    ai = AIMessage(content="", tool_calls=[
        {"id": "r1", "name": "submit_refund", "args": {"workorder_no": mine, "reason": None}}])
    out = await nodes.agent_tools({"messages": [ai], "user_id": uid})
    assert out["suggested_actions"] == [{"type": "refund_form", "draft": {"workorder_no": mine, "reason": None}}]
    tm = out["messages"][0]
    assert tm.name == "submit_refund"                       # 合成 ToolMessage 促收敛
    assert "退费" in tm.content


def test_agent_messages_injects_order_and_policy_on_refund():
    msgs = nodes._agent_messages({
        "route": "refund_flow",
        "workorder_data": {"workorder_no": "1001", "status": "已完工", "business_type": "低压电流互感器"},
        "evidence": "[1] 退费: 7天无理由",
        "messages": [HumanMessage("这单能退吗")]})
    assert isinstance(msgs[0], SystemMessage)
    assert "7天无理由" not in msgs[0].content and "低压电流互感器" not in msgs[0].content  # 不进 system
    ctx = msgs[-1].content                                   # 本轮材料紧跟用户那句
    assert "7天无理由" in ctx                                 # 政策证据
    assert "低压电流互感器" in ctx and "submit_refund" in ctx       # 工单数据 + 判定指令
    # 顺序不能反:REFUND_JUDGE_HINT 的措辞假设证据已在前文给过
    assert ctx.index("7天无理由") < ctx.index("submit_refund")


def test_agent_messages_knowledge_path_still_injects_evidence():
    msgs = nodes._agent_messages({
        "route": "knowledge", "evidence": "[1] 违约金: 满99缴费",
        "messages": [HumanMessage("违约金多少")]})
    assert "满99缴费" not in msgs[0].content                  # 不进 system
    assert "满99缴费" in msgs[-1].content                     # 放宽条件后知识路不回归


# ---- Task 10: script_reply 按意图分文案 ----

@pytest.mark.asyncio
async def test_script_reply_by_intent():
    from app.core.prompts import SCRIPT_REPLY_CHITCHAT, SCRIPT_REPLY_OTHER
    chit = await nodes.script_reply({"intent": "闲聊"})
    assert chit["answer"] == SCRIPT_REPLY_CHITCHAT
    assert chit["trace"]["route"] == "fallback_script"
    other = await nodes.script_reply({"intent": "其他"})
    assert other["answer"] == SCRIPT_REPLY_OTHER
