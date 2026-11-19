# 阅读导航：数据版本与请求快照
# 本文件作用：SQLite、语料和Milvus集合绑定同一版本；请求开始固定版本，后续工具沿用该快照。
# 建议读法：先读DatasetSnapshot、dataset_scope、pinned_dataset，再看校验和原子写入；发布入口在scripts/query/publish.py。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""版本化产物与请求快照：整套验证后切换清单，旧布局标记为未校验。"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入hashlib：内容哈希，用于检查文件是否变化。
import hashlib
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入os：环境变量与系统文件操作。
import os
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re
# 学习注释：从contextlib引入contextmanager，用于上下文管理器，管理进入/退出步骤。
from contextlib import contextmanager
# 学习注释：从contextvars引入ContextVar，用于在异步调用链中保存当前请求的独立上下文。
from contextvars import ContextVar
# 学习注释：从dataclasses引入dataclass，用于简化数据对象的字段和构造方法。
from dataclasses import dataclass
# 学习注释：从datetime引入date, datetime，用于日期时间解析与计算。
from datetime import date, datetime
# 学习注释：从functools引入wraps，用于保留装饰函数信息或组合函数。
from functools import wraps
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path
# 学习注释：从uuid引入uuid4，用于生成请求、会话或版本的随机标识。
from uuid import uuid4

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parents[2] / 'data' / 'masked'；保存到DATA_DIR。
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "masked"
# 学习注释：按目录层级拼接文件路径 DATA_DIR / 'version.json'；保存到VERSION_PATH。
VERSION_PATH = DATA_DIR / "version.json"
# 学习注释：包含start、end、basis、completeness的字典；保存到DEFAULT_COVERAGE。
DEFAULT_COVERAGE = {
    "start": "2025-01-01", "end": "2026-01-01",
    "basis": "项目声明的 2025 年抽样台账范围，并非全量业务覆盖",
    "completeness": "sample",
}


# 学习注释：自动生成数据对象构造方法；frozen=True阻止字段被随手改写，强调快照不可变。
@dataclass(frozen=True)
class DatasetSnapshot:
    # 学习注释：声明版本号（version）字段，类型为str；值由构造对象时提供。
    version: str
    # 学习注释：声明SQLite文件路径（db_path）字段，类型为Path；值由构造对象时提供。
    db_path: Path
    # 学习注释：声明事件语料文件路径（corpus_path）字段，类型为Path；值由构造对象时提供。
    corpus_path: Path
    # 学习注释：声明Milvus集合名（collection）字段，类型为str；值由构造对象时提供。
    collection: str
    # 学习注释：声明数据范围起始日（coverage_start）字段，类型为str；值由构造对象时提供。
    coverage_start: str
    # 学习注释：声明数据范围结束边界（coverage_end）字段，类型为str；值由构造对象时提供。
    coverage_end: str
    # 学习注释：声明数据覆盖范围的声明依据（coverage_basis）字段，类型为str；值由构造对象时提供。
    coverage_basis: str
    # 学习注释：声明数据一致性标记（consistency）字段，类型为str；值由构造对象时提供。
    consistency: str
    # 学习注释：None（尚无值）；保存到向量模型名（embed_model）。
    embed_model: str | None = None

    # 学习注释：函数connect接收调用时的上下文；把这一段可复用处理封装起来。
    def connect(self):
        # 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
        import sqlite3
        # 学习注释：通过mode=ro只读打开快照数据库；查询不能修改已发布版本。
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        # 学习注释：让查询行可按列名读取，例如row["事件序号"]，便于转成字典。
        conn.row_factory = sqlite3.Row
        # 学习注释：返回数据库连接（conn），结束本次函数调用。
        return conn


# 学习注释：用ContextVar保存当前请求快照；它不会像普通全局变量那样让并发请求互相覆盖。
_SNAPSHOT: ContextVar[DatasetSnapshot | None] = ContextVar("gridmate_dataset", default=None)
# 学习注释：同样在请求上下文保存请求ID，用来核对统计与检索是否属于同一轮。
_REQUEST_ID: ContextVar[str | None] = ContextVar("gridmate_request", default=None)


# 学习注释：使用时间和随机后缀生成新版本标识，避免同一秒发布时重名。
def new_version() -> str:
    # 学习注释：返回计算 datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]，结束本次函数调用。
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]


