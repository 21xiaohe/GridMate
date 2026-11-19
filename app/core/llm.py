# 阅读导航：聊天模型适配层
# 本文件作用：统一创建模型客户端、设置结构化输出和有限重试，并适配已有服务的返回格式差异。
# 建议读法：初读先看get_chat_model与structured；流式累计用量适配可留到理解API后再看。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：原有注释提及flywheel、Langfuse和旧预算控制，是历史适配背景，不能据此宣称当前停电版仍集成这些功能。
# 下方服务商兼容经验只描述当时通道行为；当前structured按模型家族选择流式/非流式，不是所有结构化输出都必须非流式。

# 学习注释：导入logging：分级日志，便于排查运行失败。
import logging

# 学习注释：从langchain_core.exceptions引入OutputParserException，用于结构化结果解析失败的异常类型。
from langchain_core.exceptions import OutputParserException
# 学习注释：从langchain_openai引入ChatOpenAI，用于把聊天API封装成LangChain模型对象。
from langchain_openai import ChatOpenAI
# 学习注释：从pydantic引入PrivateAttr，用于字段类型、默认值与数据结构校验。
from pydantic import PrivateAttr

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings

# 学习注释：创建以当前模块名标识的日志记录器；保存到logger。
logger = logging.getLogger(__name__)

# 学习注释：4项集合：'1'、'true'、'yes'、'on'；保存到_TRUTHY。
_TRUTHY = {"1", "true", "yes", "on"}


# 学习注释：把当前累计token计数减去前次累计值，得到这一流式块的增量。
def _diff_usage(cur: dict, prev: dict) -> dict:
    """把累计用量换成相对上一个 chunk 的增量,嵌套的 *_details 一起换。"""
    # 学习注释：空字典；保存到out。
    out: dict = {}
    # 学习注释：遍历读取cur的键和值配对，每轮把当前项交给k、v。
    for k, v in cur.items():
        # 学习注释：从prev读取键k，缺少时取None；保存到p。
        p = prev.get(k)
        # 学习注释：只有检查v是否属于dict类型才进入这一分支。
        if isinstance(v, dict):
            # 学习注释：用相同方法递归处理嵌套的token明细，不只处理顶层总量。
            out[k] = _diff_usage(v, p if isinstance(p, dict) else {})
        # 学习注释：只有检查v是否属于(int, float)类型才进入这一分支。
        elif isinstance(v, (int, float)):
            # 学习注释：累计值减去上次累计值，得到当前块增量；max(0,...)避免出现负用量。
            out[k] = max(0, v - (p if isinstance(p, (int, float)) else 0))
        else:
            # 学习注释：v；保存到out[k]。
            out[k] = v
    # 学习注释：返回out，结束本次函数调用。
    return out


# 学习注释：定义_CumulativeUsageChatOpenAI对象，集中保存下方属性和方法。
class _CumulativeUsageChatOpenAI(ChatOpenAI):
    """修正流式用量被重复累加的问题。

    有些 OpenAI 兼容通道在**每个** chunk 上都重报一遍**累计**用量,而 langchain-openai
    把各 chunk 的用量**相加**。两件事凑到一起,一次调用的 token 数会被放大到 chunk 条数倍
    ——实测 24 个 chunk、input 恒为 11,聚合出来是 264。flywheel 的成本账按这个数统计,
    整体虚高一个量级;agent 早先那道 token 预算闸也因此第一步就跳闸,工具从没执行过。
    开关救不了:`stream_usage=False` 时上游照发、库照加。

    修法是在 chunk 转换的入口把累计值换成增量,再交给上游逻辑去加,加完正好等于最后一个
    chunk 的累计值。只在末尾报一次用量的规范通道不受影响(增量就等于它本身)。
    """

    # 学习注释：保存该模型实例上次看到的累计用量；这是内部状态，不属于API响应字段。
    _prev_usage: dict = PrivateAttr(default_factory=dict)

    # 学习注释：函数_convert_chunk_to_generation_chunk接收chunk、default_chunk_class、base_generation_info；把这一段可复用处理封装起来。
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        # 学习注释：仅在流式块是字典时取usage，防止对非字典调用get。
        usage = chunk.get("usage") if isinstance(chunk, dict) else None
        # 学习注释：只有usage为真/非空才进入这一分支。
        if usage:
            # 学习注释：self的_prev_usage属性（_prev_usage）；保存到prev。
            prev = self._prev_usage
            # 任何一项变小 = 上一条流已经结束,这是新一条流的第一个 chunk,基准清零。
            # 同一实例被复用时(结构化输出的链会多次 invoke)靠这条自愈。
            # 学习注释：若当前某项累计值比上次小，按现有适配规则推断进入新一轮计数。
            if any(isinstance(v, (int, float)) and v < prev.get(k, 0)
                   for k, v in usage.items() if not isinstance(v, dict)):
                # 学习注释：空字典；保存到prev。
                prev = {}
            # 学习注释：浅复制流式块，并用增量usage替换累计usage，避免库再次求和时重复计算。
            chunk = {**chunk, "usage": _diff_usage(usage, prev)}
            # 学习注释：usage；保存到self._prev_usage。
            self._prev_usage = usage
        # 学习注释：把修正后的块交给父类转换；其他消息内容仍沿用LangChain处理。
        return super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)


