# -*- coding: utf-8 -*-
# 阅读导航：工具编排与多轮会话
# 本文件作用：模型选择统计或检索工具，程序执行工具；生成回答后自检，失败时有限重试或展示工具结果。
# 建议读法：先读chat理解多轮入口，再读run理解单轮；tools.py提供工具，memory.py保存历史。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：下方早期模块介绍曾把多轮、自检写为后续计划；当前实现已经在chat/run中接入这些能力，以实际函数为准。
# 温度为0用于降低随机性，不保证完全复现；忠实度通过表示模型判定通过，不是客观事实已获人工确认。

"""停电台账问数：Agent 编排层 —— 模型自主选工具的最小 ReAct 循环。

设计目标：
  把「问数工具」和「相似检索工具」交给模型，让它在 Agent 循环里**自主决定**：
    - 用户问的是统计数字 → 调 query_outage_stat
    - 用户想找相似案例 → 调 search_similar_events
    - 用户既问数字又要佐证 → 两个都调，再组合回答

这层是「代码硬路由 → Agent 自主编排」的升级。之前 ask.py/similar.py 是被调用方
按 if/else 分别调的，模型只知道「填参数」、不知道「有哪些能力可选」；现在把能力
包装成工具喂给模型，由它自己决策调用哪个、调几次、怎么组合。

为什么用 create_agent 而不是手写 StateGraph：
  - 项目已装 langgraph，create_agent 是官方 ReAct 封装，自带工具调用循环和停止条件，
    一个最小 Agent 不需要自己重写 tool_node / 循环边，避免手写出错。
  - 复用 app.core.llm 的 get_chat_model（已修好流式用量、function calling 只认 tool_calls）。

边界说明：
  - 这是「最小可跑」的 Agent 骨架，聚焦「模型自主选工具」这一件事。
  - 多轮历史、指代消解（coref）、回答自检等后续可在本层之上再叠，不在此步引入。
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入logging：分级日志，便于排查运行失败。
import logging
# 学习注释：导入weakref：弱引用，让没有使用者的会话锁能够被回收。
import weakref

# 学习注释：从langchain.agents引入create_agent，用于创建模型选择工具并循环执行的Agent。
from langchain.agents import create_agent
# 学习注释：从langchain_core.messages引入AIMessage, HumanMessage，用于区分用户、助手和工具消息。
from langchain_core.messages import AIMessage, HumanMessage

# 学习注释：从app.core导入coref, faithfulness, llm, versioning，复用对应模块的实现。
from app.core import coref, faithfulness, llm, versioning
# 学习注释：从app.core.memory导入SessionStore，复用对应模块的实现。
from app.core.memory import SessionStore
# 学习注释：从app.core.tools导入AGENT_TOOLS，复用对应模块的实现。
from app.core.tools import AGENT_TOOLS

# 学习注释：创建以当前模块名标识的日志记录器；保存到logger。
logger = logging.getLogger(__name__)

# Agent 系统提示词：说清身份、能力、以及「何时用哪个工具」的边界。
_SYSTEM_PROMPT = (
    "你是「停电台账问数助手」。你面对的是当前发布的脱敏抽样停电台账，"
    "能帮用户查两类信息：\n\n"
    "1. **结构化统计**（用 query_outage_stat 工具）：某线路停电次数、某供服中心缺供电量、"
    "某时间段停电事件数、各类停电性质占比。\n"
    "2. **相似事件检索**（用 search_similar_events 工具）：找与描述相似的历史停电事件，"
    "了解某类故障以前怎么发生、涉及什么设备。\n\n"
    "工作原则：\n"
    "- 用户要一个「数值/次数/占比」→ 调 query_outage_stat；要「相似案例/某类故障」→ 调 search_similar_events。\n"
    "- 用户的问题需要数字又需要佐证时，可以两个工具都调，再综合回答。\n"
    "- 给统计结果举例时，两个工具必须使用相同的线路、供服中心和时间条件，检索采用 record_start。"
    "若显式使用 event_overlap，必须说明案例按时间重叠筛选，不能称为刚才计数集合中的例子。\n"
    "- empty 仅表示本次没有召回；out_of_scope 表示数据范围外；processing_error 表示处理未完成。"
    "后两者不能回答成没有事件。数据版本未校验时保留工具告警。\n"
    "- insufficient_evidence表示已找到候选但没有得到依据充分的案例；说明本次依据不足，"
    "不能断言该范围没有此类事件，不得删掉用户范围重新查来凑答案。部分案例待确认时保留说明。\n"
    "- 回答必须基于工具返回的结果，工具没查到的不要编造；工具返回了「未得出结果」，"
    "就如实告诉用户并追问澄清，不要硬答。\n"
    "- 回答要简洁、说人话，关键数字后面可以附上来源口径（工具返回里带了「依据」）。\n"
    "- 检索回答用‘本次展示N条案例’，N来自最终展示案例数。召回候选数是重排前数量，"
    "二者不可混称，更不能作为范围内停电总数。\n"
    "- 停电性质按工具attrs及性质明细引用；故障原因只能复述对应正文中明确的描述。"
    "不得自行新增‘外力破坏型’等分类标签，不能把原因解释替换成台账停电性质。\n"
    "- 缺供电量是筛选后的 users 用户行直接求和，不做事件去重。时间过滤与指标聚合是两个概念，不得混用。\n"
    "- 占比工具只支持全量事件的多标签分布，不支持单标签重算，也不支持线路/供服中心过滤。"
    "不要向用户承诺或主动提出这些尚未实现的统计功能。\n"
    "- 月份不带年份时默认使用请求上下文中的数据起始年份；今年、去年、上个月按当前日期理解。"
    "不能把明确超出数据范围的年份改写成数据年份。以工具返回的范围及告警为准。"
)


# 学习注释：把聊天模型、工具列表和系统规则组装成Agent；此处构造对象，真正的网络调用在ainvoke。
def _build_agent():
    """构建 Agent。temperature=0 求可复现（问数要的是稳，不是多样性）。"""
    # 学习注释：创建支持工具编排的聊天模型，降低采样温度；这不是训练模型，也不能保证输出完全相同。
    model = llm.get_chat_model(streaming=True, temperature=0)
    # 学习注释：把模型、两个业务工具和系统规则交给LangChain的Agent循环。
    return create_agent(
        model,
        tools=AGENT_TOOLS,
        system_prompt=_SYSTEM_PROMPT,
    )


# 模块级单例（懒加载，避免 import 时就把模型初始化了）
_AGENT = None


# 学习注释：首次调用时创建Agent，之后复用，避免每个请求重复初始化。
def _get_agent():
    # 学习注释：声明下方修改的是模块级共享变量_AGENT，而非函数局部同名变量。
    global _AGENT
    # 学习注释：只有_AGENT尚未设置（None）才进入这一分支。
    if _AGENT is None:
        # 学习注释：_build_agent的执行结果；保存到_AGENT。
        _AGENT = _build_agent()
    # 学习注释：返回_AGENT，结束本次函数调用。
    return _AGENT


# 学习注释：从同一轮消息中提取最终回答、工具名和工具证据，防止重试后新旧内容混用。
def _extract_answer_and_tools(messages: list) -> tuple[str, list, list]:
    """从一轮 Agent 消息流里抽出最终回答 + 工具调用名 + 工具返回内容。

    返回 (answer, tool_calls, tool_results)。三者必须来自**同一轮消息**，
    否则回答与溯源会互相矛盾（审阅问题 2 的核心）。
    """
    # 学习注释：''；保存到生成回答（answer）。
    answer = ""
    # 学习注释：从最后一条消息向前找助手的本轮结束状态；不能把早先的执行计划当最终答案。
    for m in reversed(messages):
        # 学习注释：只有getattr(m, 'type', '')等于'ai'才进入这一分支。
        if getattr(m, "type", "") == "ai":
            # 最后一条 AI 消息才代表本轮完成状态；不能退回前面的工具计划文本。
            # 学习注释：最后的助手消息如果仍要求调用工具，就不把其内容当已经完成的回答。
            if not getattr(m, "tool_calls", None):
                # 学习注释：读取m的'content'属性，缺失时使用给定默认值；保存到content。
                content = getattr(m, "content", "")
                # 学习注释：只有检查content是否属于str类型才进入这一分支。
                if isinstance(content, str):
                    # 学习注释：content；保存到生成回答（answer）。
                    answer = content
                # 学习注释：只有检查content是否属于list类型才进入这一分支。
                elif isinstance(content, list):
                    # 学习注释：兼容内容块形式的模型响应：只拼文本块，不把其他类型块当文字展示。
                    answer = "".join(
                        block if isinstance(block, str) else block.get("text", "")
                        for block in content if isinstance(block, str) or
                        isinstance(block, dict) and block.get("type") == "text")
            # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
            break
    # 学习注释：空列表；保存到本轮使用过的工具名称（tool_calls）。
    tool_calls = []
    # 学习注释：空列表；保存到工具实际返回的证据（tool_results）。
    tool_results = []
    # 学习注释：遍历模型与工具的消息序列（messages），每轮把当前项交给m。
    for m in messages:
        # 学习注释：只有getattr(m, 'type', '')等于'tool'才进入这一分支。
        if getattr(m, "type", "") == "tool":
            # 学习注释：读取m的'name'属性，缺失时使用给定默认值；保存到name。
            name = getattr(m, "name", "")
            # 学习注释：只有name为真/非空，并且name不包含在本轮使用过的工具名称（tool_calls）才进入这一分支。
            if name and name not in tool_calls:
                # 学习注释：向本轮使用过的工具名称（tool_calls）末尾添加name。
                tool_calls.append(name)
            # 学习注释：保留每次工具实际返回的content和artifact，后续自检使用同一份材料。
            tool_results.append({"tool": name, "content": getattr(m, "content", ""),
                                 "artifact": getattr(m, "artifact", None)})
    # 学习注释：返回3项元组：生成回答（answer）、本轮使用过的工具名称（tool_calls）、工具实际返回的证据（tool_results），结束本次函数调用。
    return answer, tool_calls, tool_results


# 学习注释：执行一轮Agent并做回答自检；默认最多重试两次，仍不通过或无法验证则降级。
@versioning.pinned_dataset
async def run(query: str, self_check: bool = True, max_retries: int = 2) -> dict:
    """Agent 主入口：输入自然语言，返回自主编排后的回答。

    参数：
      - self_check: 是否在生成后跑忠实度自检（默认开）。关掉可省一次 LLM 调用。
      - max_retries: 忠实度不通过时的重新生成次数上限（默认 2）。

    返回 dict：
      - answer: 最终回答文本
      - messages: 完整消息流（含工具调用记录，可观测/留痕）
      - tool_calls: 本次实际调用了哪些工具（名字列表），便于验证「自主编排」是否发生
      - tool_results: 工具实际返回的内容列表（忠实度自检的证据来源）
      - verification_status: 忠实度三态 "faithful"/"unfaithful"/"unverified"/None
        （None 表示未自检，即 self_check=False）。与旧 faithful 布尔并存，向下兼容。
      - faithful: 忠实度布尔（status=="faithful" 时为 True；unfaithful/unverified 为 False；None 未自检）
      - faith_reason: 忠实度判定依据
      - degraded: 是否因「忠实度反复不通过 / 无法验证」而降级为直接呈现工具事实

    忠实度闸门（二次审阅问题 1/2）：
      1. 生成回答 → 忠实度自检，得到三态 status；
      2. status == "unfaithful" → 有限次数（max_retries）重新生成。重试**重新跑一轮 Agent**，
         并从**新消息流**里重新抽取答案+工具调用+工具结果，原子更新整份返回——
         绝不把「新答案」和「旧证据」混搭（否则溯源矛盾）；
      3. 仍 unfaithful，或 status == "unverified"（检查器本身出错，无从判断）→ 降级：
         不再返回可能编造的自然语言答案，而是直接呈现可验证的工具事实。
         unverified 与 unfaithful 同等对待——**未经确认的答案都不放行、都不进历史**。
      这样「忠实度闸」真正拦住所有「未确认」的答案，而不是只在「明确不忠实」这一条路上拦截。
    """
    # 学习注释：_get_agent的执行结果；保存到agent。
    agent = _get_agent()

    # 学习注释：执行完整的一轮工具编排；一次Agent执行可能包含多次模型和工具调用。
    async def _invoke_once() -> tuple[str, list, list, list]:
        """跑一轮 Agent，返回 (answer, tool_calls, tool_results, messages)。"""
        # 学习注释：从datetime引入date，用于日期时间解析与计算。
        from datetime import date
        # 学习注释：versioning.current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
        snapshot = versioning.current_snapshot()
        # 学习注释：把当前日期与台账覆盖范围提供给模型，避免把“今年”随意改成数据年份。
        context = (f"当前日期：{date.today().isoformat()}。本次台账声明范围："
                   f"[{snapshot.coverage_start}, {snapshot.coverage_end})，"
                   f"范围依据：{snapshot.coverage_basis}。")
        # 学习注释：真正执行Agent：模型可能先要求工具调用，收到结果后再继续生成答案。
        result = await agent.ainvoke({"messages": [("system", context), ("user", query)]})
        # 学习注释：从result读取键'messages'，缺少时取空列表；保存到消息列表（msgs）。
        msgs = result.get("messages", [])
        # 学习注释：从这一轮的同一消息序列同时提取回答、工具名和证据，保证互相对应。
        ans, calls, tres = _extract_answer_and_tools(msgs)
        # 学习注释：返回4项元组：ans、calls、tres、消息列表（msgs），结束本次函数调用。
        return ans, calls, tres, msgs

    # 学习注释：_invoke_once的执行结果，await等待结果后再继续；保存到生成回答（answer）、本轮使用过的工具名称（tool_calls）、工具实际返回的证据（tool_results）、模型与工具的消息序列（messages）。
    answer, tool_calls, tool_results, messages = await _invoke_once()

    # 忠实度自检 + 重试 + 降级
    verification_status = None
    # 学习注释：None（尚无值）；保存到回答是否被检查器判为忠实（faithful）。
    faithful = None
    # 学习注释：''；保存到检查器给出的理由（faith_reason）。
    faith_reason = ""
    # 学习注释：False（不成立）；保存到是否已降级为工具结果展示（degraded）。
    degraded = False
    # 学习注释：空回答不能当正常成功；直接标记无法验证并尝试展示工具事实。
    if not answer.strip():
        # 学习注释：3项元组：'unverified'、False（不成立）、True（成立）；保存到回答忠实度检查状态（verification_status）、回答是否被检查器判为忠实（faithful）、是否已降级为工具结果展示（degraded）。
        verification_status, faithful, degraded = "unverified", False, True
        # 学习注释：'模型未返回有效回答'；保存到检查器给出的理由（faith_reason）。
        faith_reason = "模型未返回有效回答"
        # 学习注释：_degrade_to_facts的执行结果；保存到生成回答（answer）。
        answer = _degrade_to_facts(tool_results)
    # 学习注释：只有self_check为真/非空才进入这一分支。
    elif self_check:
        # 学习注释：把回答和本轮工具证据交给检查器；这是模型判断，不等于人工事实认证。
        check = await faithfulness.check_faithful(answer, tool_results)
        # 学习注释：check中键/位置为'status'的值；保存到status。
        status = check["status"]
        # 学习注释：check中键/位置为'reason'的值；保存到检查器给出的理由（faith_reason）。
        faith_reason = check["reason"]
        # 学习注释：status；保存到回答忠实度检查状态（verification_status）。
        verification_status = status
        # 学习注释：只有faithful状态才记为通过；unverified与unfaithful都不算通过。
        faithful = (status == "faithful")

        # 只有「确定不忠实」才重试；「未验证」直接降级（重试也未必能修好检查器本身）
        if status == "unfaithful":
            # 学习注释：最多再运行max_retries轮Agent；默认2次，每次可能再次调用工具并增加费用。
            for _ in range(max_retries):
                # 学习注释：整轮重试后得到一组新的回答与证据，不能沿用上轮旧证据。
                retry_answer, retry_calls, retry_tres, retry_msgs = await _invoke_once()
                # 相同字符串答案也要重新校验（可能基于不同证据），不靠字符串相同跳过
                recheck = (await faithfulness.check_faithful(retry_answer, retry_tres)
                           if retry_answer.strip() else
                           {"status": "unverified", "reason": "模型未返回有效回答"})
                # 原子更新：答案/工具调用/工具结果/消息流 一起换，保证来自同一轮
                answer, tool_calls, tool_results, messages = (
                    # 学习注释：整轮重试后得到一组新的回答与证据，不能沿用上轮旧证据。
                    retry_answer, retry_calls, retry_tres, retry_msgs)
                # 学习注释：recheck中键/位置为'status'的值；保存到回答忠实度检查状态（verification_status）。
                verification_status = recheck["status"]
                # 学习注释：recheck中键/位置为'reason'的值；保存到检查器给出的理由（faith_reason）。
                faith_reason = recheck["reason"]
                # 学习注释：recheck['status']等于'faithful'；保存到回答是否被检查器判为忠实（faithful）。
                faithful = (recheck["status"] == "faithful")
                # 学习注释：检查通过即停止；检查器本身不可用也停止重试，避免继续做无法验证的生成。
                if faithful or verification_status == "unverified":
                    # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                    break
            # 重试后仍不通过 → 降级
            if not faithful:
                # 学习注释：True（成立）；保存到是否已降级为工具结果展示（degraded）。
                degraded = True
                # 学习注释：_degrade_to_facts的执行结果；保存到生成回答（answer）。
                answer = _degrade_to_facts(tool_results)
        # 学习注释：只有status等于'unverified'才进入这一分支。
        elif status == "unverified":
            # 检查器本身失败，无从判断忠实与否 → 与 unfaithful 同等降级，不放行未确认答案
            degraded = True
            # 学习注释：_degrade_to_facts的执行结果；保存到生成回答（answer）。
            answer = _degrade_to_facts(tool_results)

    # 学习注释：返回包含answer、tool_calls、tool_results、evidence、messages、verification_status的字典，结束本次函数调用。
    return {
        **versioning.evidence_context(),
        "answer": answer,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "evidence": [r["artifact"] for r in tool_results if r.get("artifact") is not None],
        "messages": messages,
        "verification_status": verification_status,
        "faithful": faithful,
        "faith_reason": faith_reason,
        "degraded": degraded,
    }


# 学习注释：停止依赖自由生成回答，改为展示工具提供的文本；原数据是否正确仍需独立核实。
def _degrade_to_facts(tool_results: list[dict]) -> str:
    """忠实度反复不通过时的降级答案：不编自然语言，直接呈现可验证的工具事实。

    原则：编造的自然语言答案是「不可信的」，但工具返回的原始事实是「可验证的」。
    降级就是把后者原样交还给用户，让用户自己对数字负责，而不是替用户圆一个可能错的数。
    """
    # 学习注释：取出工具已经提供的可读文本；降级时不再次让模型补写结论。
    facts = [r.get("content", "") for r in tool_results if r.get("content")]
    # 学习注释：遍历facts，只保留满足条件的项，将f收集为列表；保存到facts。
    facts = [f for f in facts if f.strip()]
    # 学习注释：只有facts为空或为False才进入这一分支。
    if not facts:
        # 学习注释：返回'抱歉，我暂时没能查到可信的结果，也无法确认回答的准确性。请换个问法，或补充线路/供服中心/时间段等信息。'，结束本次函数调用。
        return "抱歉，我暂时没能查到可信的结果，也无法确认回答的准确性。请换个问法，或补充线路/供服中心/时间段等信息。"
    # 学习注释：用空行连接各工具结果，保留原工具范围与告警。
    body = "\n\n".join(facts)
    # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
    return (
        "暂时无法确认生成回答的准确性，以下直接给出查到的原始数据供你核对：\n\n"
        f"{body}"
    )


# ---- 多轮会话（指代消解 + 历史管理）----

# 会话内存存储：session_id -> 消息列表。本地验证够用，不做持久化。
_SESSIONS = SessionStore()

# 每会话一把 asyncio 锁：同一 session 的请求串行处理，防并发乱序
# （三次审阅问题 5：否则「这条线」这类追问会读到还没写入的第一轮答案）。
# clear_session 也走同一把锁，避免「清空后旧请求又写回历史」。
# 获取/等待锁的协程持有强引用；没有持有者或等待者后允许回收。
# 不能在 clear_session 中直接 pop：已有等待者时会产生两把同名锁。
_session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


# 学习注释：获取同一session共用的异步锁；弱引用注册表不永久持有闲置锁。
def _lock_for(session_id: str) -> asyncio.Lock:
    # 学习注释：查找该session已有的锁；同一会话必须共享一把锁才能保证串行。
    lock = _session_locks.get(session_id)
    # 学习注释：只有本会话的互斥锁（lock）尚未设置（None）才进入这一分支。
    if lock is None:
        # 学习注释：创建异步互斥锁，使同一会话的请求依次处理；保存到本会话的互斥锁（lock）。
        lock = asyncio.Lock()
        # 学习注释：把锁登记到弱引用表；等待或持有它的协程仍有强引用，闲置后才可回收。
        _session_locks[session_id] = lock
    # 学习注释：返回本会话的互斥锁（lock），结束本次函数调用。
    return lock


# 学习注释：把最近问答转成补全模型可读的文本，用户问题优先使用已补全版本。
def _history_text(session_id: str, max_turns: int = 6) -> str:
    """把某会话最近 N 轮的问答拼成 coref 可读的纯文本历史（每行一条）。"""
    # 学习注释：读取指定会话消息，缺会话时得到空列表，并更新最近访问顺序。
    msgs = _SESSIONS.get(session_id)
    # 学习注释：只有消息列表（msgs）为空或为False才进入这一分支。
    if not msgs:
        # 学习注释：返回''，结束本次函数调用。
        return ""
    # 学习注释：空列表；保存到lines。
    lines = []
    # 学习注释：最近max_turns轮通常对应两倍条消息：一条问题加一条回答。
    for m in msgs[-max_turns * 2:]:
        # 学习注释：检查m是否属于HumanMessage类型时取'用户'，否则取'助手'；保存到role。
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（getattr(m, 'content', '') or ''）；保存到content。
        content = getattr(m, "content", "") or ""
        # 学习注释：只有检查m是否属于HumanMessage类型才进入这一分支。
        if isinstance(m, HumanMessage):
            # 学习注释：用户历史优先保留完整补全问题，防止较早实体被裁剪后只剩“那7月呢”。
            content = m.additional_kwargs.get("resolved_query") or content
        # 学习注释：只有content为真/非空才进入这一分支。
        if content:
            # 学习注释：向lines末尾添加把花括号内的变量填入f字符串，生成文字。
            lines.append(f"{role}: {content}")
    # 学习注释：返回用指定分隔符连接各段文本，结束本次函数调用。
    return "\n".join(lines)


# 学习注释：锁住本会话，读取历史、补全问题、执行单轮，成功后成对写入问答。
async def chat(session_id: str, query: str) -> dict:
    """多轮会话入口：先指代消解补全，再进 Agent，最后把本轮问答写回历史。

    与 run() 的区别：
      - run() 单轮，无历史、无指代消解；
      - chat() 多轮，用 SessionStore 存历史，每轮先 coref.resolve_outage 把
        「这条线」「那次停电」这类依赖上下文的半截话补成完整问句，再交给 Agent。

    返回 dict 在 run() 基础上多一个 resolved_query（补全后的问题），便于观察指代消解效果。
    """
    # 同一 session 串行：拿到锁再走「读历史 → 指代消解 → 跑 Agent → 写回历史」整段，
    # 避免并发下第二个请求读到「第一轮还没写答案」的残缺历史。
    async with _lock_for(session_id):
        # 学习注释：_history_text的执行结果；保存到用于补全追问的历史文本（history）。
        history = _history_text(session_id)
        # 无历史时跳过指代消解：coref 本质是「结合历史补全半截话」，首轮没有历史可依，
        # 跑一遍反而多一次 LLM 调用、还可能在无上下文时越改越偏。首轮 query 原样交给 Agent。
        # 学习注释：有历史才调用补全模型；首轮原样处理，避免无上下文时误改或多一次调用。
        resolved = await coref.resolve_outage(query, history) if history else query

        # 用补全后的完整问题跑 Agent（单轮 run，不带历史消息，Agent 只看到补全后的一句）
        # 学习注释：将完整问题交给单轮Agent；当前实现通过补全传递上下文，不把整个历史直接塞入Agent。
        result = await run(resolved)

        # 只有「确认忠实」或「未自检（用户显式关闭）」的回答才写回历史。
        # 降级答案（unfaithful 重试失败 / unverified 检查器出错）都是「未经确认」的，
        # 不能当作已确认事实进历史，否则下一轮指代消解会把可能编造的内容当成依据。
        # （二次审阅问题 1：unverified 与 unfaithful 同等对待，都不进历史。）
        # 学习注释：只保存有回答且未降级的完整问答；失败、取消和降级不留下半轮问题。
        if not result.get("degraded") and result.get("answer"):
            # 成功后成对写入；异常、取消、降级都不能留下半轮问题并挤掉可信历史。
            # 学习注释：向指定会话追加后续消息参数；第一个参数是会话ID，不是要显示的消息内容。
            _SESSIONS.append(session_id,
                HumanMessage(content=query, additional_kwargs={"resolved_query": resolved}),
                AIMessage(content=result["answer"]))
        # 学习注释：返回包含resolved_query的字典，结束本次函数调用。
        return {
            **result,
            "resolved_query": resolved,
        }


# 学习注释：用同一把会话锁清空历史，避免进行中的请求在清空后又写回。
async def clear_session(session_id: str) -> None:
    """清空某会话历史（可选：测试或重置对话用）。

    与 chat 用同一把锁：确保「清空」发生在任意进行中的请求写回之后，
    不会出现「清空了、旧请求又把自己那轮写回来」的竞态。
    """
    # 学习注释：异步取得本会话锁，整个读历史/执行/写回过程串行；退出块时释放锁。
    async with _lock_for(session_id):
        # 学习注释：在会话锁保护下删除历史；进行中的请求需先结束，避免旧请求随后写回。
        _SESSIONS._sessions.pop(session_id, None)


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：导入asyncio：异步任务、等待、锁和超时。
    import asyncio

    # 学习注释：异步函数_demo接收调用时的上下文；把这一段可复用处理封装起来。
    async def _demo():
        # 学习注释：4项列表：'线路1 去年停了几次电'、'有没有电缆故障导致的停电事件'、'今年6月停了几次电，顺便看看有没有类似案例'、'帮我查一下天气'；保存到tests。
        tests = [
            "线路1 去年停了几次电",          # 纯问数 → 应只调 query_outage_stat
            "有没有电缆故障导致的停电事件",    # 纯检索 → 应只调 search_similar_events
            "今年6月停了几次电，顺便看看有没有类似案例",  # 组合 → 应两个都调
            "帮我查一下天气",                # 越界 → 应不调工具，礼貌拒绝
        ]
        # 学习注释：遍历tests，每轮把当前项交给q。
        for q in tests:
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"\n{'='*60}\nQ: {q}")
            # 学习注释：run的执行结果，await等待结果后再继续；保存到r。
            r = await run(q)
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"调用工具: {r['tool_calls']}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"回答: {r['answer']}")

        # 多轮指代消解演示
        print(f"\n{'='*60}\n【多轮对话】session=multi-test")
        # 学习注释：'multi-test'；保存到sid。
        sid = "multi-test"
        # 学习注释：clear_session的执行结果，await等待结果后再继续。
        await clear_session(sid)
        # 学习注释：3项列表：'线路1 去年停了几次电'、'那这条线今年6月呢'、'有没有和它类似的电缆故障事件'；保存到turns。
        turns = [
            "线路1 去年停了几次电",
            "那这条线今年6月呢",           # 指代「这条线」→ 线路1
            "有没有和它类似的电缆故障事件",   # 指代「它」→ 线路1/电缆故障
        ]
        # 学习注释：遍历turns，每轮把当前项交给q。
        for q in turns:
            # 学习注释：chat的执行结果，await等待结果后再继续；保存到r。
            r = await chat(sid, q)
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"\nQ: {q}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"  → 补全: {r['resolved_query']}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"  调用工具: {r['tool_calls']}")
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"  回答: {r['answer']}")

    # 学习注释：启动事件循环，执行脚本的异步主函数。
    asyncio.run(_demo())
