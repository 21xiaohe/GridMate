# -*- coding: utf-8 -*-
# 阅读导航：事件级语料聚合
# 本文件作用：把同一事件的多条用户记录合成一条检索文档，保留备注、属性明细和冲突标记。
# 建议读法：事件正文用于检索；统计仍按原用户行口径计算，不能拿聚合文本替代数值表。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账问数：按事件去重合并备注，构建「事件级」检索语料

把 gridmate.db 里 users 表（用户级记录）按事件序号聚合：
  - 备注说明：同一事件的多条不同备注去重合并成一段事件文本（检索单位=事件）
  - 事件级属性：停电性质、电压等级、供服中心、线路数、起始时间、时户数合计等

为什么按事件去重（而非每条备注独立建块）：
  实测同一事件最多有 4 条不同备注文本，且同一事件在用户表有 3.5 条记录。
  若按备注原文逐条建块，重复/近重复文本会占满召回结果——用户问"有没有类似停电"，
  前排可能全是同一个事件的几条重复记录。以「事件」为检索单位，召回的是"事件"，
  天然去重。

产出：data/masked/event_corpus.jsonl，每行一个事件：
  {事件序号, 事件文本(合并备注), 停电性质, 电压等级, 供服中心, 线路数, 涉及用户数,
   起始时间, 终止时间, 时户数合计, 缺供电量合计}
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入json：Python对象与JSON文本互转。
import json
# 学习注释：导入os：环境变量与系统文件操作。
import os
# 学习注释：导入sqlite3：本地SQLite数据库连接与SQL执行。
import sqlite3
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'data' / 'masked' / 'gridmate.db'；保存到DB_PATH。
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "masked" / "gridmate.db"
# 学习注释：按目录层级拼接文件路径 Path(__file__).resolve().parent.parent.parent / 'data' / 'masked' / 'event_corpus.jsonl'；保存到OUT_PATH。
OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "masked" / "event_corpus.jsonl"


