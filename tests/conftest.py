# 阅读导航：测试公共环境
# 本文件作用：pytest自动发现这里的fixture：生成合成数据库、禁用外部网络、替换模型边界。
# 建议读法：fixture是测试准备函数；monkeypatch临时替换对象，测试结束后自动恢复。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""合成台账与禁止外部网络的回归环境；不读取真实台账或调用付费服务。"""
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入os：环境变量与系统文件操作。
import os
# 学习注释：导入socket：网络连接；测试中用于拦截外部调用。
import socket
# 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
import sqlite3
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从types引入SimpleNamespace，用于临时封装带命名属性的对象。
from types import SimpleNamespace
# 学习注释：从uuid引入uuid4，用于生成请求、会话或版本的随机标识。
from uuid import uuid4

# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest

# 在配置模块导入之前固定虚拟凭据。测试进程不会使用本机 .env 的服务配置。
for key, value in {
    "CHAT_MODEL": "test-chat", "CHAT_BASE_URL": "http://invalid.test/v1",
    "CHAT_API_KEY": "test-only", "EMBED_MODEL": "test-embed",
    "EMBED_BASE_URL": "http://invalid.test/v1", "EMBED_API_KEY": "test-only",
    "RERANK_MODEL": "test-reranker", "RERANK_BASE_URL": "http://invalid.test/v1",
    "RERANK_API_KEY": "test-only", "MILVUS_URI": "http://invalid.test:19530",
}.items():
    # 学习注释：在导入配置前设虚拟凭据与无效地址，避免测试误用本机真实模型账号。
    os.environ[key] = value

# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning
# 学习注释：从scripts.query.build_db导入create_schema，复用对应模块的实现。
from scripts.query.build_db import create_schema
# 学习注释：从scripts.query.build_event_corpus导入write_corpus，复用对应模块的实现。
from scripts.query.build_event_corpus import write_corpus


# 学习注释：函数pytest_configure接收config；把这一段可复用处理封装起来。
def pytest_configure(config):
    # 本机系统 pytest 临时目录存在权限问题；每次使用全新的仓库内忽略目录。
    if config.option.basetemp is None:
        # 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parents[1] / 'data/evaluations'；保存到parent。
        parent = Path(__file__).resolve().parents[1] / "data/evaluations"
        # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
        parent.mkdir(parents=True, exist_ok=True)
        # 学习注释：把按目录层级拼接文件路径 parent / ('pytest_' + uuid4().hex)转换为字符串；保存到config.option.basetemp。
        config.option.basetemp = str(parent / ("pytest_" + uuid4().hex))


# 学习注释：每个测试自动应用网络拦截，不需要测试函数主动声明使用。
@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    # 学习注释：socket.socket的connect属性（connect）；保存到original。
    original = socket.socket.connect
    # 学习注释：函数blocked接收调用时的上下文；把这一段可复用处理封装起来。
    def blocked(*args, **kwargs):
        # Windows asyncio 创建本地唤醒 socketpair 需要 loopback 连接。
        address = args[1] if len(args) > 1 else None
        # 学习注释：只有检查address是否属于tuple类型，并且address[0]包含在2项元组：'127.0.0.1'、'::1'才进入这一分支。
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            # 学习注释：返回调用original，处理*args，结束本次函数调用。
            return original(*args, **kwargs)
        # 学习注释：以异常终止这一条路径：'回归测试禁止网络调用'；上层不能把它当作正常成功。
        raise AssertionError("回归测试禁止网络调用")
    # 学习注释：临时替换socket连接方法，阻止外部网络；测试结束自动恢复。
    monkeypatch.setattr(socket.socket, "connect", blocked)


# 学习注释：把下面函数注册为测试准备对象；同名参数会让pytest自动注入其返回值。
@pytest.fixture
def dataset(tmp_path, monkeypatch):
    # 学习注释：按目录层级拼接文件路径 tmp_path / 'gridmate.db'；保存到db。
    db = tmp_path / "gridmate.db"
    # 学习注释：打开SQLite连接；保存到数据库连接（conn）。
    conn = sqlite3.connect(db)
    # 学习注释：create_schema的执行结果。
    create_schema(conn)
    # E3 的线路1记录在五月，线路2记录在六月；专门检查行关联不能丢失。
    rows = [
        ("E1", "线路1", "中心1", "2025-05-31 23:00:00", "2025-06-01 01:00:00", "检修", "合成跨月案例", 1),
        ("E2", "线路1", "中心1", "2025-06-01 10:00:00", "2025-06-01 11:00:00", "故障", "合成故障案例", 2),
        ("E3", "线路1", "中心1", "2025-05-30 10:00:00", "2025-05-30 11:00:00", "检修", "合成记录甲", 3),
        ("E3", "线路2", "中心2", "2025-06-02 10:00:00", "2025-06-02 11:00:00", "故障", "合成记录乙", 4),
        ("E4", "线路1", "中心1", "2025-12-31 10:00:00", "2025-12-31 11:00:00", "故障", "合成年末案例", 5),
    ]
    # 学习注释：对多行数据批量执行同一条带占位符的SQL。
    conn.executemany(
        "INSERT INTO users(事件序号,线路名称,供服中心,停电起始时间,停电终止时间,"
        "停电性质,备注说明,缺供电量) VALUES(?,?,?,?,?,?,?,?)", rows)
    # 学习注释：提交数据库事务，使本次写入生效。
    conn.commit()
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 学习注释：按目录层级拼接文件路径 tmp_path / 'event_corpus.jsonl'；保存到事件语料文件路径（corpus_path）。
    corpus_path = tmp_path / "event_corpus.jsonl"
    # 学习注释：write_corpus的执行结果；保存到事件级语料（corpus）。
    corpus = write_corpus(db, corpus_path)
    # 学习注释：调用versioning.DatasetSnapshot，处理'test_v1'；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.DatasetSnapshot(
        "test_v1", db, corpus_path, "outage_events_test_v1",
        "2025-01-01", "2026-01-01", "合成抽样范围", "verified_bundle", "test-embed")
    # 学习注释：让测试只读取自己创建的临时快照，隔离真实业务数据库。
    monkeypatch.setattr(versioning, "load_snapshot", lambda: snapshot)
    # 学习注释：返回调用SimpleNamespace，结束本次函数调用。
    return SimpleNamespace(snapshot=snapshot, db=db, corpus=corpus, corpus_path=corpus_path)