# 学习注释：根据配置构造OpenAI兼容聊天客户端；temperature影响采样但不保证输出完全固定。
def get_chat_model(streaming: bool = False, model: str | None = None,
                   temperature: float | None = None) -> ChatOpenAI:
    """直连聊天上游,说 OpenAI 协议。
    model 显式覆盖模型名(意图识别可配更大模型求准),None 时回落 settings.chat_model。
    flywheel:usage 统一收口——流式也回传 token(等效 stream_options.include_usage),
    否则 Langfuse 按意图统计只有非流式调用的账,主力 Agent 全漏。

    temperature 默认 0.3(答用户的话要有点人味)。**当评委用的链路要显式传 0**:
    同一份证据+同一份答案,0.3 下重放四次给出过 5/6、5/6、6/6、6/6 三种结果,
    量出来的分数里就混进了采样噪声——评估要的是可复现,不是多样性。"""
    # 学习注释：构造聊天客户端对象；真实网络请求发生在后续invoke/ainvoke。
    return _CumulativeUsageChatOpenAI(
        model=model or settings.chat_model,
        base_url=settings.chat_base_url,
        api_key=settings.chat_api_key,
        streaming=streaming,
        stream_usage=True,
        # 学习注释：调用方没指定时使用0.3；核对链路通常传0，但temperature=0仍不保证完全确定。
        temperature=0.3 if temperature is None else temperature,
        **_thinking_kwargs(),
    )


# 学习注释：只发送已配置的推理相关字段；服务是否支持这些字段由上游决定。
def _thinking_kwargs() -> dict:
    """把三项思考链配置拼成 ChatOpenAI 的参数。留空的不传,交给上游默认。

    thinking 和 reasoning_split 不是 OpenAI 协议里的标准字段,得走 extra_body;
    reasoning_effort 是标准字段,ChatOpenAI 直接认。上游不认的参数会被静默忽略
    (实测 MiniMax-M2.x 收到 thinking.type 不报错、也不生效),所以这里不按 provider 分叉。"""
    # 学习注释：空字典；保存到kwargs。
    kwargs: dict = {}
    # 学习注释：空字典；保存到extra。
    extra: dict = {}
    # 学习注释：只有settings.chat_thinking为真/非空才进入这一分支。
    if settings.chat_thinking:
        # 学习注释：包含type的字典；保存到extra['thinking']。
        extra["thinking"] = {"type": settings.chat_thinking}
    # 学习注释：只有settings.chat_reasoning_split为真/非空才进入这一分支。
    if settings.chat_reasoning_split:
        # 只认字面量 "true" 会把填 1 / yes / on 的人坑了:那几种写法本意是开,却会被转成
        # False 显式发出去,等于亲手关掉这项兜底。填 false / 0 才是真的要关。
        # 学习注释：将1/true/yes/on等配置统一解释为开启，再传给支持该字段的服务。
        extra["reasoning_split"] = settings.chat_reasoning_split.strip().lower() in _TRUTHY
    # 学习注释：只有extra为真/非空才进入这一分支。
    if extra:
        # 学习注释：extra；保存到kwargs['extra_body']。
        kwargs["extra_body"] = extra
    # 学习注释：只有settings.chat_reasoning_effort为真/非空才进入这一分支。
    if settings.chat_reasoning_effort:
        # 学习注释：settings的可选推理强度属性（chat_reasoning_effort）；保存到kwargs['reasoning_effort']。
        kwargs["reasoning_effort"] = settings.chat_reasoning_effort
    # 学习注释：返回kwargs，结束本次函数调用。
    return kwargs