# 学习注释：分块读取文件并计算SHA256指纹，检查内容是否与发布记录一致。
def file_sha256(path: Path) -> str:
    # 学习注释：调用hashlib.sha256；保存到digest。
    digest = hashlib.sha256()
    # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with path.open("rb") as stream:
        # 学习注释：每次读1MB，直到读出空字节串；大文件不用一次全部放入内存。
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            # 学习注释：将新字段合并到digest，同名字段会覆盖旧值。
            digest.update(block)
    # 学习注释：返回把哈希结果转换为十六进制字符串，便于保存和比较，结束本次函数调用。
    return digest.hexdigest()


# 学习注释：把事件ID转为字符串并排序后计算哈希，使集合校验不受输入顺序影响。
def event_ids_sha256(ids) -> str:
    # 学习注释：返回把哈希结果转换为十六进制字符串，便于保存和比较，结束本次函数调用。
    return hashlib.sha256(json.dumps(sorted(map(str, ids)), ensure_ascii=False).encode()).hexdigest()


# 学习注释：函数read_version接收调用时的上下文；把这一段可复用处理封装起来。
def read_version() -> dict | None:
    # 学习注释：只有不满足（检查路径是否存在）才进入这一分支。
    if not VERSION_PATH.exists():
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 损坏清单必须报错，不能悄悄改查另一份数据库。
    value = json.loads(VERSION_PATH.read_text(encoding="utf-8"))
    # 学习注释：只有不满足（检查value是否属于dict类型）才进入这一分支。
    if not isinstance(value, dict):
        # 学习注释：以异常终止这一条路径：'数据版本清单格式错误'；上层不能把它当作正常成功。
        raise ValueError("数据版本清单格式错误")
    # 学习注释：返回value，结束本次函数调用。
    return value


# 学习注释：校验起止日期和范围依据；当前项目只允许声明抽样台账。
def validate_coverage(coverage: dict) -> None:
    # 学习注释：遍历2项元组：'start'、'end'，每轮把当前项交给key。
    for key in ("start", "end"):
        # 学习注释：从coverage读取键key，缺少时取''；保存到value。
        value = coverage.get(key, "")
        # 学习注释：只有不满足（检查value是否属于str类型），或者不满足（调用re.fullmatch，处理'\\d{4}-\\d{2}-\\d{2}'）才进入这一分支。
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            # 学习注释：以异常终止这一条路径：'覆盖范围必须是 YYYY-MM-DD'；上层不能把它当作正常成功。
            raise ValueError("覆盖范围必须是 YYYY-MM-DD")
        # 学习注释：调用date.fromisoformat，处理value。
        date.fromisoformat(value)
    # 学习注释：只有coverage['start']不小于coverage中键/位置为'end'的值，或者不满足（从coverage读取键'basis'，缺少时取None）才进入这一分支。
    if coverage["start"] >= coverage["end"] or not coverage.get("basis"):
        # 学习注释：以异常终止这一条路径：'覆盖范围需 start < end，且必须说明范围依据'；上层不能把它当作正常成功。
        raise ValueError("覆盖范围需 start < end，且必须说明范围依据")
    # 学习注释：只有coverage.get('completeness')不等于'sample'才进入这一分支。
    if coverage.get("completeness") != "sample":
        # 学习注释：以异常终止这一条路径：'当前项目仅支持声明为抽样台账'；上层不能把它当作正常成功。
        raise ValueError("当前项目仅支持声明为抽样台账")


# 学习注释：函数_artifact_path接收数据版本清单（manifest）、key；把这一段可复用处理封装起来。
def _artifact_path(manifest: dict, key: str) -> Path:
    # 学习注释：把路径解析成绝对路径；保存到release。
    release = (DATA_DIR / "versions" / manifest["version"]).resolve()
    # 学习注释：把路径解析成绝对路径；保存到path。
    path = (DATA_DIR / manifest[key]).resolve()
    # 学习注释：解析后的产物必须直接位于对应版本目录，避免清单通过路径跳转指向其他文件。
    if path.parent != release:
        # 学习注释：以异常终止这一条路径：'产物必须位于对应的独立版本目录'；上层不能把它当作正常成功。
        raise ValueError("产物必须位于对应的独立版本目录")
    # 学习注释：返回path，结束本次函数调用。
    return path


