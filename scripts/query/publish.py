# 阅读导航：发布与回滚入口
# 本文件作用：依次建库、生成语料、向量化、校验；全部成功后才切换version.json。
# 建议读法：这是真实写操作。学习时先阅读，演练使用独立合成副本；回滚也会验证目标产物。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""整套发布及回滚：版本目录/集合不可变，全部验证后仅替换 version.json。"""
# 学习注释：延迟解析类型注解，允许类型引用稍后定义的类；这行不是普通业务模块导入。
from __future__ import annotations

# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入asyncio：异步任务、等待、锁和超时。
import asyncio
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re
# 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
import sqlite3
# 学习注释：导入sys：解释器参数、导入路径与进程退出码。
import sys
# 学习注释：从datetime引入datetime，用于日期时间解析与计算。
from datetime import datetime
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：调用sys.path.insert，处理0。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 学习注释：从app.core导入versioning，复用对应模块的实现。
from app.core import versioning


# 学习注释：函数_run_db接收SQLite文件路径（db_path）、source_dir；把这一段可复用处理封装起来。
def _run_db(db_path: Path, source_dir: Path):
    # 学习注释：从scripts.query.build_db导入build，复用对应模块的实现。
    from scripts.query.build_db import build
    # 学习注释：返回调用build，处理SQLite文件路径（db_path），结束本次函数调用。
    return build(db_path, source_dir)


# 学习注释：函数_run_corpus接收SQLite文件路径（db_path）、事件语料文件路径（corpus_path）；把这一段可复用处理封装起来。
def _run_corpus(db_path: Path, corpus_path: Path):
    # 学习注释：从scripts.query.build_event_corpus导入write_corpus，复用对应模块的实现。
    from scripts.query.build_event_corpus import write_corpus
    # 学习注释：返回write_corpus的执行结果，结束本次函数调用。
    return write_corpus(db_path, corpus_path)


# 学习注释：异步函数_run_vectorize接收事件语料文件路径（corpus_path）、Milvus集合名（collection）；把这一段可复用处理封装起来。
async def _run_vectorize(corpus_path: Path, collection: str) -> dict:
    # 学习注释：从scripts.query.vectorize_events导入vectorize，复用对应模块的实现。
    from scripts.query.vectorize_events import vectorize
    # 学习注释：返回vectorize的执行结果，await等待结果后再继续，结束本次函数调用。
    return await vectorize(corpus_path, collection)


# 学习注释：比较数据库事件ID集合与语料事件ID集合，防止仅数量相等但内容错位。
def _validate_event_sets(db_path: Path, corpus: list[dict]) -> str:
    # 学习注释：打开SQLite连接，mode=ro表示只读；保存到数据库连接（conn）。
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
    try:
        # 学习注释：只有conn.execute('PRAGMA integrity_check').fetchone()[0]不等于'ok'才进入这一分支。
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            # 学习注释：以异常终止这一条路径：'SQLite 完整性校验失败'；上层不能把它当作正常成功。
            raise RuntimeError("SQLite 完整性校验失败")
        # 学习注释：遍历conn.execute('SELECT DISTINCT 事件序号 FROM users')，将str(row[0])收集为集合；保存到ids。
        ids = {str(row[0]) for row in conn.execute("SELECT DISTINCT 事件序号 FROM users")}
    finally:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()
    # 学习注释：遍历事件级语料（corpus），将str(e['事件序号'])收集为列表；保存到corpus_ids。
    corpus_ids = [str(e["事件序号"]) for e in corpus]
    # 学习注释：只有ids为空或为False，或者len(set(corpus_ids))不等于计算corpus_ids的元素数量，或者set(corpus_ids)不等于ids才进入这一分支。
    if not ids or len(set(corpus_ids)) != len(corpus_ids) or set(corpus_ids) != ids:
        # 学习注释：以异常终止这一条路径：'数据库和语料事件集合不一致'；上层不能把它当作正常成功。
        raise RuntimeError("数据库和语料事件集合不一致")
    # 学习注释：返回versioning.event_ids_sha256的执行结果，结束本次函数调用。
    return versioning.event_ids_sha256(ids)