# ---------- 结构化输出 ----------
# 只走 function calling 一条路,开流式,结果只认 tool_calls。三条都不是随手选的:
#
# 中转的 OpenAI 兼容层在**非流式**下会把 arguments 拼坏,前面粘一个空对象变成
# `{}{"faithful":...}`,MiniMax 与 Claude 全中(Claude 原生最擅长工具调用、它也坏,
# 说明是通道的转译,不是模型能力)。同一个中转、同一个模型换成流式,arguments 就是
# 干净的合法 JSON。所以这个坑靠开流式绕过。
#
# 机制也必须写死成 function_calling:langchain-openai 的 ChatOpenAI 默认是
# json_schema,那条路解析的是正文,而模型有时会在 tool_calls 旁边额外吐一段思考正文
# (实测同一道题、temperature=0,六次里三次带、三次不带),解析正文就会拿到自然语言、
# 返回 None。只认 tool_calls 则不受这个随机形态影响。
#
# 不留 json_mode 这条降级路。它听着像多一层保险,实际是个陷阱:json_mode 自己不带
# schema,字段定义只能塞进系统消息里靠模型自觉,本来就比 function calling 弱一截,
# 而 MiniMax 在 json_mode 下会把 JSON Schema 原样吐回来,解析必炸——2026-08-25 实测
# MiniMax-M3 走 json_mode 10 次全败,同一个模型同一个通道走 function calling
# 流式非流式各 12 次全过,DeepSeek-V4-Flash 同样两种流式各 12 次全过。降级到一条必炸的
# 路,不如当场失败:失败会抛到调用点,那里降级成安全默认值(检索用原问题、自评判证据不足),
# 系统照样答得出话,日志里还留得下真正的出错位置。
#
# 「失败」有两种形态,漏掉哪种都不行:抛异常(arguments 被拼坏),以及**返回 None 不抛**
# ——响应里没有 tool_calls 时 PydanticToolsParser(first_tool_only=True) 就是给 None。
# 只认异常的话,None 会一路传到调用点上炸(意图解析的 r.template、忠实度评审的 r.faithful),
# 报错位置还指不到真正的出处,所以下面把 None 就地转成异常。

# 结构化输出必须走非流式的模型。两家上游的毛病正好相反:中转的非流式 OpenAI 兼容层会把
# tool_calls 的 arguments 拼坏(所以 structured 默认开流式),而 MiniMax 反过来——流式下
# 压根不吐 tool_calls,非流式才给(实测 7 个 chunk 全无 tool_calls,同一请求非流式正常)。
_NO_STREAM_TOOLCALL_FAMILIES: tuple[str, ...] = ("minimax",)


# 学习注释：根据已知模型家族选择非流式工具调用兼容路径。
def needs_non_streaming_tools(model: str | None = None) -> bool:
    # 学习注释：把model or settings.chat_model or ''转换为统一大小写；保存到name。
    name = (model or settings.chat_model or "").lower()
    # 学习注释：模型名含已列出的家族名时走非流式工具调用兼容路径；不是对所有模型的通用保证。
    return any(f in name for f in _NO_STREAM_TOOLCALL_FAMILIES)


