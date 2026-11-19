# -*- coding: utf-8 -*-
# 阅读导航：CSV导入SQLite
# 本文件作用：创建固定表结构，把CSV字段转换后写入数据库，再完成一致性检查。
# 建议读法：建库会写文件；先看create_schema，再看import_rows和build，理解事件/用户/线段三种粒度。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：建 SQLite 库 + 导入脱敏数据

把 data/masked 下的三个脱敏 CSV 导入 SQLite，建四张表：
  - events   事件清单（事件序号、标签、大类）
  - users    用户级记录（时户数/缺供电量等用户级度量）
  - segments 线段级记录（停电户数等线段级度量）
  - lines    线路台账

关联键设计（基于真实数据实测）：
  - 事件序号：三表通用主键（跨表一致）
  - 线路关联：靠「线路名称」假名（PseudoMapper 保证跨表一致；用户/线段表无线路编码列）

用法：
    python build_db.py --db ../data/masked/gridmate.db
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入csv：读写CSV表格。
import csv
# 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
import sqlite3
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'data' / 'masked'；保存到DATA_DIR。
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "masked"


# 学习注释：CSV 里的度量字段可能是空串/字符串，统一转 float；空则 None。
def _to_float(v):
    """CSV 里的度量字段可能是空串/字符串，统一转 float；空则 None。"""
    # 学习注释：只有v尚未设置（None）才进入这一分支。
    if v is None:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：清理str(v)首尾或指定一侧的字符；保存到s。
    s = str(v).strip()
    # 学习注释：只有s等于''才进入这一分支。
    if s == "":
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：返回把s转换为浮点数，结束本次函数调用。
        return float(s)
    except ValueError:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None


# 学习注释：函数_to_int接收v；把这一段可复用处理封装起来。
def _to_int(v):
    # 学习注释：只有v尚未设置（None）才进入这一分支。
    if v is None:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：清理str(v)首尾或指定一侧的字符；保存到s。
    s = str(v).strip()
    # 学习注释：只有s等于''才进入这一分支。
    if s == "":
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：返回把把s转换为浮点数转换为整数，结束本次函数调用。
        return int(float(s))
    except ValueError:
        # 学习注释：返回None（尚无值），结束本次函数调用。
        return None


# 学习注释：函数load_csv接收path；把这一段可复用处理封装起来。
def load_csv(path):
    # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with open(path, encoding="utf-8-sig") as f:
        # 学习注释：返回转为可逐项访问的列表：调用csv.DictReader，处理f，结束本次函数调用。
        return list(csv.DictReader(f))