# 学习注释：把同一事件的用户记录聚合成一条检索语料，保留多个备注和属性冲突。
def _agg_event(rows: list[dict]) -> dict:
    """把同一事件的若干条用户级记录，聚合成一个事件级语料。

    关键修复（v2 评测发现）：
    - 同一事件内「停电性质」「供服中心」可能多值（实测 165/2000 事件性质冲突），
      不能简单「取第一条非空」——那会吞掉冲突、误导检索与溯源。
    - 改为：主性质仍取频次最高的单值（兼容 outage_type 字段），但额外产出
      「停电性质明细」（含频次的完整分布）和「属性冲突」标记，暴露多值情况。
    - 备注改用「 ；」分隔并在段首保留各自性质前缀，避免不同作业混拼成语义混乱的一串。
    """
    # 学习注释：从collections引入Counter，用于计数器和按键分组容器。
    from collections import Counter

    # 备注去重合并：保留顺序，去掉重复；同时记录每条备注对应的停电性质
    seen = set()
    # 学习注释：空列表；保存到note_items。
    note_items = []  # [(备注, 停电性质)]
    # 学习注释：遍历查询或表格行（rows），每轮把当前项交给r。
    for r in rows:
        # 学习注释：清理r.get('备注说明') or ''首尾或指定一侧的字符；保存到n。
        n = (r.get("备注说明") or "").strip()
        # 学习注释：只有n为真/非空，并且n不包含在seen才进入这一分支。
        if n and n not in seen:
            # 学习注释：调用seen.add，处理n。
            seen.add(n)
            # 学习注释：向note_items末尾添加2项元组：n、清理r.get('停电性质') or ''首尾或指定一侧的字符。
            note_items.append((n, (r.get("停电性质") or "").strip()))

    # 停电性质：频次统计，主性质=最高频，明细=完整分布
    type_counter = Counter((r.get("停电性质") or "").strip() for r in rows if (r.get("停电性质") or "").strip())
    # 学习注释：只有type_counter为真/非空才进入这一分支。
    if type_counter:
        # 学习注释：type_counter.most_common(1)[0]中键/位置为0的值；保存到main_type。
        main_type = type_counter.most_common(1)[0][0]
        # 学习注释：用指定分隔符连接各段文本；保存到type_detail。
        type_detail = "、".join(f"{t}({c})" for t, c in type_counter.most_common())
        # 学习注释：len(type_counter)大于1；保存到停电性质是否存在多种归类（type_conflict）。
        type_conflict = len(type_counter) > 1
    else:
        # 学习注释：''；保存到main_type。
        main_type = ""
        # 学习注释：''；保存到type_detail。
        type_detail = ""
        # 学习注释：False（不成立）；保存到停电性质是否存在多种归类（type_conflict）。
        type_conflict = False

    # 供服中心：同样可能多值（实测 274/2000），保留明细 + 冲突标记
    center_counter = Counter((r.get("供服中心") or "").strip() for r in rows if (r.get("供服中心") or "").strip())
    # 学习注释：只有center_counter为真/非空才进入这一分支。
    if center_counter:
        # 学习注释：center_counter.most_common(1)[0]中键/位置为0的值；保存到main_center。
        main_center = center_counter.most_common(1)[0][0]
        # 学习注释：用指定分隔符连接各段文本；保存到center_detail。
        center_detail = "、".join(f"{t}({c})" for t, c in center_counter.most_common())
        # 学习注释：len(center_counter)大于1；保存到供服中心是否存在多种归类（center_conflict）。
        center_conflict = len(center_counter) > 1
    else:
        # 学习注释：''；保存到main_center。
        main_center = ""
        # 学习注释：''；保存到center_detail。
        center_detail = ""
        # 学习注释：False（不成立）；保存到供服中心是否存在多种归类（center_conflict）。
        center_conflict = False

    # 事件级单值属性：取第一条非空（这些字段组内一致，实测电压等级 0 冲突）
    # 学习注释：函数_first接收key；把这一段可复用处理封装起来。
    def _first(key):
        # 学习注释：遍历查询或表格行（rows），每轮把当前项交给r。
        for r in rows:
            # 学习注释：清理r.get(key) or ''首尾或指定一侧的字符；保存到v。
            v = (r.get(key) or "").strip()
            # 学习注释：只有v为真/非空才进入这一分支。
            if v:
                # 学习注释：返回v，结束本次函数调用。
                return v
        # 学习注释：返回''，结束本次函数调用。
        return ""

    # 时间属性：明确口径为「最早开始、最晚结束」（事件整体时间包络），
    # 而不是「取第一条非空」——同一事件不同用户/线段可能分别停送电，
    # 起始/终止时间组内多值（实测 608/662 个事件），「取第一条」是偶然的、
    # 无定义依据的，会让「事件起止时间」失真，也与按日过滤的用户行口径不一致。
    # 学习注释：函数_min_time接收key；把这一段可复用处理封装起来。
    def _min_time(key):
        # 学习注释：遍历查询或表格行（rows），只保留满足条件的项，将(r.get(key) or '').strip()收集为列表；保存到vals。
        vals = [(r.get(key) or "").strip() for r in rows if (r.get(key) or "").strip()]
        # 学习注释：返回vals为真/非空时取调用min，处理vals，否则取''，结束本次函数调用。
        return min(vals) if vals else ""

    # 学习注释：函数_max_time接收key；把这一段可复用处理封装起来。
    def _max_time(key):
        # 学习注释：遍历查询或表格行（rows），只保留满足条件的项，将(r.get(key) or '').strip()收集为列表；保存到vals。
        vals = [(r.get(key) or "").strip() for r in rows if (r.get(key) or "").strip()]
        # 学习注释：返回vals为真/非空时取调用max，处理vals，否则取''，结束本次函数调用。
        return max(vals) if vals else ""

    # 用户级度量的合计（正确口径：单表内 SUM，不 join）
    # 学习注释：函数_sum接收key；把这一段可复用处理封装起来。
    def _sum(key):
        # 学习注释：0.0；保存到s。
        s = 0.0
        # 学习注释：遍历查询或表格行（rows），每轮把当前项交给r。
        for r in rows:
            # 学习注释：执行可能失败的步骤；except处理异常，异常处理后由上层决定返回、重试或继续。
            try:
                # 学习注释：用把按or短路取第一个真值；都为空/假则取最后一个值（r.get(key) or 0）转换为浮点数更新s；下方+=等写法把计算结果存回原变量。
                s += float(r.get(key) or 0)
            except (TypeError, ValueError):
                # 学习注释：此处暂不增加执行语句；例如自定义异常只需继承已有行为。
                pass
        # 学习注释：返回s，结束本次函数调用。
        return s

    # 学习注释：转为不重复元素集合；保存到line_names。
    line_names = set()
    # 学习注释：遍历查询或表格行（rows），每轮把当前项交给r。
    for r in rows:
        # 学习注释：清理r.get('线路名称') or ''首尾或指定一侧的字符；保存到ln。
        ln = (r.get("线路名称") or "").strip()
        # 学习注释：只有ln为真/非空才进入这一分支。
        if ln:
            # 学习注释：调用line_names.add，处理ln。
            line_names.add(ln)

    # 合并备注正文：段首保留性质前缀 + 「；」分隔，检索模型能区分不同作业
    text_parts = []
    # 学习注释：遍历note_items，每轮把当前项交给n、t。
    for n, t in note_items:
        # 学习注释：只有t为真/非空，并且不满足（调用n.startswith，处理t）才进入这一分支。
        if t and not n.startswith(t):
            # 学习注释：向text_parts末尾添加把花括号内的变量填入f字符串，生成文字。
            text_parts.append(f"【{t}】{n}")
        else:
            # 学习注释：向text_parts末尾添加n。
            text_parts.append(n)
    # 学习注释：用指定分隔符连接各段文本；保存到event_text。
    event_text = "；".join(text_parts)

    # 学习注释：返回包含事件序号、事件文本、停电性质、停电性质明细、停电性质冲突、电压等级的字典，结束本次函数调用。
    return {
        "事件序号": _first("事件序号"),
        "事件文本": event_text,  # 合并后的备注作为检索正文（保留性质前缀）
        "停电性质": main_type,                       # 主性质（单值，兼容 outage_type）
        "停电性质明细": type_detail,                  # 完整分布（暴露多值冲突）
        "停电性质冲突": type_conflict,                # 是否存在组内性质多值
        "电压等级": _first("电压等级"),
        "供服中心": main_center,                      # 主供服中心（单值）
        "供服中心明细": center_detail,                # 完整分布
        "供服中心冲突": center_conflict,              # 是否存在组内多值
        "线路名称": "、".join(sorted(line_names)),
        "线路数": len(line_names),
        "涉及用户数": len(rows),
        "起始时间": _min_time("停电起始时间"),   # 事件时间包络：最早开始
        "终止时间": _max_time("停电终止时间"),   # 事件时间包络：最晚结束
        "时户数合计": round(_sum("时户数"), 4),
        "缺供电量合计": round(_sum("缺供电量"), 4),
        "备注条数": len(note_items),
        "source_row_ids": [r["id"] for r in rows],
    }


