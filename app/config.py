# 阅读导航：服务配置
# 本文件作用：Settings从进程环境变量和本地.env读取模型与Milvus配置，其他模块共享settings对象。
# 建议读法：API Key是访问服务的凭据；这里只解释字段，真实值不要写入源码或日志。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。
# 阅读校正：服务商思考参数是否生效取决于对应接口，历史注释里的耗时不是当前GridMate性能结果。

# 学习注释：从pydantic_settings引入BaseSettings, SettingsConfigDict，用于读取环境变量与.env配置。
from pydantic_settings import BaseSettings, SettingsConfigDict


# 学习注释：配置类：从环境变量和.env读取字段；没有默认值的字段为必填，缺少时启动报错。
class Settings(BaseSettings):
    # 学习注释：从当前工作目录的.env读取配置；extra=ignore允许.env包含本模型不使用的其他字段。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 聊天模型（意图识别 + Agent + 忠实度自检共用）
    # 必填,故意不给默认值。模型名是跟着账号走的,给默认值等于替使用者猜:猜错不会在
    # 启动时报错,而是在第一次调模型时抛一个看不懂的上游 401。没有默认值,缺了就在启动
    # 时报 chat_model Field required,直接指向 .env。
    # 学习注释：必填的聊天模型标识；名字必须是配置的服务商实际支持的名称。
    chat_model: str
    # 学习注释：必填的聊天API根地址；HTTP请求将发到这个服务。
    chat_base_url: str
    # 学习注释：必填的聊天服务凭据；源码只声明字段，真实值由本机环境提供。
    chat_api_key: str
    # 思考链控制。三项都留空就用上游默认,填了才传。
    # 关不关是上游的能力、不是参数写法:同一个 thinking.type,硅基流动的 DeepSeek 和
    # MiniMax-M3 上能关(实测 22.4s/318 token → 1.4s/16),MiniMax-M2.x 上会被静默忽略。
    chat_thinking: str = ""           # disabled 关 / adaptive 开
    # 学习注释：''；保存到可选推理强度（chat_reasoning_effort）。
    chat_reasoning_effort: str = ""   # 开着时的强度:low / high / max
    # 学习注释：''；保存到是否要求服务单独返回推理字段（chat_reasoning_split）。
    chat_reasoning_split: str = ""    # MiniMax 专属:true 让思考进 reasoning_content 独立字段

    # 向量化（相似事件检索 dense 召回）
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    # 学习注释：声明向量服务凭据（embed_api_key）字段，类型为str；值由构造对象时提供。
    embed_api_key: str
    # 学习注释：指定文本向量模型；查询时必须与建库使用的向量模型兼容。
    embed_model: str = "BAAI/bge-m3"          # 上游真实名

    # 重排（相似事件检索精排）
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    # 学习注释：声明重排服务凭据（rerank_api_key）字段，类型为str；值由构造对象时提供。
    rerank_api_key: str
    # 学习注释：'BAAI/bge-reranker-v2-m3'；保存到重排模型名称（rerank_model）。
    rerank_model: str = "BAAI/bge-reranker-v2-m3"   # 上游真实名

    # 向量库（相似事件检索的 Milvus 集合）
    # 学习注释：向量数据库连接地址；localhost表示当前机器，19530是此配置使用的端口。
    milvus_uri: str = "http://localhost:19530"


# 学习注释：导入本模块时实例化配置；必填配置缺失会在这里报错，不是实际调用模型。
settings = Settings()
