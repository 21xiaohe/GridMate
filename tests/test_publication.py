# 阅读导航：版本发布回归
# 本文件作用：检查数据产物校验、发布失败、锁、请求快照和回滚相关规则。
# 建议读法：测试使用临时数据与替身，真实服务演练由scripts/accept_publication.py承担。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入shutil：文件复制等操作。
import shutil
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从unittest.mock引入AsyncMock，用于用确定性替身模拟外部服务和故障。
from unittest.mock import AsyncMock

# 学习注释：导入pytest：测试收集、fixture、参数化与异常断言。
import pytest
# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning
# 学习注释：从scripts.query导入publish, vectorize_events并简称vector，复用对应模块的实现。
from scripts.query import publish, vectorize_events as vector


# 学习注释：函数publisher接收pytest提供的独立临时目录（tmp_path）、dataset、pytest临时对象替换工具（monkeypatch）；把这一段可复用处理封装起来。
@pytest.fixture
def publisher(tmp_path, dataset, monkeypatch):
    # 学习注释：按目录层级拼接文件路径 tmp_path / 'published'；保存到root。
    root = tmp_path / "published"
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    root.mkdir()
    # 学习注释：按目录层级拼接文件路径 root / 'version.json'；保存到current。
    current = root / "version.json"
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    current.write_text('{"version":"old"}', encoding="utf-8")
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (root / "gridmate.db").write_bytes(b"OLD_DB")
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (root / "event_corpus.jsonl").write_bytes(b"OLD_CORPUS")
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(versioning, "DATA_DIR", root)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(versioning, "VERSION_PATH", current)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(publish, "_run_db", lambda path, source: shutil.copyfile(dataset.db, path))

    # 学习注释：调用Embedding构造新版本向量集合并读回检查；本函数不切换业务版本。
    async def vectorize(path, collection):
        # 学习注释：返回包含collection、rows的字典，结束本次函数调用。
        return {"collection": collection, "rows": len(dataset.corpus)}

    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(publish, "_run_vectorize", vectorize)
    # 学习注释：返回root，结束本次函数调用。
    return root


# 学习注释：本测试验证：新版本构建失败时，旧业务清单和产物保持可用。
@pytest.mark.parametrize("failure", ["db", "corpus", "vector", "commit"])
async def test_failed_publication_preserves_old_bundle(publisher, monkeypatch, failure):
    # 学习注释：函数fail接收调用时的上下文；把这一段可复用处理封装起来。
    def fail(*args, **kwargs):
        # 学习注释：以异常终止这一条路径：'injected failure'；上层不能把它当作正常成功。
        raise RuntimeError("injected failure")
    # 学习注释：只有failure等于'vector'才进入这一分支。
    if failure == "vector":
        # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
        monkeypatch.setattr(publish, "_run_vectorize", AsyncMock(side_effect=RuntimeError("injected")))
    # 学习注释：只有failure等于'commit'才进入这一分支。
    elif failure == "commit":
        # 学习注释：versioning的atomic_json属性（atomic_json）；保存到original。
        original = versioning.atomic_json
        # 学习注释：函数reject_pointer接收path、value；把这一段可复用处理封装起来。
        def reject_pointer(path, value):
            # 学习注释：只有path等于versioning的VERSION_PATH属性（VERSION_PATH）才进入这一分支。
            if path == versioning.VERSION_PATH:
                # 学习注释：调用fail。
                fail()
            # 学习注释：调用original，处理path。
            original(path, value)
        # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
        monkeypatch.setattr(versioning, "atomic_json", reject_pointer)
    else:
        # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
        monkeypatch.setattr(publish, "_run_" + failure, fail)
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(RuntimeError):
        # 学习注释：publish.publish的执行结果，await等待结果后再继续。
        await publish.publish()
    # 学习注释：测试要求：json.loads(versioning.VERSION_PATH.read_text())等于包含version的字典；不满足就失败。
    assert json.loads(versioning.VERSION_PATH.read_text()) == {"version": "old"}
    # 学习注释：测试要求：(publisher / 'gridmate.db').read_bytes()等于b'OLD_DB'；不满足就失败。
    assert (publisher / "gridmate.db").read_bytes() == b"OLD_DB"
    # 学习注释：测试要求：(publisher / 'event_corpus.jsonl').read_bytes()等于b'OLD_CORPUS'；不满足就失败。
    assert (publisher / "event_corpus.jsonl").read_bytes() == b"OLD_CORPUS"
    # 学习注释：测试要求：不满足（检查路径是否存在）；不满足就失败。
    assert not (publisher / ".publish.lock").exists()


