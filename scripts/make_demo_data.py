# 阅读导航：合成台账生成
# 本文件作用：生成完全虚构的6事件/8用户行CSV，供演示与手工核对，不代表真实业务效果。
# 建议读法：运行脚本会创建文件；输出目录必须不存在，避免覆盖数据。六月总数5，线路1六月2。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""生成完全虚构的演示 CSV；不读取、复制或切换业务数据。"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入csv：读写CSV表格。
import csv
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path


# 学习注释：函数generate接收本轮报告目录（folder）；把这一段可复用处理封装起来。
def generate(folder: Path) -> None:
    # 新目录才能写入，避免覆盖已有业务 CSV。
    folder.mkdir(parents=True, exist_ok=False)
    # 学习注释：8项列表：7项元组：'SYN001'、'线路1'、'供服中心1'、'2025-06-03'、'故障停电'、'合成案例：电缆终端损坏导致开关跳闸，隔离后抢修。'、10、7项元组：'SYN001'、'线路1'、'…；保存到查询或表格行（rows）。
    rows = [
        ("SYN001", "线路1", "供服中心1", "2025-06-03", "故障停电", "合成案例：电缆终端损坏导致开关跳闸，隔离后抢修。", 10),
        ("SYN001", "线路1", "供服中心1", "2025-06-03", "故障停电", "合成案例：电缆终端损坏导致开关跳闸，隔离后抢修。", 20),
        ("SYN002", "线路1", "供服中心1", "2025-06-10", "计划检修停电", "合成案例：按计划停电检修开关站。", 30),
        ("SYN003", "线路2", "供服中心2", "2025-07-02", "故障停电", "合成案例：雷击断线导致开关跳闸，重合失败。", 40),
        ("SYN004", "线路1", "供服中心1", "2025-05-31", "计划检修停电", "合成案例：五月安排检修。", 50),
        ("SYN004", "线路2", "供服中心2", "2025-06-01", "故障停电", "合成案例：六月另一用户发生设备故障。", 60),
        ("SYN005", "线路32", "供服中心2", "2025-06-15", "计划施工停电", "合成案例：配网优化施工，新增电缆连接；未发生电缆故障。", 70),
        ("SYN006", "线路2", "供服中心2", "2025-06-20", "计划检修停电", "合成案例：分类记为停电，但备注为带电检修，待人工确认。", 80),
    ]
    # 学习注释：遍历查询或表格行（rows），将dict(zip(('事件序号', '线路名称', '供服中心', '停电起始时间', '停电性质', '备注说明', '缺供电量'), …收集为列表；保存到users。
    users = [dict(zip(("事件序号", "线路名称", "供服中心", "停电起始时间", "停电性质", "备注说明", "缺供电量"), row)) for row in rows]
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、row。
    for i, row in enumerate(users, 1):
        # 学习注释：row中键/位置为'停电起始时间'的值；保存到day。
        day = row["停电起始时间"]
        # 学习注释：将新字段合并到row，同名字段会覆盖旧值。
        row.update(停电起始时间=day+" 10:00:00", 停电终止时间=day+" 11:00:00",
                   用户编码=f"SYN_U{i}", 用户名称=f"虚构用户{i}", 电压等级="10", 时户数=1)
    # 学习注释：遍历sorted({r['事件序号'] for r in users})，将{'事件序号': eid, '标签': '合成演示', '大类': '合成数据'}收集为列表；保存到事件列表（events）。
    events = [{"事件序号": eid, "标签": "合成演示", "大类": "合成数据"}
              for eid in sorted({r["事件序号"] for r in users})]
    # 学习注释：遍历users，将{key: row[key] for key in ('事件序号', '线路名称', '供服中心', '停电起始时间', '停电终止时间'…收集为列表；保存到segments。
    segments = [{key: row[key] for key in ("事件序号", "线路名称", "供服中心", "停电起始时间", "停电终止时间", "停电性质", "备注说明", "电压等级")} for row in users]
    # 学习注释：遍历enumerate(('线路1', '线路2', '线路32'), 1)，将{'线路名称': name, '线路编码': f'SYN_L{i}'}收集为列表；保存到lines。
    lines = [{"线路名称": name, "线路编码": f"SYN_L{i}"} for i, name in enumerate(("线路1", "线路2", "线路32"), 1)]
    # 学习注释：遍历4项元组：2项元组：'users_masked.csv'、users、2项元组：'segments_masked.csv'、segments、2项元组：'events_sample.csv'、事件列表（eve…，每轮把当前项交给filename、records。
    for filename, records in (("users_masked.csv", users), ("segments_masked.csv", segments),
                              ("events_sample.csv", events), ("lines_masked.csv", lines)):
        # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
        with (folder / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            # 学习注释：调用csv.DictWriter，处理stream；保存到writer。
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            # 学习注释：调用writer.writeheader。
            writer.writeheader()
            # 学习注释：调用writer.writerows，处理records。
            writer.writerows(records)
    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (folder / "README.txt").write_text(
        "完全虚构：6事件、8用户行。2025年6月按起始时间筛选5事件；线路1六月2事件。\n"
        "六月供服中心1缺供电量60；SYN004故意跨月/跨线路并保留属性冲突。\n"
        "不得作为业务效果评估集。生成CSV不会发布，发布请在独立演示副本执行。\n", encoding="utf-8")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用argparse.ArgumentParser；保存到parser。
    parser = argparse.ArgumentParser(description=__doc__)
    # 学习注释：调用parser.add_argument，处理'--output'。
    parser.add_argument("--output", type=Path, default=Path("data/demo_source"))
    # 学习注释：调用parser.parse_args；保存到args。
    args = parser.parse_args()
    # 学习注释：调用generate，处理args的output属性（output）。
    generate(args.output)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"合成 CSV 已生成：{args.output}")