# 学习注释：校验版本目录、文件哈希、数量、范围和向量模型，返回可用快照。
def validate_manifest(manifest: dict) -> DatasetSnapshot:
    # 学习注释：只有manifest.get('schema_version')不等于2才进入这一分支。
    if manifest.get("schema_version") != 2:
        # 学习注释：以异常终止这一条路径：'只允许切换经过整套验证的 v2 清单'；上层不能把它当作正常成功。
        raise ValueError("只允许切换经过整套验证的 v2 清单")
    # 学习注释：从manifest读取键'version'，缺少时取''；保存到版本号（version）。
    version = manifest.get("version", "")
    # 学习注释：只有不满足（调用re.fullmatch，处理'[A-Za-z0-9_]+'）才进入这一分支。
    if not re.fullmatch(r"[A-Za-z0-9_]+", version):
        # 学习注释：以异常终止这一条路径：'版本号格式错误'；上层不能把它当作正常成功。
        raise ValueError("版本号格式错误")
    # 学习注释：只有manifest.get('collection')不等于计算 'outage_events_' + version才进入这一分支。
    if manifest.get("collection") != "outage_events_" + version:
        # 学习注释：以异常终止这一条路径：'集合必须绑定当前版本'；上层不能把它当作正常成功。
        raise ValueError("集合必须绑定当前版本")
    # 学习注释：从manifest读取键'corpus_lines'，缺少时取None；保存到count。
    count = manifest.get("corpus_lines")
    # 学习注释：只有type(count)不是同一对象/空值int，或者count小于1，或者manifest.get('collection_rows')不等于count才进入这一分支。
    if type(count) is not int or count < 1 or manifest.get("collection_rows") != count:
        # 学习注释：以异常终止这一条路径：'语料和向量数量不一致或为空'；上层不能把它当作正常成功。
        raise ValueError("语料和向量数量不一致或为空")
    # 学习注释：manifest中键/位置为'coverage'的值；保存到coverage。
    coverage = manifest["coverage"]
    # 学习注释：validate_coverage的执行结果。
    validate_coverage(coverage)
    # 学习注释：调用_artifact_path，处理数据版本清单（manifest）；保存到db。
    db = _artifact_path(manifest, "db_path")
    # 学习注释：调用_artifact_path，处理数据版本清单（manifest）；保存到事件级语料（corpus）。
    corpus = _artifact_path(manifest, "corpus_path")
    # 学习注释：遍历2项元组：2项元组：db、'db_sha256'、2项元组：事件级语料（corpus）、'corpus_sha256'，每轮把当前项交给path、key。
    for path, key in ((db, "db_sha256"), (corpus, "corpus_sha256")):
        # 学习注释：把实际文件内容哈希与清单对比；只检查文件名或条数不足以发现内容变化。
        if file_sha256(path) != manifest.get(key):
            # 学习注释：以异常终止这一条路径：把花括号内的变量填入f字符串，生成文字；上层不能把它当作正常成功。
            raise ValueError(f"数据产物校验失败：{path.name}")
    # 学习注释：只有不满足（调用re.fullmatch，处理'[a-f0-9]{64}'）才进入这一分支。
    if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("event_ids_sha256", "")):
        # 学习注释：以异常终止这一条路径：'缺少事件集合校验信息'；上层不能把它当作正常成功。
        raise ValueError("缺少事件集合校验信息")
    # 学习注释：只有不满足（从manifest.get('embedding', {})读取键'model'，缺少时取None）才进入这一分支。
    if not manifest.get("embedding", {}).get("model"):
        # 学习注释：以异常终止这一条路径：'缺少向量模型信息'；上层不能把它当作正常成功。
        raise ValueError("缺少向量模型信息")
    # 学习注释：返回调用DatasetSnapshot，处理版本号（version），结束本次函数调用。
    return DatasetSnapshot(
        version, db, corpus, manifest["collection"],
        coverage["start"], coverage["end"], coverage["basis"], "verified_bundle",
        manifest["embedding"]["model"],
    )


# 学习注释：先完整写临时文件并刷盘，再原子替换目标，避免读到半份JSON。
def atomic_json(path: Path, value: dict) -> None:
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    path.parent.mkdir(parents=True, exist_ok=True)
    # 学习注释：使用随机后缀创建同目录临时文件，避免并发写入时临时名碰撞。
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
        with tmp.open("x", encoding="utf-8") as stream:
            # 学习注释：把Python对象编码为JSON并写入已打开的文件。
            json.dump(value, stream, ensure_ascii=False, indent=2)
            # 学习注释：把Python文件缓冲送交操作系统。
            stream.flush()
            # 学习注释：要求操作系统把文件缓冲写入存储设备。
            os.fsync(stream.fileno())
        # 学习注释：完整写入并刷盘后一次替换目标文件，读取者不会读到只写了一半的JSON。
        os.replace(tmp, path)
    finally:
        # 学习注释：只有检查路径是否存在才进入这一分支。
        if tmp.exists():
            # 学习注释：删除指定文件，例如本次临时文件或发布锁。
            tmp.unlink()