# 学习注释：建立固定表与索引；表名和字段名由代码定义，用户只提供数据值。
def create_schema(conn):
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 事件清单
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            事件序号 TEXT PRIMARY KEY,
            标签 TEXT,
            大类 TEXT,
            用户记录数 INTEGER,
            线段记录数 INTEGER
        )
    """)
    # 用户级记录
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            事件序号 TEXT NOT NULL,
            供服中心 TEXT,
            用户编码 TEXT,
            用户名称 TEXT,
            单位名称 TEXT,
            线段编码 TEXT,
            线段名称 TEXT,
            线路名称 TEXT,
            变电站名称 TEXT,
            停电起始时间 TEXT,
            停电终止时间 TEXT,
            持续时间 REAL,
            电压等级 TEXT,
            停电性质 TEXT,
            停电设备名称 TEXT,
            停电技术名称 TEXT,
            停电责任名称 TEXT,
            时户数 REAL,
            缺供电量 REAL,
            总容量 REAL,
            停电容量 REAL,
            低压用户数 REAL,
            低压停电用户数 REAL,
            备注说明 TEXT,
            修改原因 TEXT
        )
    """)
    # 线段级记录
    cur.execute("""
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            事件序号 TEXT NOT NULL,
            供服中心 TEXT,
            线段编码 TEXT,
            线段名称 TEXT,
            线路名称 TEXT,
            变电站名称 TEXT,
            停电起始时间 TEXT,
            停电终止时间 TEXT,
            持续时间 REAL,
            电压等级 TEXT,
            停电性质 TEXT,
            停电设备名称 TEXT,
            停电技术名称 TEXT,
            停电责任名称 TEXT,
            停电户数 REAL,
            时户数 REAL,
            停电容量 REAL,
            缺供电量 REAL,
            备注说明 TEXT,
            修改原因 TEXT
        )
    """)
    # 线路台账
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lines (
            线路编码 TEXT,
            线路名称 TEXT,
            变电站名称 TEXT,
            供服中心 TEXT,
            投运日期 TEXT,
            线路性质 TEXT,
            供电区域 TEXT,
            线路长度_km REAL,
            总用户数 REAL
        )
    """)
    # 索引：问数高频过滤维度
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_event ON users(事件序号)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_line ON users(线路名称)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_supply ON users(供服中心)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_seg_event ON segments(事件序号)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_seg_line ON segments(线路名称)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_seg_supply ON segments(供服中心)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lines_name ON lines(线路名称)")
    # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lines_supply ON lines(供服中心)")
    # 学习注释：提交数据库事务，使本次写入生效。
    conn.commit()


# 学习注释：函数import_rows接收数据库连接（conn）、table、查询或表格行（rows）；把这一段可复用处理封装起来。
def import_rows(conn, table, rows):
    # 学习注释：只有查询或表格行（rows）为空或为False才进入这一分支。
    if not rows:
        # 学习注释：返回0，结束本次函数调用。
        return 0
    # 学习注释：转为可逐项访问的列表：读取rows[0]的全部键；保存到结果列名（cols）。
    cols = list(rows[0].keys())
    # 学习注释：用指定分隔符连接各段文本；保存到quoted。
    quoted = ",".join(f'"{c}"' for c in cols)
    # 学习注释：用指定分隔符连接各段文本；保存到placeholders。
    placeholders = ",".join("?" for _ in cols)
    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到待执行的SQL文本（sql）。
    sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
    # 学习注释：遍历查询或表格行（rows），将[r.get(c) for c in cols]收集为列表；保存到data。
    data = [[r.get(c) for c in cols] for r in rows]
    # 学习注释：对多行数据批量执行同一条带占位符的SQL。
    conn.executemany(sql, data)
    # 学习注释：提交数据库事务，使本次写入生效。
    conn.commit()
    # 学习注释：返回计算查询或表格行（rows）的元素数量，结束本次函数调用。
    return len(rows)


# 学习注释：函数build接收SQLite文件路径（db_path）、source_dir；把这一段可复用处理封装起来。
def build(db_path: Path, source_dir: Path = DATA_DIR) -> dict:
    # 学习注释：构造文件路径对象：SQLite文件路径（db_path）；保存到SQLite文件路径（db_path）。
    db_path = Path(db_path)
    # 学习注释：构造文件路径对象：source_dir；保存到source_dir。
    source_dir = Path(source_dir)
    # 学习注释：只有检查路径是否存在才进入这一分支。
    if (db_path.parent / "manifest.json").exists():
        # 学习注释：以异常终止这一条路径：'已发布版本不可原地重建'；上层不能把它当作正常成功。
        raise ValueError("已发布版本不可原地重建")
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 学习注释：在命令行输出本步骤结果或进度。
    print("加载脱敏 CSV...")
    # 学习注释：调用load_csv，处理按目录层级拼接文件路径 source_dir / 'users_masked.csv'；保存到users。
    users = load_csv(source_dir / "users_masked.csv")
    # 学习注释：调用load_csv，处理按目录层级拼接文件路径 source_dir / 'segments_masked.csv'；保存到segs。
    segs = load_csv(source_dir / "segments_masked.csv")
    # 学习注释：调用load_csv，处理按目录层级拼接文件路径 source_dir / 'lines_masked.csv'；保存到lines。
    lines = load_csv(source_dir / "lines_masked.csv")
    # 学习注释：调用load_csv，处理按目录层级拼接文件路径 source_dir / 'events_sample.csv'；保存到事件列表（events）。
    events = load_csv(source_dir / "events_sample.csv")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  users {len(users)} 行, segments {len(segs)} 行, lines {len(lines)} 行, events {len(events)} 行")

    # 全量重建：先导临时库，校验通过后原子替换目标库。
    # 之前的「CREATE IF NOT EXISTS + INSERT」重复执行会主键冲突（IntegrityError），
    # 各表单独 commit 失败还留部分新数据。改为「临时库 → 校验 → os.replace 切换」，
    # 重复执行可复现，失败不影响目标库（二次审阅问题 8）。
    tmp_path = db_path.with_suffix(db_path.suffix + ".tmp")
    # 学习注释：只有检查路径是否存在才进入这一分支。
    if tmp_path.exists():
        # 学习注释：删除指定文件，例如本次临时文件或发布锁。
        tmp_path.unlink()  # 清掉上次失败的残留

    # 学习注释：打开SQLite连接；保存到数据库连接（conn）。
    conn = sqlite3.connect(tmp_path)
    # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
    try:
        # 学习注释：在命令行输出本步骤结果或进度。
        print("建表...")
        # 学习注释：create_schema的执行结果。
        create_schema(conn)

        # 学习注释：在命令行输出本步骤结果或进度。
        print("导入数据...")
        # 学习注释：调用import_rows，处理数据库连接（conn）；保存到n_events。
        n_events = import_rows(conn, "events", events)
        # 学习注释：调用import_rows，处理数据库连接（conn）；保存到n_users。
        n_users = import_rows(conn, "users", users)
        # 学习注释：调用import_rows，处理数据库连接（conn）；保存到n_segs。
        n_segs = import_rows(conn, "segments", segs)
        # 学习注释：调用import_rows，处理数据库连接（conn）；保存到n_lines。
        n_lines = import_rows(conn, "lines", lines)

        # 完整校验：行数对得上才算成功（避免 zip 静默截断、部分导入）
        cur = conn.cursor()
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT COUNT(*) FROM events")
        # 学习注释：测试要求：cur.fetchone()[0]等于n_events；不满足就失败。
        assert cur.fetchone()[0] == n_events, "events 行数校验失败"
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT COUNT(*) FROM users")
        # 学习注释：测试要求：cur.fetchone()[0]等于n_users；不满足就失败。
        assert cur.fetchone()[0] == n_users, "users 行数校验失败"
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT COUNT(*) FROM segments")
        # 学习注释：测试要求：cur.fetchone()[0]等于n_segs；不满足就失败。
        assert cur.fetchone()[0] == n_segs, "segments 行数校验失败"
        # 学习注释：执行SQL并返回游标；参数绑定将数据值与SQL结构分开。
        cur.execute("SELECT COUNT(*) FROM lines")
        # 学习注释：测试要求：cur.fetchone()[0]等于n_lines；不满足就失败。
        assert cur.fetchone()[0] == n_lines, "lines 行数校验失败"
    except Exception:
        # 学习注释：关闭conn占用的连接或资源。
        conn.close()
        # 学习注释：只有检查路径是否存在才进入这一分支。
        if tmp_path.exists():
            # 学习注释：删除指定文件，例如本次临时文件或发布锁。
            tmp_path.unlink()
        # 学习注释：把当前捕获的异常继续抛给上层，保留原始错误类型。
        raise

    # 学习注释：关闭conn占用的连接或资源。
    conn.close()
    # 原子替换：os.replace 在 Windows 上也覆盖已有文件
    import os
    # 学习注释：原子替换目标文件，读者看到旧文件或完整新文件。
    os.replace(tmp_path, db_path)

    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"\n完成。导入 events={n_events}, users={n_users}, segments={n_segs}, lines={n_lines}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"数据库: {db_path}")
    # 学习注释：返回包含events、users、segments、lines的字典，结束本次函数调用。
    return {"events": n_events, "users": n_users, "segments": n_segs, "lines": n_lines}


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main():
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser()
    # 学习注释：调用ap.add_argument，处理'--db'。
    ap.add_argument("--db", type=Path, default=DATA_DIR / "gridmate.db")
    # 学习注释：调用ap.add_argument，处理'--source-dir'。
    ap.add_argument("--source-dir", type=Path, default=DATA_DIR)
    # 学习注释：调用ap.parse_args；保存到args。
    args = ap.parse_args()
    # 学习注释：调用build，处理args的db属性（db）。
    build(args.db, args.source_dir)


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用main。
    main()
