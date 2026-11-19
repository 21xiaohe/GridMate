# 阅读导航：文本向量服务
# 本文件作用：把文本发给Embedding模型，获得浮点数列表；向量用于语义召回，不是聊天回答。
# 建议读法：embed_texts处理一批文本，embed_query处理单个问题；复用客户端可以复用网络连接。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：从openai引入AsyncOpenAI，用于调用兼容OpenAI协议的服务，本项目用其异步Embedding客户端。
from openai import AsyncOpenAI

# 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
from app.config import settings

# 学习注释：None（尚无值）；保存到_CLIENT。
_CLIENT: AsyncOpenAI | None = None


# 学习注释：直连嵌入上游(硅基流动的 bge-m3,OpenAI 兼容)。
def _client() -> AsyncOpenAI:
    """直连嵌入上游(硅基流动的 bge-m3,OpenAI 兼容)。
    进程内单例:复用同一 httpx 连接池,避免每次 embed 新建客户端累积连接/fd。"""
    # 学习注释：声明下方修改的是模块级共享变量_CLIENT，而非函数局部同名变量。
    global _CLIENT
    # 学习注释：只有_CLIENT尚未设置（None）才进入这一分支。
    if _CLIENT is None:
        # 学习注释：创建兼容OpenAI协议的异步API客户端；保存到_CLIENT。
        _CLIENT = AsyncOpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key)
    # 学习注释：返回_CLIENT，结束本次函数调用。
    return _CLIENT


# 学习注释：把多段文本发送给Embedding服务，返回每段文本对应的数字向量。
async def embed_texts(texts: list[str]) -> list[list[float]]:
    # 学习注释：向Embedding API发送一批texts；await等待网络响应，返回的是向量而非自然语言回答。
    resp = await _client().embeddings.create(model=settings.embed_model, input=texts)
    # 学习注释：从每条响应数据提取浮点数向量；例如一条文本对应1024个数字。
    return [d.embedding for d in resp.data]


# 学习注释：复用批量Embedding接口处理一个查询，再取出唯一的向量。
async def embed_query(text: str) -> list[float]:
    # 学习注释：先用[text]把单个问题包装成一项列表，调用批量接口后取第0个向量。
    return (await embed_texts([text]))[0]
