# 阅读导航：多轮问题补全
# 本文件作用：结合历史把“那7月呢”补成完整问题；失败时保留用户原问题，不凭空编造上下文。
# 建议读法：这里主要调用提示词和模型，完整会话读写流程在agent.chat。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：从app.core.llm导入get_chat_model，复用对应模块的实现。
from app.core.llm import get_chat_model
# 学习注释：从app.core.prompts导入COREF_REWRITE_PROMPT_OUTAGE，复用对应模块的实现。
from app.core.prompts import COREF_REWRITE_PROMPT_OUTAGE


# 学习注释：用给定 prompt 做指代消解 + 口语归一。出错兜底返回原句(不阻断下游)。
async def _resolve_with(query: str, history: str, prompt) -> str:
    """用给定 prompt 做指代消解 + 口语归一。出错兜底返回原句(不阻断下游)。"""
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：get_chat_model的执行结果；保存到模型调用对象或模型名（model）。
        model = get_chat_model()
        # 学习注释：用竖线连接提示词模板和模型，传入query/history并异步请求补全；括号内是LangChain调用链。
        r = await (prompt | model).ainvoke(
            {"query": query, "history": history or "(无)"})
        # 学习注释：只接受字符串正文并去掉首尾空白；非字符串响应先视为空内容。
        text = (r.content if isinstance(r.content, str) else "").strip()
    except Exception:
        # 学习注释：返回用户查询文本（query），结束本次函数调用。
        return query
    # 学习注释：若模型给出非空补全问题就返回它；否则返回原query。Python的or在这里返回字符串，不是布尔值。
    return text or query


# 学习注释：根据近期历史补全追问；解析失败时回退为原问题。
async def resolve_outage(query: str, history: str = "") -> str:
    """停电台账问数语境的指代消解(线路/供服中心/事件/故障类型)。"""
    # 学习注释：使用停电业务专用补全提示词调用内部函数，返回完整问题或失败回退的原问题。
    return await _resolve_with(query, history, COREF_REWRITE_PROMPT_OUTAGE)