# 学习注释：函数build接收SQLite文件路径（db_path）；把这一段可复用处理封装起来。
def build(db_path: Path = DB_PATH) -> list[dict]:
    # 学习注释：打开SQLite连接，mode=ro表示只读；保存到数据库连接（conn）。
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    # 学习注释：sqlite3的Row属性（Row）；保存到conn.row_factory。
    conn.row_factory = sqlite3.Row
    # 学习注释：创建执行SQL和读取结果的游标；保存到SQL游标（cur）。
    cur = conn.cursor()
    # 按事件序号分组，取每条记录的关键字段
    cur.execute("""
        SELECT id, 事件序号, 备注说明, 停电性质, 电压等级, 供服中心, 线路名称,
               停电起始时间, 停电终止时间, 时户数, 缺供电量
        FROM users
        ORDER BY 事件序号, id
    """)
    # 学习注释：空字典；保存到事件列表（events）。
    events: dict[str, list[dict]] = {}
    # 学习注释：遍历读取SQL剩余全部结果行，每轮把当前项交给row。
    for row in cur.fetchall():
        # 学习注释：创建或浅复制字典：row；保存到d。
        d = dict(row)
        # 学习注释：向events.setdefault(d['事件序号'], [])末尾添加d。
        events.setdefault(d["事件序号"], []).append(d)
    # 学习注释：关闭conn占用的连接或资源。
    conn.close()

    # 学习注释：遍历events.items()，将_agg_event(rows)收集为列表；保存到事件级语料（corpus）。
    corpus = [_agg_event(rows) for _, rows in events.items()]
    # 学习注释：返回事件级语料（corpus），结束本次函数调用。
    return corpus