# 学习注释：归档完整清单后切换当前清单；同一版本的内容不允许覆盖。
def write_version(manifest: dict) -> dict:
    """调用方须持有 publication_lock；写档案后只切换一个当前清单。"""
    # 学习注释：validate_manifest的执行结果。
    validate_manifest(manifest)
    # 学习注释：按目录层级拼接文件路径 DATA_DIR / 'versions' / manifest['version'] / 'manifest.json'；保存到archive。
    archive = DATA_DIR / "versions" / manifest["version"] / "manifest.json"
    # 学习注释：只有检查路径是否存在才进入这一分支。
    if archive.exists():
        # 学习注释：只有json.loads(archive.read_text(encoding='utf-8'))不等于数据版本清单（manifest）才进入这一分支。
        if json.loads(archive.read_text(encoding="utf-8")) != manifest:
            # 学习注释：以异常终止这一条路径：'已发布版本不可覆盖'；上层不能把它当作正常成功。
            raise ValueError("已发布版本不可覆盖")
    else:
        # 学习注释：atomic_json的执行结果。
        atomic_json(archive, manifest)
    # 学习注释：atomic_json的执行结果。
    atomic_json(VERSION_PATH, manifest)
    # 学习注释：返回数据版本清单（manifest），结束本次函数调用。
    return manifest


# 学习注释：独占创建发布锁文件，限制同时发布；正常退出时删除，进程被杀可能留下锁。
@contextmanager
def publication_lock():
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 学习注释：按目录层级拼接文件路径 DATA_DIR / '.publish.lock'；保存到path。
    path = DATA_DIR / ".publish.lock"
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：O_CREAT加O_EXCL表示仅当锁文件不存在时创建；同时发布者中只有一个能成功。
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # 学习注释：以异常终止这一条路径：'已有发布/回滚锁；若上次异常退出，确认无发布进程后再清理锁文件'；上层不能把它当作正常成功。
        raise RuntimeError("已有发布/回滚锁；若上次异常退出，确认无发布进程后再清理锁文件") from None
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：调用os.fdopen，处理fd；with结束时调用退出逻辑，关闭资源或恢复上下文。
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            # 学习注释：把Python对象编码为JSON并写入已打开的文件。
            json.dump({"pid": os.getpid()}, stream)
        # 学习注释：把控制权交给with块或迭代调用者；恢复后继续执行清理步骤。
        yield
    finally:
        # 学习注释：删除指定文件，例如本次临时文件或发布锁。
        path.unlink(missing_ok=True)


# 学习注释：优先加载校验过的v2版本；旧目录布局只能标为未验证兼容模式。
def load_snapshot() -> DatasetSnapshot:
    # 学习注释：调用read_version；保存到数据版本清单（manifest）。
    manifest = read_version()
    # 学习注释：只有数据版本清单（manifest）为真/非空，并且manifest.get('schema_version')等于2才进入这一分支。
    if manifest and manifest.get("schema_version") == 2:
        # 学习注释：返回validate_manifest的执行结果，结束本次函数调用。
        return validate_manifest(manifest)
    # 学习注释：只有数据版本清单（manifest）为真/非空，并且'schema_version'包含在数据版本清单（manifest）才进入这一分支。
    if manifest and "schema_version" in manifest:
        # 学习注释：以异常终止这一条路径：'不支持的数据清单版本'；上层不能把它当作正常成功。
        raise ValueError("不支持的数据清单版本")
    # 学习注释：按目录层级拼接文件路径 DATA_DIR / 'gridmate.db'；保存到db。
    db = DATA_DIR / "gridmate.db"
    # 学习注释：返回调用DatasetSnapshot，处理计算 'legacy_' + db_mtime_fingerprint(str(db))，结束本次函数调用。
    return DatasetSnapshot(
        "legacy_" + db_mtime_fingerprint(str(db)), db, DATA_DIR / "event_corpus.jsonl",
        "outage_events", DEFAULT_COVERAGE["start"], DEFAULT_COVERAGE["end"],
        DEFAULT_COVERAGE["basis"], "legacy_unverified",
    )