# 学习注释：在新目录和新集合构建整套数据；所有验证成功后才激活版本。
async def publish(source_dir: Path | None = None, coverage: dict | None = None) -> dict:
    # 学习注释：共享本机配置对象settings；模型名、服务地址和凭据统一由config.py加载。
    from app.config import settings
    # 学习注释：从scripts.query.vectorize_events导入DIM，复用对应模块的实现。
    from scripts.query.vectorize_events import DIM
    # 学习注释：复制覆盖范围声明；未提供时使用项目默认的2025年抽样范围。
    coverage = dict(coverage or versioning.DEFAULT_COVERAGE)
    # 学习注释：versioning.validate_coverage的执行结果。
    versioning.validate_coverage(coverage)
    # 学习注释：整段构建和切换使用独占发布锁，避免两个发布/回滚同时修改当前清单。
    with versioning.publication_lock():
        # 学习注释：versioning.new_version的执行结果；保存到版本号（version）。
        version = versioning.new_version()
        # 学习注释：按目录层级拼接文件路径 versioning.DATA_DIR / 'versions' / version；保存到release。
        release = versioning.DATA_DIR / "versions" / version
        # 学习注释：创建独立且全新的版本目录；已存在则报错，不覆盖历史数据。
        release.mkdir(parents=True, exist_ok=False)
        # 学习注释：按目录层级拼接文件路径 release / 'gridmate.db'；保存到db。
        db = release / "gridmate.db"
        # 学习注释：按目录层级拼接文件路径 release / 'event_corpus.jsonl'；保存到事件语料文件路径（corpus_path）。
        corpus_path = release / "event_corpus.jsonl"
        # 学习注释：新Milvus集合绑定新版本号，旧请求仍可读取旧集合。
        collection = "outage_events_" + version
        # 任一步失败仅留下未激活的版本目录/集合，不修改当前版本及旧产物。
        _run_db(db, source_dir or versioning.DATA_DIR)
        # 学习注释：从刚生成的新数据库构建事件语料，避免误读当前旧库。
        corpus = _run_corpus(db, corpus_path)
        # 学习注释：核对SQLite事件集合与语料事件集合完全一致，再生成集合指纹。
        ids_hash = _validate_event_sets(db, corpus)
        # 学习注释：调用真实Embedding并写新Milvus集合；此时尚未激活业务版本。
        vector = await _run_vectorize(corpus_path, collection)
        # 学习注释：只有vector['collection']不等于Milvus集合名（collection），或者vector['rows']不等于计算事件级语料（corpus）的元素数量才进入这一分支。
        if vector["collection"] != collection or vector["rows"] != len(corpus):
            # 学习注释：以异常终止这一条路径：'向量构建返回的版本或条数不一致'；上层不能把它当作正常成功。
            raise RuntimeError("向量构建返回的版本或条数不一致")
        # 学习注释：包含schema_version、version、published_at、db_path、corpus_path、db_sha256的字典；保存到数据版本清单（manifest）。
        manifest = {
            "schema_version": 2, "version": version,
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": db.relative_to(versioning.DATA_DIR).as_posix(),
            "corpus_path": corpus_path.relative_to(versioning.DATA_DIR).as_posix(),
            "db_sha256": versioning.file_sha256(db),
            "corpus_sha256": versioning.file_sha256(corpus_path),
            "event_ids_sha256": ids_hash,
            "corpus_lines": len(corpus), "collection": collection,
            "collection_rows": vector["rows"], "coverage": coverage,
            "embedding": {"model": settings.embed_model, "dimension": DIM},
        }
        # 学习注释：所有产物检查成功后才归档并切换清单；之前异常只留下未激活产物。
        return versioning.write_version(manifest)