# 学习注释：本测试验证：发布切换发生在请求中途时，该请求仍使用开始时的快照。
async def test_published_snapshot_is_pinned_across_switch(publisher, monkeypatch):
    # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到first。
    first = await publish.publish()
    # 学习注释：versioning.validate_manifest的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.validate_manifest(first)
    # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
    with versioning.dataset_scope(snapshot):
        # 学习注释：调用versioning.current_request_id；保存到本轮请求标识（request_id）。
        request_id = versioning.current_request_id()
        # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到second。
        second = await publish.publish()
        # 学习注释：测试要求：first['version']不等于second中键/位置为'version'的值；不满足就失败。
        assert first["version"] != second["version"]
        # 学习注释：测试要求：versioning.current_snapshot().version等于first中键/位置为'version'的值；不满足就失败。
        assert versioning.current_snapshot().version == first["version"]
        # 学习注释：测试要求：versioning.current_request_id()等于本轮请求标识（request_id）；不满足就失败。
        assert versioning.current_request_id() == request_id
        # 学习注释：测试要求：versioning.current_snapshot().db_path等于snapshot的SQLite文件路径属性（db_path）；不满足就失败。
        assert versioning.current_snapshot().db_path == snapshot.db_path
    # 学习注释：测试要求：versioning.current_request_id()尚未设置（None）；不满足就失败。
    assert versioning.current_request_id() is None
    # 学习注释：测试要求：json.loads(versioning.VERSION_PATH.read_text())['version']等于second中键/位置为'version'的值；不满足就失败。
    assert json.loads(versioning.VERSION_PATH.read_text())["version"] == second["version"]
    # 学习注释：测试要求：检查路径是否存在；不满足就失败。
    assert snapshot.db_path.exists()
    # 学习注释：测试要求：检查路径是否存在；不满足就失败。
    assert snapshot.corpus_path.exists()


# 学习注释：本测试验证：数据库或语料内容被改动后，文件哈希检查必须发现。
async def test_snapshot_hash_rejects_tampered_artifact(publisher):
    # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到数据版本清单（manifest）。
    manifest = await publish.publish()
    # 学习注释：versioning.validate_manifest的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = versioning.validate_manifest(manifest)
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    snapshot.corpus_path.write_text("changed", encoding="utf-8")
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError, match="校验失败"):
        # 学习注释：versioning.validate_manifest的执行结果。
        versioning.validate_manifest(manifest)


# 学习注释：本测试验证：向量条数与语料不一致时不能激活新版本。
async def test_publish_count_mismatch_does_not_switch(publisher, monkeypatch):
    # 学习注释：异步函数bad_count接收path、Milvus集合名（collection）；把这一段可复用处理封装起来。
    async def bad_count(path, collection):
        # 学习注释：返回包含collection、rows的字典，结束本次函数调用。
        return {"collection": collection, "rows": 999}
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(publish, "_run_vectorize", bad_count)
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(RuntimeError):
        # 学习注释：publish.publish的执行结果，await等待结果后再继续。
        await publish.publish()
    # 学习注释：测试要求：json.loads(versioning.VERSION_PATH.read_text())等于包含version的字典；不满足就失败。
    assert json.loads(versioning.VERSION_PATH.read_text()) == {"version": "old"}


# 学习注释：本测试验证：回滚先核对旧版完整产物，通过后才切换当前版本。
async def test_rollback_validates_then_switches(publisher, monkeypatch):
    # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到first。
    first = await publish.publish()
    # 学习注释：publish.publish的执行结果，await等待结果后再继续；保存到second。
    second = await publish.publish()
    # 学习注释：定义Client对象，集中保存下方属性和方法。
    class Client:
        # 学习注释：函数close接收调用时的上下文；把这一段可复用处理封装起来。
        def close(self): pass
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "_client", Client)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "verify_collection", lambda *args: 4)
    # 学习注释：publish.rollback的执行结果。
    publish.rollback(first["version"])
    # 学习注释：测试要求：versioning.read_version()['version']等于first中键/位置为'version'的值；不满足就失败。
    assert versioning.read_version()["version"] == first["version"]
    # 学习注释：测试要求：检查路径是否存在；不满足就失败。
    assert versioning.validate_manifest(second).db_path.exists()
    # 学习注释：函数bad接收调用时的上下文；把这一段可复用处理封装起来。
    def bad(*args):
        # 学习注释：以异常终止这一条路径：'collection unavailable'；上层不能把它当作正常成功。
        raise RuntimeError("collection unavailable")
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "verify_collection", bad)
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(RuntimeError):
        # 学习注释：publish.rollback的执行结果。
        publish.rollback(second["version"])
    # 学习注释：测试要求：versioning.read_version()['version']等于first中键/位置为'version'的值；不满足就失败。
    assert versioning.read_version()["version"] == first["version"]


