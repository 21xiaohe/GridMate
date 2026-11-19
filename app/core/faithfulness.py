# -*- coding: utf-8 -*-
# 阅读导航：回答与证据核对
# 本文件作用：比较生成回答与工具证据，返回faithful/unfaithful/unverified，供agent决定重试或降级。
# 建议读法：忠实表示模型认为回答受证据支持，不等于原始台账事实已经人工核实。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：检查器异常的实际返回是unverified（无法验证），不是unfaithful（明确不忠实）；Agent对两者都不直接放行生成答案。
# 检查器的空回答兼容分支不能单独作为成功判据；当前Agent在调用检查器前已将空回答判为未验证并降级。

"""停电台账问数：回答忠实度自检（生成后）。

Agent 已经产出了回答之后，本模块用台账专属的 FAITHFULNESS_SYSTEM_OUTAGE 判断回答里的
数字/事件能否被工具返回结果支撑，拦住「工具没给、模型自己编」的杜撰。

为什么单独写台账版、不直接复用客服的 FAITHFULNESS_SYSTEM：
  - 客服版盯的是「退费规则/违约金/时效/换表维护」这类政策事实；
  - 台账问数盯的是「停电次数/缺供电量/占比」这类统计数字，以及「把 A 线路的事件安到 B 线路」
    这类归属张冠李戴。判定对象完全不同，prompt 必须重写。

可靠性原则：
  模型自检失败时**宁可判「不忠实」也不放行**——这道闸的职责就是拦住编造，它自己挂掉时无从判断，
  放行等于把没过闸的回答直接交给用户。
"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入logging：分级日志，便于排查运行失败。
import logging

# 学习注释：从pydantic引入BaseModel, Field，用于字段类型、默认值与数据结构校验。
from pydantic import BaseModel, Field

# 学习注释：从app.core导入llm，复用对应模块的实现。
from app.core import llm
# 学习注释：从app.core.evidence导入ToolEvidence, render_bundle，复用对应模块的实现。
from app.core.evidence import ToolEvidence, render_bundle
# 学习注释：从app.core.prompts导入FAITHFULNESS_PROMPT_OUTAGE，复用对应模块的实现。
from app.core.prompts import FAITHFULNESS_PROMPT_OUTAGE

# 学习注释：创建以当前模块名标识的日志记录器；保存到logger。
logger = logging.getLogger(__name__)


# 学习注释：_FaithCheck定义数据字段与默认值；继承的Pydantic会在创建/解析时检查类型和约束。
class _FaithCheck(BaseModel):
    # 学习注释：声明字段：回答里的具体事实是否都能被工具结果支撑；保存到回答是否被检查器判为忠实（faithful）。
    faithful: bool = Field(description="回答里的具体事实是否都能被工具结果支撑")
    # 学习注释：声明字段：判断依据，指出编造的是哪一句；保存到判定或失败原因（reason）。
    reason: str = Field(default="", description="判断依据，指出编造的是哪一句")


# 学习注释：函数_chain接收调用时的上下文；把这一段可复用处理封装起来。
def _chain():
    # 当评委的结构化链路显式传 temperature=0，求可复现（见 llm.py 的说明）
    # 学习注释：竖线把提示词模板与结构化模型串接：先填入回答/证据，再解析_FaithCheck结果。
    return FAITHFULNESS_PROMPT_OUTAGE | llm.structured(_FaithCheck, temperature=0)


# 学习注释：核对回答与同一份工具证据，服务异常时返回unverified而非伪装成通过。
async def check_faithful(answer: str, tool_results: list[dict]) -> dict:
    """评审回答的忠实度。

    tool_results: 工具实际返回的结果列表，每条是一个 dict（问数/检索工具返回的原始内容）。
      这些 content 是 tools.py 用统一证据模型（evidence.py）渲染出的**完整证据文本**，
      与回答生成消费的是同一份——忠实度评审不重新拼摘要，直接用这份同版本证据。
    返回 {"status": "faithful"|"unfaithful"|"unverified", "reason": str}。

    三种状态（审阅第 5 条：别把「检查器异常」和「确定不忠实」混为一谈）：
      - faithful   回答里的数字/事件都能被证据支撑，可放行；
      - unfaithful 评委明确判「有编造」，必须降级处理，不能直接返回给用户；
      - unverified 检查器本身出错（LLM 调用失败等），此时无从判断忠实与否，
                   应单独标记，不能当成「确定不忠实」拦截，也不能当「忠实」放行。
    """
    # 学习注释：辅助检查器保留了空回答返回faithful的历史分支；Agent主流程已提前拦截空回答并降级。
    if not answer or not answer.strip():
        # 学习注释：返回包含status、reason的字典，结束本次函数调用。
        return {"status": "faithful", "reason": "空回答"}

    # 学习注释：空列表；保存到evidence_parts。
    evidence_parts = []
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、r。
    for i, r in enumerate(tool_results, 1):
        # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
        try:
            # 学习注释：优先解析工具的结构化artifact，再用统一渲染器还原证据，避免另写一套不一致摘要。
            bundle = ToolEvidence.model_validate(r.get("artifact"))
            # 学习注释：render_bundle的执行结果；保存到rendered。
            rendered = render_bundle(bundle)
        except (ValueError, TypeError):
            # 学习注释：返回包含status、reason的字典，结束本次函数调用。
            return {"status": "unverified", "reason": "工具缺少有效的结构化证据"}
        # 学习注释：只有rendered不等于从r读取键'content'，缺少时取None才进入这一分支。
        if rendered != r.get("content"):
            # 学习注释：返回包含status、reason的字典，结束本次函数调用。
            return {"status": "unverified", "reason": "工具文本与结构化证据不一致"}
        # 学习注释：收集这一工具的证据文本，交给后续模型逐项核对。
        evidence_parts.append(f"[{i}] {rendered}")
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（'\n'.join(evidence_parts) or '(无工具结果)'）；保存到evidence。
    evidence = "\n".join(evidence_parts) or "(无工具结果)"

    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：异步执行_chain()调用，输入包含本次问题或检查材料，await等待结果后再继续；保存到r。
        r: _FaithCheck = await _chain().ainvoke({"evidence": evidence, "answer": answer})
    except Exception:
        # 学习注释：记录warning级别日志，用于定位运行状态。
        logger.warning("忠实度自检失败，标记为未验证", exc_info=True)
        # 学习注释：返回包含status、reason的字典，结束本次函数调用。
        return {"status": "unverified", "reason": "忠实度自检失败，无法验证"}
    # 学习注释：返回包含status、reason的字典，结束本次函数调用。
    return {
        "status": "faithful" if r.faithful else "unfaithful",
        "reason": r.reason or "",
    }