# 学习注释：重新验证指定历史版本的数据库、语料和向量内容，再切换当前清单。
def rollback(version: str) -> dict:
    # 学习注释：从scripts.query.vectorize_events导入_client, verify_collection，复用对应模块的实现。
    from scripts.query.vectorize_events import _client, verify_collection
    # 学习注释：只有不满足（调用re.fullmatch，处理'[A-Za-z0-9_]+'）才进入这一分支。
    if not re.fullmatch(r"[A-Za-z0-9_]+", version):
        # 学习注释：以异常终止这一条路径：'回滚版本格式错误'；上层不能把它当作正常成功。
        raise ValueError("回滚版本格式错误")
    # 学习注释：整段构建和切换使用独占发布锁，避免两个发布/回滚同时修改当前清单。
    with versioning.publication_lock():
        # 学习注释：按目录层级拼接文件路径 versioning.DATA_DIR / 'versions' / version / 'manifest.json'；保存到archive。
        archive = versioning.DATA_DIR / "versions" / version / "manifest.json"
        # 学习注释：把JSON文本解析为Python对象；保存到数据版本清单（manifest）。
        manifest = json.loads(archive.read_text(encoding="utf-8"))
        # 学习注释：versioning.validate_manifest的执行结果；保存到本次请求固定的数据版本（snapshot）。
        snapshot = versioning.validate_manifest(manifest)
        # 学习注释：遍历snapshot.corpus_path.read_text(encoding='utf-8').splitlines()，只保留满足条件的项，将json.loads(line)收集为列表；保存到事件级语料（corpus）。
        corpus = [json.loads(line) for line in snapshot.corpus_path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        # 学习注释：只有_validate_event_sets(snapshot.db_path, corpus)不等于manifest中键/位置为'event_ids_sha256'的值才进入这一分支。
        if _validate_event_sets(snapshot.db_path, corpus) != manifest["event_ids_sha256"]:
            # 学习注释：以异常终止这一条路径：'回滚版本事件集合校验失败'；上层不能把它当作正常成功。
            raise RuntimeError("回滚版本事件集合校验失败")
        # 学习注释：调用_client；保存到外部服务客户端（client）。
        client = _client()
        # 学习注释：执行可能失败的步骤；finally无论成功失败都负责清理。
        try:
            # 学习注释：回滚也要读回目标向量集合内容，不能只因旧版本曾成功发布就直接相信仍完好。
            verify_collection(client, snapshot.collection, corpus)
        finally:
            # 学习注释：关闭client占用的连接或资源。
            client.close()
        # 学习注释：所有产物检查成功后才归档并切换清单；之前异常只留下未激活产物。
        return versioning.write_version(manifest)


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
async def main(argv=None) -> int:
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用ap.add_argument，处理'--source-dir'。
    ap.add_argument("--source-dir", type=Path, default=None)
    # 学习注释：调用ap.add_argument，处理'--rollback'。
    ap.add_argument("--rollback", metavar="VERSION", help="验证并切回一个已有的 v2 发布版本")
    # 学习注释：调用ap.add_argument，处理'--coverage-start'。
    ap.add_argument("--coverage-start", default=versioning.DEFAULT_COVERAGE["start"])
    # 学习注释：调用ap.add_argument，处理'--coverage-end'。
    ap.add_argument("--coverage-end", default=versioning.DEFAULT_COVERAGE["end"])
    # 学习注释：调用ap.add_argument，处理'--coverage-basis'。
    ap.add_argument("--coverage-basis", default=versioning.DEFAULT_COVERAGE["basis"])
    # 学习注释：遍历3项元组：'--skip-db'、'--skip-corpus'、'--skip-vector'，每轮把当前项交给flag。
    for flag in ("--skip-db", "--skip-corpus", "--skip-vector"):
        # 学习注释：调用ap.add_argument，处理flag。
        ap.add_argument(flag, action="store_true", help="已禁用：不允许发布不完整版本")
    # 学习注释：调用ap.parse_args，处理argv；保存到args。
    args = ap.parse_args(argv)
    # 学习注释：只有args.skip_db为真/非空，或者args.skip_corpus为真/非空，或者args.skip_vector为真/非空才进入这一分支。
    if args.skip_db or args.skip_corpus or args.skip_vector:
        # 学习注释：记录error级别日志，用于定位运行状态。
        ap.error("不能跳过单个产物发布；请使用独立构建脚本调试，或 --rollback 切回完整旧版")
    # 学习注释：只有args.rollback为真/非空，并且args.source_dir为真/非空才进入这一分支。
    if args.rollback and args.source_dir:
        # 学习注释：记录error级别日志，用于定位运行状态。
        ap.error("--rollback 不能与 --source-dir 同用")
    # 学习注释：只有args.rollback为真/非空才进入这一分支。
    if args.rollback:
        # 学习注释：rollback的执行结果；保存到数据版本清单（manifest）。
        manifest = rollback(args.rollback)
    else:
        # 学习注释：publish的执行结果，await等待结果后再继续；保存到数据版本清单（manifest）。
        manifest = await publish(args.source_dir, {
            "start": args.coverage_start, "end": args.coverage_end,
            "basis": args.coverage_basis, "completeness": "sample",
        })
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"已切换完整数据版本：{manifest['version']}")
    # 学习注释：返回0，结束本次函数调用。
    return 0


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用sys.exit，处理启动事件循环，执行脚本的异步主函数。
    sys.exit(asyncio.run(main()))
