# -*- coding: utf-8 -*-
# 阅读导航：进程内会话历史
# 本文件作用：按session_id保存最近消息，并限制每会话长度和会话总量；不写磁盘，重启会丢失。
# 建议读法：get把访问的会话移到末尾，append从最久未访问的会话开始淘汰，这是LRU策略。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：会话内存存储（多轮对话历史）。

只保留台账问数用到的会话存储。原客服系统的「滑窗 + 摘要注入」上下文管理层
（trim_history / build_window / summary_line / summary_system）已随客服代码移除——
台账多轮靠 coref.resolve_outage 做指代消解补全，不需要那套摘要压缩。
"""
# 学习注释：从langchain_core.messages引入BaseMessage，用于区分用户、助手和工具消息。
from langchain_core.messages import BaseMessage


# 学习注释：定义SessionStore对象，集中保存下方属性和方法。
class SessionStore:
    """内存会话存储，session_id -> 消息列表。不做持久化（本地验证够用）。

    加了两个上限，防止长会话把内存无限撑大（本地演示几十轮没事，但这是工程隐患）：
      - max_history：单 session 最多保留的最近消息条数，超了就裁掉最老的；
      - max_sessions：最多保留的 session 数，超了淘汰最久未访问的（LRU）。
    """

    # 学习注释：创建会话容器；默认最多1000个会话，每个会话保留最近12条消息，通常约6轮问答。
    def __init__(self, max_history: int = 12, max_sessions: int = 1000) -> None:
        # 学习注释：空字典；保存到self._sessions。
        self._sessions: dict[str, list[BaseMessage]] = {}
        # 学习注释：单会话保留的消息数上限（max_history）；保存到self.max_history。
        self.max_history = max_history
        # 学习注释：内存中保留的会话数上限（max_sessions）；保存到self.max_sessions。
        self.max_sessions = max_sessions

    # 学习注释：读取会话历史，并把该会话标记为最近访问；不存在时返回空列表。
    def get(self, session_id: str) -> list[BaseMessage]:
        # 学习注释：只有会话标识（session_id）不包含在self的_sessions属性（_sessions）才进入这一分支。
        if session_id not in self._sessions:
            # 学习注释：返回空列表，结束本次函数调用。
            return []
        # 读取视作「访问」，挪到队尾，让淘汰时先踢最久未用的（LRU）
        self._sessions[session_id] = self._sessions.pop(session_id)
        # 学习注释：返回self._sessions中键/位置为session_id的值，结束本次函数调用。
        return self._sessions[session_id]

    # 学习注释：把传入的一条或多条消息追加到会话，然后裁剪过长历史并淘汰过多会话。
    def append(self, session_id: str, *messages: BaseMessage) -> None:
        # 学习注释：读取self._sessions的键会话标识（session_id），不存在时写入默认值再返回；保存到bucket。
        bucket = self._sessions.setdefault(session_id, [])
        # 学习注释：把模型与工具的消息序列（messages）的各元素追加到bucket。
        bucket.extend(messages)
        # 单 session 裁剪：只留最近 max_history 条
        if len(bucket) > self.max_history:
            # 学习注释：删除bucket[:len(bucket) - self.max_history]，清除不再需要的状态。
            del bucket[: len(bucket) - self.max_history]
        # 挪到队尾（本次写入即最近访问）
        self._sessions[session_id] = self._sessions.pop(session_id)
        # session 总数上限：超了淘汰最久未访问的（队首）
        while len(self._sessions) > self.max_sessions:
            # 学习注释：字典保持插入顺序，iter取最前面的会话键；移除它就是淘汰最久未访问者。
            self._sessions.pop(next(iter(self._sessions)))
