"""classifier 权威归并术语表:全系统唯一一份,17 类主题类目。
数据处理、预标、训练、推理、评测、前端全部 import 这里,不许各自抄一份。
类目名与边界说明采用归并术语表原文;元组顺序即 label id,训练/推理共用。
severity 是容错档位:归错会带偏补知识优先级的类目从严。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicClass:
    name: str
    boundary: str                 # 一句边界说明:什么算这一类,近邻类目靠它划开
    examples: tuple[str, ...]     # 用户的说法示例
    severity: str                 # 容错档位:严 / 中 / 宽


TOPIC_CLASSES: tuple[TopicClass, ...] = (
    TopicClass("退费退补", "退费、退补、预收退还怎么办;修归表计维修,退归这里",
               ("退费", "多收电费", "退钱", "想退了", "预存的电费能退吗"), "严"),
    TopicClass("停电报修", "停电了、什么时候来电、抢修到哪了;欠费停电归违约金,故障停电归这里",
               ("停电了", "还没来电", "抢修到哪了", "怎么还不动"), "严"),
    TopicClass("计量电表", "电表走字、准不准、读数怎么看",
               ("电表走得快", "读数怎么看", "电表准不准", "底度对不上"), "严"),
    TopicClass("电费发票", "开票、抬头、报销凭证",
               ("开发票", "发票抬头开错了", "能开增值税发票吗", "合并开票"), "严"),
    TopicClass("用电故障", "户内线路、开关、电压的毛病;片区停电归停电报修",
               ("家里跳闸", "插座没电", "电压不稳", "总闸合不上"), "严"),
    TopicClass("违约金", "欠费违约金谁承担、能不能减免;管的是钱,停电来了没归停电报修",
               ("违约金怎么算", "能减免吗", "滞纳金"), "中"),
    TopicClass("电价政策", "阶梯、峰谷、电价类别的规则;执行得对不对归电价执行",
               ("阶梯电价", "峰谷电价怎么算", "居民电价多少"), "中"),
    TopicClass("电价执行", "自己被执行的电价类别对不对、怎么改",
               ("被执行商业电价了", "电价类别改一下", "执行错电价"), "中"),
    TopicClass("缴费支付", "缴费环节出的问题",
               ("付不了款", "扣了两次钱", "缴费没到账", "代扣失败"), "中"),
    TopicClass("业务变更", "过户、更名、改类、销户这类户档案变更",
               ("过户怎么办理", "改户名", "销户", "改类"), "中"),
    TopicClass("业扩新装", "新装、增容、装表接电",
               ("要装新电表", "增容", "新装申请", "开店要用电"), "中"),
    TopicClass("用电咨询", "业务材料、流程、时限的通用咨询",
               ("要什么材料", "流程怎么走", "办理要多久"), "中"),
    TopicClass("表计维修", "换表、表箱、铅封、采集终端;修归这里,退归退费退补",
               ("电表坏了", "换表", "铅封掉了", "表箱破损"), "中"),
    TopicClass("用电账户", "户号绑定、服务密码、账户安全",
               ("忘了密码", "户号怎么查", "换绑手机号", "注销账户"), "宽"),
    TopicClass("线上渠道", "App、公众号、代扣签约解约、电子账单",
               ("App打不开", "代扣怎么解约", "电子账单订阅"), "宽"),
    TopicClass("服务评价", "评价、投诉反馈的规则",
               ("评价怎么改", "投诉处理得怎么样", "回访能拒绝吗"), "宽"),
    TopicClass("其他", "上面都对不上的,先兜底",
               ("闲聊", "转人工", "客服几点上班"), "宽"),
)

TOPIC_NAMES: tuple[str, ...] = tuple(c.name for c in TOPIC_CLASSES)
LABEL2ID: dict[str, int] = {name: i for i, name in enumerate(TOPIC_NAMES)}
ID2LABEL: dict[int, str] = {i: name for i, name in enumerate(TOPIC_NAMES)}
NUM_CLASSES = len(TOPIC_CLASSES)
SEVERITY: dict[str, str] = {c.name: c.severity for c in TOPIC_CLASSES}


def terminology_table() -> str:
    """预标/造数 prompt 用的术语表文本:类目:边界说明(示例)。"""
    return "\n".join(
        f"- {c.name}:{c.boundary}(示例:{'、'.join(c.examples)})" for c in TOPIC_CLASSES
    )