# 学习注释：要求模型通过function calling返回指定数据结构；异常或无tool_calls时有限重试。
def structured(schema, *, model: str | None = None, streaming: bool = True,
               temperature: float | None = None):
    """要结构化结果就用这个,别直接 with_structured_output——它默认走 json_schema,
    那条路解析正文,会被模型多吐的思考正文带崩。

    默认开流式:中转的非流式 OpenAI 兼容层会把 tool_calls 的 arguments 拼坏,流式不会
    (见上面那段注释里的实测)。

    只有 function calling 一条通路。偶发失败原样重试一次,再不成就把异常抛给调用点——
    调用点各自降级成安全默认值,别在这里降级到一条更差的通路上。

    temperature 透传给底层模型:当评委用的结构化链路传 0(见 get_chat_model 的说明)。
    """
    # 学习注释：从langchain_core.runnables引入RunnableLambda，用于组合可同步或异步调用的处理步骤。
    from langchain_core.runnables import RunnableLambda

    # 学习注释：把model or settings.chat_model or ''转换为统一大小写；保存到name。
    name = (model or settings.chat_model or "").lower()
    # 学习注释：依据当前兼容规则选择调用方式，避免已知服务在流式下遗漏tool_calls。
    if needs_non_streaming_tools(model):
        # 学习注释：False（不成立）；保存到streaming。
        streaming = False        # 这类模型流式不吐 tool_calls,见 _NO_STREAM_TOOLCALL_FAMILIES
    # 学习注释：get_chat_model的执行结果；保存到m。
    m = get_chat_model(streaming=streaming, model=model, temperature=temperature)
    # 学习注释：要求结果按schema返回，并从function_calling的tool_calls解析，不解析自由正文。
    fc = m.with_structured_output(schema, method="function_calling")

    # 学习注释：函数_log接收attempt、why；把这一段可复用处理封装起来。
    def _log(attempt: int, why: str) -> None:
        # 带上通道:同一个模型换个上游行为就不一样,只打模型名排障时定位不到
        logger.warning("%s 经 %s 的 function calling 第 %s 次没成(%s),%s",
                       name, settings.chat_base_url, attempt, why,
                       "原样重试一次" if attempt == 1 else "抛给调用点")

    # 学习注释：函数_no_tool_calls接收调用时的上下文；把这一段可复用处理封装起来。
    def _no_tool_calls() -> OutputParserException:
        # 学习注释：返回调用OutputParserException，处理把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
        return OutputParserException(
            f"{name} 经 {settings.chat_base_url} 连着两次都没返回 tool_calls")

    # 学习注释：函数_run接收x；把这一段可复用处理封装起来。
    def _run(x):
        # 学习注释：遍历2项元组：1、2，每轮把当前项交给attempt。
        for attempt in (1, 2):     # 偶发不返回 tool_calls,原样重试一次
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 学习注释：同步调用结构化模型链；可能产生网络请求，也可能抛解析异常。
                r = fc.invoke(x)
            except Exception as e:
                # 学习注释：调用_log，处理attempt。
                _log(attempt, type(e).__name__)
                # 学习注释：只有attempt等于2才进入这一分支。
                if attempt == 2:
                    # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
                    raise
                # 学习注释：跳过这一轮剩余步骤，继续处理下一项或下一次尝试。
                continue
            # 学习注释：只有实际得到解析对象才算成功；None也表示结构化输出失败。
            if r is not None:
                # 学习注释：返回r，结束本次函数调用。
                return r
            # 学习注释：调用_log，处理attempt。
            _log(attempt, "响应里没有 tool_calls")
        # 学习注释：以异常终止这一条路径：_no_tool_calls()；上层不能把它当作正常成功。
        raise _no_tool_calls()

    # 学习注释：异步函数_arun接收x；把这一段可复用处理封装起来。
    async def _arun(x):
        # 学习注释：遍历2项元组：1、2，每轮把当前项交给attempt。
        for attempt in (1, 2):
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 学习注释：异步调用同一结构化链；等待网络时让出执行机会。
                r = await fc.ainvoke(x)
            except Exception as e:
                # 学习注释：调用_log，处理attempt。
                _log(attempt, type(e).__name__)
                # 学习注释：只有attempt等于2才进入这一分支。
                if attempt == 2:
                    # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
                    raise
                # 学习注释：跳过这一轮剩余步骤，继续处理下一项或下一次尝试。
                continue
            # 学习注释：只有实际得到解析对象才算成功；None也表示结构化输出失败。
            if r is not None:
                # 学习注释：返回r，结束本次函数调用。
                return r
            # 学习注释：调用_log，处理attempt。
            _log(attempt, "响应里没有 tool_calls")
        # 学习注释：以异常终止这一条路径：_no_tool_calls()；上层不能把它当作正常成功。
        raise _no_tool_calls()

    # 学习注释：把同步_run与异步_arun包装成同一可组合对象，调用者可选invoke或ainvoke。
    return RunnableLambda(_run, afunc=_arun)