# 学习注释：本测试验证：发布锁拒绝同时进入，并在正常退出后释放。
def test_publish_lock_serializes_and_releases(publisher):
    # 学习注释：versioning.publication_lock的执行结果；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with versioning.publication_lock():
        # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
        with pytest.raises(RuntimeError, match="发布/回滚锁"):
            # 学习注释：versioning.publication_lock的执行结果；with结束时调用退出逻辑，关闭资源或恢复上下文。
            with versioning.publication_lock():
                # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
                pass
    # 学习注释：测试要求：不满足（检查路径是否存在）；不满足就失败。
    assert not (publisher / ".publish.lock").exists()


# 学习注释：本测试验证：不允许跳过数据库、语料或向量中的任一部分来发布。
@pytest.mark.parametrize("flag", ["--skip-db", "--skip-corpus", "--skip-vector"])
async def test_partial_publish_flags_are_rejected(flag):
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(SystemExit) as exc:
        # 学习注释：调用publish.main，处理1项列表：flag，await等待结果后再继续。
        await publish.main([flag])
    # 学习注释：测试要求：exc.value.code等于2；不满足就失败。
    assert exc.value.code == 2


# 学习注释：定义FakeIterator对象，集中保存下方属性和方法。
class FakeIterator:
    # 学习注释：函数__init__接收查询或表格行（rows）；把这一段可复用处理封装起来。
    def __init__(self, rows):
        # 学习注释：查询或表格行（rows）；保存到self.rows。
        self.rows = rows
    # 学习注释：函数next接收调用时的上下文；把这一段可复用处理封装起来。
    def next(self):
        # 学习注释：2项元组：self的查询或表格行属性（rows）、空列表；保存到查询或表格行（rows）、self.rows。
        rows, self.rows = self.rows, []
        # 学习注释：返回查询或表格行（rows），结束本次函数调用。
        return rows
    # 学习注释：函数close接收调用时的上下文；把这一段可复用处理封装起来。
    def close(self):
        # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
        pass


# 学习注释：定义FakeMilvus对象，集中保存下方属性和方法。
class FakeMilvus:
    # 学习注释：函数__init__接收fail；把这一段可复用处理封装起来。
    def __init__(self, fail=None):
        # 学习注释：包含outage_events的字典；保存到self.collections。
        self.collections = {"outage_events": [{"event_id": "OLD", "text": "old"}]}
        # 学习注释：fail；保存到self.fail。
        self.fail = fail
        # 学习注释：False（不成立）；保存到self.closed。
        self.closed = False
    # 学习注释：函数has_collection接收name；把这一段可复用处理封装起来。
    def has_collection(self, name):
        # 学习注释：返回name包含在self的collections属性（collections），结束本次函数调用。
        return name in self.collections
    # 学习注释：函数upsert接收name、查询或表格行（rows）；把这一段可复用处理封装起来。
    def upsert(self, name, rows):
        # 学习注释：只有self.fail等于'write'才进入这一分支。
        if self.fail == "write":
            # 学习注释：以异常终止这一条路径：'write failed'；上层不能把它当作正常成功。
            raise RuntimeError("write failed")
        # 学习注释：把查询或表格行（rows）的各元素追加到self.collections[name]。
        self.collections[name].extend(rows)
    # 学习注释：函数flush接收name；把这一段可复用处理封装起来。
    def flush(self, name): pass
    # 学习注释：函数load_collection接收name；把这一段可复用处理封装起来。
    def load_collection(self, name):
        # 学习注释：只有self.fail等于'load'才进入这一分支。
        if self.fail == "load":
            # 学习注释：以异常终止这一条路径：'load failed'；上层不能把它当作正常成功。
            raise RuntimeError("load failed")
    # 学习注释：函数query接收name；把这一段可复用处理封装起来。
    def query(self, name, **kwargs):
        # 学习注释：返回1项列表：包含count(*)的字典，结束本次函数调用。
        return [{"count(*)": len(self.collections[name])}]
    # 学习注释：函数query_iterator接收collection_name；把这一段可复用处理封装起来。
    def query_iterator(self, collection_name, **kwargs):
        # 学习注释：遍历self.collections[collection_name]，将dict(r)收集为列表；保存到查询或表格行（rows）。
        rows = [dict(r) for r in self.collections[collection_name]]
        # 学习注释：只有self.fail等于'content'才进入这一分支。
        if self.fail == "content":
            # 学习注释：'wrong'；保存到rows[0]['text']。
            rows[0]["text"] = "wrong"
        # 学习注释：返回调用FakeIterator，处理查询或表格行（rows），结束本次函数调用。
        return FakeIterator(rows)
    # 学习注释：函数search接收调用时的上下文；把这一段可复用处理封装起来。
    def search(self, **kwargs):
        # 学习注释：只有self.fail等于'search'才进入这一分支。
        if self.fail == "search":
            # 学习注释：以异常终止这一条路径：'search failed'；上层不能把它当作正常成功。
            raise RuntimeError("search failed")
        # 学习注释：返回1项列表：1项列表：包含id的字典，结束本次函数调用。
        return [[{"id": 0}]]
    # 学习注释：函数drop_collection接收调用时的上下文；把这一段可复用处理封装起来。
    def drop_collection(self, *args):
        # 学习注释：以异常终止这一条路径：'不得删除任何已有集合'；上层不能把它当作正常成功。
        raise AssertionError("不得删除任何已有集合")
    # 学习注释：函数rename_collection接收调用时的上下文；把这一段可复用处理封装起来。
    def rename_collection(self, *args):
        # 学习注释：以异常终止这一条路径：'不得重命名集合'；上层不能把它当作正常成功。
        raise AssertionError("不得重命名集合")
    # 学习注释：函数close接收调用时的上下文；把这一段可复用处理封装起来。
    def close(self):
        # 学习注释：True（成立）；保存到self.closed。
        self.closed = True