# 学习注释：生成事件语料并逐行写成JSONL，返回同一批事件对象。
def write_corpus(db_path: Path, out_path: Path) -> list[dict]:
    # 学习注释：只有检查路径是否存在才进入这一分支。
    if (out_path.parent / "manifest.json").exists():
        # 学习注释：以异常终止这一条路径：'已发布版本不可原地重建'；上层不能把它当作正常成功。
        raise ValueError("已发布版本不可原地重建")
    # 学习注释：调用build，处理SQLite文件路径（db_path）；保存到事件级语料（corpus）。
    corpus = build(db_path)
    # 学习注释：只有事件级语料（corpus）为空或为False才进入这一分支。
    if not corpus:
        # 学习注释：以异常终止这一条路径：'语料为空，拒绝发布'；上层不能把它当作正常成功。
        raise ValueError("语料为空，拒绝发布")
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 学习注释：调用out_path.with_suffix，处理'.jsonl.tmp'；保存到tmp。
    tmp = out_path.with_suffix(".jsonl.tmp")
    # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
    with open(tmp, "w", encoding="utf-8") as f:
        # 学习注释：遍历事件级语料（corpus），每轮把当前项交给e。
        for e in corpus:
            # 学习注释：调用f.write，处理计算 json.dumps(e, ensure_ascii=False) + '\n'。
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # 学习注释：原子替换目标文件，读者看到旧文件或完整新文件。
    os.replace(tmp, out_path)
    # 学习注释：返回事件级语料（corpus），结束本次函数调用。
    return corpus


# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main():
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser()
    # 学习注释：调用ap.add_argument，处理'--db'。
    ap.add_argument("--db", type=Path, default=DB_PATH)
    # 学习注释：调用ap.add_argument，处理'--output'。
    ap.add_argument("--output", type=Path, default=OUT_PATH)
    # 学习注释：调用ap.parse_args；保存到args。
    args = ap.parse_args()
    # 学习注释：write_corpus的执行结果；保存到事件级语料（corpus）。
    corpus = write_corpus(args.db, args.output)

    # 打印统计
    n = len(corpus)
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到notes_cnt。
    notes_cnt = sum(e["备注条数"] for e in corpus)
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到multi。
    multi = sum(1 for e in corpus if e["备注条数"] > 1)
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到停电性质是否存在多种归类（type_conflict）。
    type_conflict = sum(1 for e in corpus if e.get("停电性质冲突"))
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到供服中心是否存在多种归类（center_conflict）。
    center_conflict = sum(1 for e in corpus if e.get("供服中心冲突"))
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"事件数: {n}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"备注总条数: {notes_cnt}（事件级去重后，平均每事件 {notes_cnt/n:.2f} 条）")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"含多条不同备注的事件: {multi}（{multi/n:.1%}）——这些若不按事件去重会重复召回")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"停电性质冲突的事件: {type_conflict}（{type_conflict/n:.1%}）——已保留明细+冲突标记，不再取首值吞信息")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"供服中心冲突的事件: {center_conflict}（{center_conflict/n:.1%}）")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"输出: {args.output}")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用main。
    main()