# 学习注释：把下面函数注册为测试准备对象；同名参数会让pytest自动注入其返回值。
@pytest.fixture
def fake_search(dataset, monkeypatch):
    """保留真实范围逻辑、trace 和信号，仅替换 Embedding/Milvus/Reranker 边界。"""
    # 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
    from unittest.mock import AsyncMock
    # 学习注释：从app.core导入similar，复用对应模块的实现。
    from app.core import similar
    # 学习注释：从scripts.query.vectorize_events导入FIELD_MAP，复用对应模块的实现。
    from scripts.query.vectorize_events import FIELD_MAP
    # 学习注释：空列表；保存到calls。
    calls = []

    # 学习注释：函数recall接收外部服务客户端（client）、vec、文本（text）、期望最多返回的案例数（top_k）、单路召回上限（recall）、expr、Milvus集合名（collection）；把这一段可复用处理封装起来。
    def recall(client, vec, text, top_k, recall=50, expr=None, collection=None):
        # 学习注释：expr尚未设置（None）时取None（尚无值），否则取转为不重复元素集合：把JSON文本解析为Python对象；保存到ids。
        ids = None if expr is None else set(json.loads(expr.removeprefix("event_id in ")))
        # 学习注释：空列表；保存到result。
        result = []
        # 学习注释：遍历dataset的事件级语料属性（corpus），每轮把当前项交给当前事件（event）。
        for event in dataset.corpus:
            # 学习注释：只有ids已经有值（不是None），并且event['事件序号']不包含在ids才进入这一分支。
            if ids is not None and event["事件序号"] not in ids:
                # 学习注释：跳过这一轮剩余步骤，继续处理下一项或下一次尝试。
                continue
            # 学习注释：遍历FIELD_MAP.items()，将en→event.get(cn)收集为字典；保存到entity。
            entity = {en: event.get(cn) for en, cn in FIELD_MAP.items()}
            # 学习注释：event中键/位置为'事件文本'的值；保存到entity['text']。
            entity["text"] = event["事件文本"]
            # 学习注释：向result末尾添加包含entity、distance的字典。
            result.append({"entity": entity, "distance": 0.5})
        # 学习注释：向calls末尾添加包含expr、collection、query的字典。
        calls.append({"expr": expr, "collection": collection, "query": text})
        # 学习注释：返回result中键/位置为:top_k的值，结束本次函数调用。
        return result[:top_k]

    # 学习注释：异步函数acall接收fn；把这一段可复用处理封装起来。
    async def acall(fn, *args, **kwargs):
        # 学习注释：返回调用fn，处理*args，结束本次函数调用。
        return fn(*args, **kwargs)

    # 学习注释：异步函数ranked接收用户查询文本（query）、候选正文列表（docs）、重排保留数量（top_n）；把这一段可复用处理封装起来。
    async def ranked(query, docs, top_n=None):
        # 学习注释：返回遍历range(min(len(docs), top_n or len(docs)))，将(idx, 0.8)收集为列表，结束本次函数调用。
        return [(idx, 0.8) for idx in range(min(len(docs), top_n or len(docs)))]

    # 学习注释：用固定小向量替代真实Embedding请求；这个测试不评估向量质量。
    embed = AsyncMock(return_value=[0.1, 0.2])
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.embeddings, "embed_query", embed)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar, "_get_client", lambda: object())
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar, "_acall", acall)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar, "_hybrid_search_sync", recall)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(similar.rerank, "rerank", ranked)
    # 学习注释：异步函数reviewed接收用户查询文本（query）、事件列表（events）；把这一段可复用处理封装起来。
    async def reviewed(query, events):
        # 学习注释：返回遍历事件列表（events），将{'event_id': str(e['事件序号']), 'decision': 'supported', 'quotes': [e['事…收集为列表，结束本次函数调用。
        return [{"event_id": str(e["事件序号"]), "decision": "supported",
                 "quotes": [e["事件文本"]], "reason": "合成边界替身",
                 "human_validated": False, "annotator_type": "stub"} for e in events]
    # 学习注释：把依据核对换成固定返回的替身；需要测试拒绝/异常时再在单个测试中覆盖它。
    monkeypatch.setattr(similar.relevance, "review_candidates", reviewed)
    # 学习注释：返回调用SimpleNamespace，结束本次函数调用。
    return SimpleNamespace(calls=calls, embed=embed)