# 学习注释：本测试验证：构建新向量集合时不删除旧版本集合。
@pytest.mark.parametrize("failure", ["write", "load", "content", "search", None])
async def test_new_collection_never_deletes_old(dataset, monkeypatch, failure):
    # 学习注释：调用FakeMilvus，处理failure；保存到外部服务客户端（client）。
    client = FakeMilvus(failure)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "_client", lambda: client)
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "_create_collection", lambda c, name: c.collections.setdefault(name, []))
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector.embeddings, "embed_texts",
                        AsyncMock(return_value=[[0.1] * vector.DIM for _ in dataset.corpus]))
    # 学习注释：只有failure为真/非空才进入这一分支。
    if failure:
        # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
        with pytest.raises(RuntimeError):
            # 学习注释：vector.vectorize的执行结果，await等待结果后再继续。
            await vector.vectorize(dataset.corpus_path, "outage_events_new")
    else:
        # 学习注释：vector.vectorize的执行结果，await等待结果后再继续；保存到result。
        result = await vector.vectorize(dataset.corpus_path, "outage_events_new")
        # 学习注释：测试要求：result等于包含collection、rows的字典；不满足就失败。
        assert result == {"collection": "outage_events_new", "rows": 4}
    # 学习注释：测试要求：client.collections['outage_events']等于1项列表：包含event_id、text的字典；不满足就失败。
    assert client.collections["outage_events"] == [{"event_id": "OLD", "text": "old"}]
    # 学习注释：测试要求：client.closed为真/非空；不满足就失败。
    assert client.closed


# 学习注释：本测试验证：目标集合已经存在时拒绝覆盖，保护已发布版本。
async def test_existing_collection_not_overwritten(dataset, monkeypatch):
    # 学习注释：调用FakeMilvus；保存到外部服务客户端（client）。
    client = FakeMilvus()
    # 学习注释：空列表；保存到client.collections['outage_events_existing']。
    client.collections["outage_events_existing"] = []
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector, "_client", lambda: client)
    # 学习注释：创建可await的测试替身，模拟返回值或异常；保存到embed。
    embed = AsyncMock()
    # 学习注释：临时替换依赖，控制测试返回值或故障，结束后恢复。
    monkeypatch.setattr(vector.embeddings, "embed_texts", embed)
    # 学习注释：测试要求这一块抛出指定异常；没有抛出或类型不符都会导致测试失败。
    with pytest.raises(ValueError, match="已存在"):
        # 学习注释：vector.vectorize的执行结果，await等待结果后再继续。
        await vector.vectorize(dataset.corpus_path, "outage_events_existing")
    # 学习注释：调用embed.assert_not_awaited。
    embed.assert_not_awaited()
