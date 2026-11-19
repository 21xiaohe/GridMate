# 阅读导航：候选重排服务
# 本文件作用：把问题与候选正文发送到/rerank，得到候选原索引和相关分，再按分数排序。
# 建议读法：重排只处理已召回候选；分数未经校准，不应直接当作正确概率。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：原有注释中的子句拆分/旧eval-rag案例是历史背景；当前本模块仅提供重排API和重试适配。

# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re

# 学习注释：导入httpx：发送HTTP请求。
import httpx

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings

# 学习注释：调用re.compile，处理'/v\\d+$'；保存到_VERSION_SEG。
_VERSION_SEG = re.compile(r"/v\d+$")


# 学习注释：规范化重排API地址，缺少版本路径时补/v1，再拼接/rerank。
def _rerank_url() -> str:
    """把配置里的上游地址拼成 /rerank 端点。每次现算,改配置不用重启。

    地址带不带版本段都收:硅基流动和 Jina 的 rerank 在 /v1 下,Cohere 在 /v2 下,而
    「上游地址」按字面理解很容易只填到域名。少了版本段会打成 https://host/rerank,
    上游回 404,而且只在查询时以 hybrid_rerank 失败的形式冒出来,排障要绕一圈。"""
    # 学习注释：清理settings.rerank_base_url首尾或指定一侧的字符；保存到base。
    base = settings.rerank_base_url.rstrip("/")
    # 学习注释：只有不满足（调用_VERSION_SEG.search，处理base）才进入这一分支。
    if not _VERSION_SEG.search(base):
        # 学习注释：用'/v1'更新base；下方+=等写法把计算结果存回原变量。
        base += "/v1"
    # 学习注释：返回计算 base + '/rerank'，结束本次函数调用。
    return base + "/rerank"


# 学习注释：5项集合：429、500、502、503、504；保存到_RETRY_STATUS。
_RETRY_STATUS = {429, 500, 502, 503, 504}
# 学习注释：3；保存到_RETRIES。
_RETRIES = 3          # 限流退避重试次数
# 学习注释：1.5；保存到_BACKOFF。
_BACKOFF = 1.5        # 秒,每次翻倍


# 学习注释：对网络错误和指定429/5xx响应做有上限的指数退避重试。
async def _post(url, json, headers, timeout):
    """带退避重试:子句拆分之后一个问题要打好几次 /rerank,上游按秒限流,
    突发流量会撞 429。这是瞬时限流不是坏请求,退一步再试就过去了;
    连试几次还不行才抛出去(评估脚本会把这一轮记成失败,不会静默算低分)。"""
    # 学习注释：None（尚无值）；保存到last。
    last: httpx.Response | None = None
    # 学习注释：None（尚无值）；保存到last_exc。
    last_exc: Exception | None = None
    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给i。
    for i in range(_RETRIES + 1):
        # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
        try:
            # 学习注释：创建HTTP客户端，用于发送请求并管理连接；with结束时调用退出逻辑，关闭资源或恢复上下文。
            async with httpx.AsyncClient() as c:
                # 学习注释：向指定地址发送HTTP POST请求，await等待结果后再继续；保存到HTTP/API响应（resp）。
                resp = await c.post(url, json=json, headers=headers, timeout=timeout)
        except httpx.TransportError as e:
            # 连接层的抖动(断连、读超时、连不上)跟 429/5xx 一样是瞬时故障,一样该退避重试。
            # 早先只判状态码,c.post 抛出来就直接冒到顶:上游回 503 会重试,上游把连接
            # 掐了反而一次都不试。eval-rag 跑到第 200 多题被断一次,十几分钟整轮白跑。
            last_exc = e
            # 学习注释：只有i小于_RETRIES才进入这一分支。
            if i < _RETRIES:
                # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
                await asyncio.sleep(_BACKOFF * (2 ** i))
                # 学习注释：跳过这一轮剩余步骤，继续处理下一项或下一次尝试。
                continue
            # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
            raise
        # 学习注释：只有resp.status_code不包含在_RETRY_STATUS才进入这一分支。
        if resp.status_code not in _RETRY_STATUS:
            # 学习注释：返回HTTP/API响应（resp），结束本次函数调用。
            return resp
        # 学习注释：HTTP/API响应（resp）；保存到last。
        last = resp
        # 学习注释：只有i小于_RETRIES才进入这一分支。
        if i < _RETRIES:
            # 学习注释：异步等待退避时间，让出执行机会而非阻塞线程，await等待结果后再继续。
            await asyncio.sleep(_BACKOFF * (2 ** i))
    # 学习注释：只有last尚未设置（None），并且last_exc已经有值（不是None）才进入这一分支。
    if last is None and last_exc is not None:
        # 学习注释：以异常终止这一条路径：last_exc；上层不能把它当作正常成功。
        raise last_exc
    # 学习注释：返回last，结束本次函数调用。
    return last


# 学习注释：请求重排模型，按相关分降序返回原候选索引；不会凭空生成新案例。
async def rerank(query: str, docs: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """直连上游 /rerank 调 bge-reranker-v2-m3。返回 [(原始索引, 相关分)] 按分降序,截断 top_n。

    rerank 不是 OpenAI 协议里的东西,这个接口走的是 Jina / Cohere 那套形状
    (query + documents,回 results 里的 index 与 relevance_score),所以这里手写请求、
    不用 openai 客户端。"""
    # 学习注释：只有候选正文列表（docs）为空或为False才进入这一分支。
    if not docs:
        # 学习注释：返回空列表，结束本次函数调用。
        return []
    # 学习注释：包含model、query、documents、top_n的字典；保存到发送给服务的请求体（payload）。
    payload = {"model": settings.rerank_model, "query": query, "documents": docs,
               "top_n": top_n or len(docs)}
    # 学习注释：_post的执行结果，await等待结果后再继续；保存到HTTP/API响应（resp）。
    resp = await _post(_rerank_url(), payload,
                       {"Authorization": f"Bearer {settings.rerank_api_key}"}, 60)
    # 学习注释：遇到非成功HTTP状态时抛异常，不把错误响应当正常数据。
    resp.raise_for_status()
    # 学习注释：resp.json()中键/位置为'results'的值；保存到结果列表（results）。
    results = resp.json()["results"]
    # 学习注释：按指定键排序后得到新列表；保存到重排后的索引和分数（ranked）。
    ranked = sorted(((r["index"], float(r["relevance_score"])) for r in results),
                    key=lambda x: x[1], reverse=True)
    # 学习注释：返回重排保留数量（top_n）为真/非空时取ranked中键/位置为:top_n的值，否则取重排后的索引和分数（ranked），结束本次函数调用。
    return ranked[:top_n] if top_n else ranked