# 学习注释：优先取当前异步调用链已有快照；没有时才读取当前清单。
def current_snapshot() -> DatasetSnapshot:
    # 学习注释：返回按or短路取第一个真值；都为空/假则取最后一个值（_SNAPSHOT.get() or load_snapshot()），结束本次函数调用。
    return _SNAPSHOT.get() or load_snapshot()


# 学习注释：在请求上下文中固定快照和请求ID；嵌套调用复用，退出后恢复原上下文。
@contextmanager
def dataset_scope(snapshot: DatasetSnapshot | None = None):
    # 学习注释：外层请求已经固定版本时直接复用，不在内部工具调用时重新读取当前清单。
    if _SNAPSHOT.get() is not None:
        # 学习注释：把控制权交给with块或迭代调用者；恢复后继续执行清理步骤。
        yield _SNAPSHOT.get()
        # 学习注释：提前结束当前函数，不再执行后面的步骤。
        return
    # 学习注释：按or短路取第一个真值；都为空/假则取最后一个值（snapshot or load_snapshot()）；保存到本次请求固定的数据版本（snapshot）。
    snapshot = snapshot or load_snapshot()
    # 学习注释：记录快照到当前上下文，保存token以便退出时恢复之前的值。
    token = _SNAPSHOT.set(snapshot)
    # 学习注释：为本次最外层请求生成唯一ID，后续两个工具沿用该ID。
    request_token = _REQUEST_ID.set(uuid4().hex)
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：把固定快照交给with块；块内整个Agent及工具链运行结束后再回来清理上下文。
        yield snapshot
    finally:
        # 学习注释：恢复进入作用域前的请求ID，避免后续任务继承错误标识。
        _REQUEST_ID.reset(request_token)
        # 学习注释：恢复进入作用域前的快照；当前请求完成后允许新请求读取新发布版本。
        _SNAPSHOT.reset(token)


# 学习注释：装饰异步函数，使它与内部工具在同一次调用中共享数据版本。
def pinned_dataset(fn):
    # 学习注释：保留原函数名称等元数据，避免装饰后日志和工具识别只看到wrapped。
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        # 学习注释：进入固定数据版本的请求上下文；内部工具复用，退出时恢复旧上下文。
        with dataset_scope():
            # 学习注释：把调用者的位置/关键字参数原样传入，并在同一版本上下文中等待原异步函数完成。
            return await fn(*args, **kwargs)
    # 学习注释：返回wrapped，结束本次函数调用。
    return wrapped


# 学习注释：函数current_request_id接收调用时的上下文；把这一段可复用处理封装起来。
def current_request_id() -> str | None:
    # 学习注释：返回从_REQUEST_ID读取键，缺少时取None，结束本次函数调用。
    return _REQUEST_ID.get()


# 学习注释：函数current_dataset_version接收调用时的上下文；把这一段可复用处理封装起来。
def current_dataset_version() -> str:
    # 学习注释：返回current_snapshot()的版本号属性（version），结束本次函数调用。
    return current_snapshot().version


# 学习注释：函数db_mtime_fingerprint接收SQLite文件路径（db_path）；把这一段可复用处理封装起来。
def db_mtime_fingerprint(db_path: str) -> str:
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：调用Path(db_path).stat；保存到统计证据或文件属性（stat）。
        stat = Path(db_path).stat()
        # 学习注释：返回把花括号内的变量填入f字符串，生成文字，结束本次函数调用。
        return f"{stat.st_mtime_ns}_{stat.st_size}"
    except OSError:
        # 学习注释：返回'unknown'，结束本次函数调用。
        return "unknown"


# 学习注释：把当前请求、数据版本和范围声明附到每份工具证据上。
def evidence_context() -> dict:
    # 学习注释：current_snapshot的执行结果；保存到本次请求固定的数据版本（snapshot）。
    snapshot = current_snapshot()
    # 学习注释：返回包含request_id、dataset_version、data_consistency、coverage_basis、warnings的字典，结束本次函数调用。
    return {
        "request_id": current_request_id(),
        "dataset_version": snapshot.version,
        "data_consistency": snapshot.consistency,
        "coverage_basis": snapshot.coverage_basis,
        "warnings": ([] if snapshot.consistency == "verified_bundle" else
                     ["当前沿用旧数据布局，统计库与向量库尚未通过整套发布校验"]),
    }
