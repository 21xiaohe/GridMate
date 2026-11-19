# -*- coding: utf-8 -*-
# 阅读导航：抽样与假名化
# 本文件作用：从本地项目表格选取事件及关联行，替换名称和标识符，输出本地CSV。
# 建议读法：假名化不等于彻底匿名；自由文本可能残留识别信息。阅读脚本即可，勿为学习随意重跑真实数据处理。
# 语法提示：def定义函数；冒号后的缩进属于该代码块；return返回；await等待异步结果。

"""停电台账数据：筛选 + 分类型脱敏脚本（两阶段）

阶段一【筛选】：从三张原始表里，只提取问数项目真正需要的字段（约 20 列），
  丢弃审核/操作/报送/内部ID 等无关的 90 余列。按事件分层抽样，带出关联记录。

阶段二【脱敏】：对筛选后的数据分类型脱敏——
  - 维度字段（供服中心/线路/线段/设备/变电站名）→ 跨表一致假名（保留 group by 能力）
  - 标识符（用户编码/单位编码/记录ID）→ 哈希化（不参与查询，不保证无法重新识别）
  - 度量值（时户数/缺供电量/容量/户数）→ 原样保留（仍按业务资料管理）
  - 文本（备注说明/修改原因）→ 轻脱敏（保留技术术语，只换地名/机构/工程名）

关键设计：脱敏不"抹掉"分析维度，而是"换假名"，这样"长宁区停几次电"→"供服中心A停几次电"
  可继续按假名 group by；自由文本仍可能含敏感信息，产物只供本地验证。

用法：
    python -m scripts.data_prep.sample_and_mask --source-dir <本地数据目录> --events 2000 --out data/masked
输出：
    - events_sample.csv     抽样事件清单
    - users_masked.csv      脱敏后用户记录（精选列）
    - segments_masked.csv   脱敏后线段记录（精选列）
    - lines_masked.csv      脱敏后线路台账（仅抽样事件涉及的线路）
    - sampling_report.txt   抽样与脱敏报告
"""
# 学习注释：导入argparse：解析命令行选项。
import argparse
# 学习注释：导入csv：读写CSV表格。
import csv
# 学习注释：导入hashlib：内容哈希，用于检查文件是否变化。
import hashlib
# 学习注释：导入re：正则表达式，匹配日期、版本或名称格式。
import re
# 学习注释：从collections引入Counter, defaultdict，用于计数器和按键分组容器。
from collections import Counter, defaultdict
# 学习注释：从pathlib引入Path，用于跨平台文件路径对象。
from pathlib import Path

# 学习注释：导入openpyxl：读取Excel工作簿。
import openpyxl



# ============================================================
# 阶段一：字段筛选清单（只保留问数项目需要的列）
# ============================================================

# 停电用户导出表：保留的列（原列名 -> 新列名，None 表示沿用原名）
USER_KEEP = {
    "事件序号": "事件序号",          # 关联主键
    "供服中心名称": "供服中心名称",   # 分析维度（假名）
    "用户编码": "用户编码",          # 标识符（哈希）
    "用户名称": "用户名称",          # 标识符（假名，跨表一致）
    "单位名称": "单位名称",          # 标识符（假名）
    "线段编码": "线段编码",          # 关联键（哈希）
    "线段名称": "线段名称",          # 分析维度（假名）
    "线路名称": "线路名称",          # 分析维度（假名）
    "变电站名称": "变电站名称",       # 分析维度（假名）
    "停电起始时间": "停电起始时间",   # 维度（保留）
    "停电终止时间": "停电终止时间",   # 维度（保留）
    "持续时间": "持续时间",          # 度量（保留）
    "电压等级": "电压等级",          # 维度（保留，技术术语非隐私）
    "停电性质": "停电性质",          # 维度（保留）
    "停电设备名称": "停电设备名称",   # 维度（假名）
    "停电技术名称": "停电技术名称",   # 维度（保留，技术术语）
    "停电责任名称": "停电责任名称",   # 维度（保留，技术术语）
    "时户数": "时户数",              # 度量（保留）
    "缺供电量": "缺供电量",          # 度量（保留）
    "总容量": "总容量",              # 度量（保留）
    "停电容量": "停电容量",          # 度量（保留）
    "低压用户数": "低压用户数",       # 度量（保留）
    "低压停电用户数": "低压停电用户数",  # 度量（保留）
    "备注说明": "备注说明",          # 文本（轻脱敏）
    "修改原因": "修改原因",          # 文本（轻脱敏）
}

# 停电线段导出表：保留的列
SEG_KEEP = {
    "事件序号": "事件序号",
    "供服中心名称": "供服中心名称",
    "线段编码": "线段编码",
    "线段名称": "线段名称",
    "线路名称": "线路名称",
    "变电站名称": "变电站名称",
    "停电起始时间": "停电起始时间",
    "停电终止时间": "停电终止时间",
    "持续时间": "持续时间",
    "电压等级": "电压等级",
    "停电性质": "停电性质",
    "停电设备名称": "停电设备名称",
    "停电技术名称": "停电技术名称",
    "停电责任名称": "停电责任名称",
    "停电户数": "停电户数",
    "时户数": "时户数",
    "停电容量": "停电容量",
    "缺供电量": "缺供电量",
    "备注说明": "备注说明",
    "修改原因": "修改原因",
}

# 线路查询结果表：保留的列
LINE_KEEP = {
    "线路编码": "线路编码",          # 关联键（哈希）
    "线路名称": "线路名称",          # 分析维度（假名）
    "变电站名称": "变电站名称",       # 分析维度（假名）
    "供服中心名称": "供服中心名称",   # 分析维度（假名）
    "投运日期": "投运日期",          # 维度（保留）
    "线路性质": "线路性质",          # 维度（保留）
    "供电区域": "供电区域",          # 维度（保留）
    "线路合计(km)": "线路长度_km",   # 度量（保留）
    "总用户数": "总用户数",          # 度量（保留）
}


# ============================================================
# 脱敏工具
# ============================================================

# 学习注释：不可逆哈希编码（标识符用，不参与查询）。
def hash_id(v, salt="gridmate"):
    """不可逆哈希编码（标识符用，不参与查询）。"""
    # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到s。
    s = f"{salt}:{v}"
    # 学习注释：返回hashlib.sha256(s.encode('utf-8')).hexdigest()中键/位置为:16的值，结束本次函数调用。
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# 学习注释：定义PseudoMapper对象，集中保存下方属性和方法。
class PseudoMapper:
    """跨表一致的假名映射器。同一真实名 -> 同一个假名，保证 join 不丢关系。"""

    # 学习注释：函数__init__接收prefix；把这一段可复用处理封装起来。
    def __init__(self, prefix):
        # 学习注释：prefix；保存到self.prefix。
        self.prefix = prefix
        # 学习注释：空字典；保存到self.mapping。
        self.mapping = {}  # 真实名 -> 假名

    # 学习注释：函数map接收name；把这一段可复用处理封装起来。
    def map(self, name):
        # 学习注释：只有name为空或为False，或者str(name).strip()等于''才进入这一分支。
        if not name or str(name).strip() == "":
            # 学习注释：返回''，结束本次函数调用。
            return ""
        # 学习注释：清理str(name)首尾或指定一侧的字符；保存到key。
        key = str(name).strip()
        # 学习注释：只有key不包含在self的mapping属性（mapping）才进入这一分支。
        if key not in self.mapping:
            # 学习注释：把花括号内的变量填入f字符串，生成文字；保存到self.mapping[key]。
            self.mapping[key] = f"{self.prefix}{len(self.mapping) + 1}"
        # 学习注释：返回self.mapping中键/位置为key的值，结束本次函数调用。
        return self.mapping[key]


# 维度假名映射器（跨表一致）
pseudo_supply_center = PseudoMapper("供服中心")
# 学习注释：调用PseudoMapper，处理'线路'；保存到pseudo_line。
pseudo_line = PseudoMapper("线路")
# 学习注释：调用PseudoMapper，处理'线段'；保存到pseudo_segment。
pseudo_segment = PseudoMapper("线段")
# 学习注释：调用PseudoMapper，处理'设备'；保存到pseudo_device。
pseudo_device = PseudoMapper("设备")
# 学习注释：调用PseudoMapper，处理'变电站'；保存到pseudo_station。
pseudo_station = PseudoMapper("变电站")
# 学习注释：调用PseudoMapper，处理'用户'；保存到pseudo_user。
pseudo_user = PseudoMapper("用户")
# 学习注释：调用PseudoMapper，处理'单位'；保存到pseudo_org。
pseudo_org = PseudoMapper("单位")


# ============================================================
# 文本轻脱敏（保留技术术语，只换地名/机构/工程名）
# ============================================================

# 学习注释：20项元组：'浦东新区'、'浦东'、'崇明'、'嘉定'、'松江'、'金山'、'奉贤'、'青浦'、'闵行'、'宝山'、'静安'、'黄浦'、'长宁'、'徐汇'、'虹口'、'杨浦'、'普陀'、'市南'、'市北'、'市…；保存到_TEXT_DISTRICT。
_TEXT_DISTRICT = (
    "浦东新区", "浦东", "崇明", "嘉定", "松江", "金山", "奉贤", "青浦",
    "闵行", "宝山", "静安", "黄浦", "长宁", "徐汇", "虹口", "杨浦", "普陀",
    "市南", "市北", "市区",
)

# 电力技术词保护清单：这些词以「路/线」结尾，会被地址正则误杀（断路器→某路器）。
# 脱敏前先把它们整体替换成不含「路」字的占位符，地址脱敏后再还原。
# 注意：这是「有限兜底」，不是「无限扩充例外」——真正的方向性修复是收紧地址正则
# 要求门牌号（见 TEXT_PATTERNS），这里的白名单只补高频、确定的电力故障核心术语。
_TECH_TERMS = (
    "断路器", "配电线路", "输电线路", "架空线路", "电缆线路", "主干线路",
    "分支线路", "馈线", "出线", "进线", "母线", "支路", "主干线", "线路",
    # 含「路」的故障术语（单靠门牌号正则可能漏掉的裸词，兜底护住）
    "短路", "断路", "开路", "回路", "支路",
)


# 学习注释：把电力技术词替换成不含「路」字的占位符，防止被地址正则误杀。
def _protect_tech(text):
    """把电力技术词替换成不含「路」字的占位符，防止被地址正则误杀。

    占位符用「§」开头 + 序号 + 「§」，避免与真实文本冲突，且不含汉字、
    不含「路」字，地址正则完全匹配不到。
    """
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、term。
    for i, term in enumerate(sorted(_TECH_TERMS, key=len, reverse=True)):
        # 学习注释：调用text.replace，处理term；保存到文本（text）。
        text = text.replace(term, f"\u00a7{len(_TECH_TERMS) - i:02d}\u00a7")
    # 学习注释：返回文本（text），结束本次函数调用。
    return text


# 学习注释：把占位符还原成对应技术词。
def _restore_tech(text):
    """把占位符还原成对应技术词。"""
    # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给i、term。
    for i, term in enumerate(sorted(_TECH_TERMS, key=len, reverse=True)):
        # 学习注释：调用text.replace，处理把花括号内的变量填入f字符串，生成文字；保存到文本（text）。
        text = text.replace(f"\u00a7{len(_TECH_TERMS) - i:02d}\u00a7", term)
    # 学习注释：返回文本（text），结束本次函数调用。
    return text


# 学习注释：3项列表：2项元组：调用re.compile，处理计算 '(?:上海市|上海)?(?:' + '|'.join(_TEXT_DISTRICT) + ')'、'某区'、2项元组：调用re.compile，处理'…；保存到TEXT_PATTERNS。
TEXT_PATTERNS = [
    (re.compile(r"(?:上海市|上海)?(?:" + "|".join(_TEXT_DISTRICT) + r")"), "某区"),
    (re.compile(r"[\u4e00-\u9fa5]{2,6}(?:体育局|医院|学校|大学|公司|集团|公园|广场|大厦|小区|花园|新村|工厂|园区|供电公司|电力|部队)"), "某机构"),
    # 地址：真正的地名+路/街/大道/弄+**门牌号**。要求门牌号（\d+）是收紧识别依据的关键：
    # 技术词「短路/回路/开路」从不在「路」后跟门牌号，而真实地址（张江路5号、中山北路100号）
    # 几乎都带门牌号。这样既保住技术词，又不漏脱敏真实地址。
    # 裸路名（不带门牌号，如「张江路」）由镇名白名单（张江）兜底。
    (re.compile(r"[\u4e00-\u9fa5]{1,3}[\u4e00-\u9fa5]{0,3}(?:路|街|大道|弄)\d+号?"), "某路"),
]

# 确定无疑的真实乡镇名白名单（精确替换，避免误杀电力术语）
TOWN_NAMES = (
    "马桥", "西岑", "龚路", "金泽", "重固", "杨行", "莲盛", "新河",
    "申江", "瓦屑", "康桥", "华新", "北蔡", "周浦", "张江", "老港",
    "新场", "航头", "祝桥", "惠南", "大团", "芦潮港", "泥城", "书院",
    "万祥", "合庆", "高行", "高桥", "金桥", "唐镇", "泖港", "九亭",
    "车墩", "洞泾", "佘山", "泗泾", "新桥", "叶榭", "小昆山", "石湖荡",
    "罗店", "罗泾", "顾村", "祁连", "彭浦", "庙行", "淞南", "亭林",
    "漕泾", "朱泾", "廊下", "枫泾", "张堰", "吕巷", "干巷", "朱行",
    "山阳", "金卫", "兴塔", "钱圩", "松隐", "新农", "金山卫", "南桥",
    "奉城", "柘林", "四团", "青村", "金汇", "海湾", "朱家角", "赵巷",
    "白鹤", "练塘", "凤溪", "小蒸", "沈巷", "大盈", "华漕", "七宝",
    "梅陇", "浦江", "吴泾", "颛桥", "曹行", "杜行", "鲁汇", "堡镇",
    "庙镇", "陈家", "城桥", "向化", "港沿", "港西", "中兴", "建设",
    "绿华", "竖新", "马陆", "安亭", "南翔", "外冈", "徐行", "江桥",
    "娄塘", "华亭", "曹王", "葛隆", "横沔", "坦直", "下沙",
    "孙桥", "东海", "宣桥", "川沙新", "赵屯", "泰日", "徐泾", "三星", "塔汇",
)


# 学习注释：轻脱敏：保留技术术语和数字，只替换真实地名/机构/工程名。
def mask_text(text):
    """轻脱敏：保留技术术语和数字，只替换真实地名/机构/工程名。

    顺序：先护技术词（断路器/线路等）→ 脱敏地名/机构/地址 → 还原技术词，
    避免地址正则把「断路器」误杀成「某路器」、把「线路」误杀成「某路」。
    """
    # 学习注释：只有文本（text）为空或为False才进入这一分支。
    if not text:
        # 学习注释：返回''，结束本次函数调用。
        return ""
    # 学习注释：把文本（text）转换为字符串；保存到s。
    s = str(text)
    # 学习注释：调用_protect_tech，处理s；保存到s。
    s = _protect_tech(s)
    # 学习注释：遍历TEXT_PATTERNS，每轮把当前项交给pat、repl。
    for pat, repl in TEXT_PATTERNS:
        # 学习注释：调用pat.sub，处理repl；保存到s。
        s = pat.sub(repl, s)
    # 镇名白名单精确替换
    for tn in TOWN_NAMES:
        # 学习注释：调用s.replace，处理计算 tn + '镇'；保存到s。
        s = s.replace(tn + "镇", "某镇")
    # 兜底：清掉残留的"上海/上海市"
    s = s.replace("上海市", "某市").replace("上海", "某市")
    # 学习注释：返回调用_restore_tech，处理s，结束本次函数调用。
    return _restore_tech(s)


# ============================================================
# 数据加载与筛选
# ============================================================

# 学习注释：加载 Excel，返回 (表头, [{列名:值}])。
def load_sheet(path):
    """加载 Excel，返回 (表头, [{列名:值}])。"""
    # 学习注释：调用openpyxl.load_workbook，处理path；保存到wb。
    wb = openpyxl.load_workbook(path)
    # 学习注释：wb.worksheets中键/位置为0的值；保存到ws。
    ws = wb.worksheets[0]
    # 学习注释：遍历ws[1]，将c.value收集为列表；保存到header。
    header = [c.value for c in ws[1]]
    # 学习注释：空列表；保存到查询或表格行（rows）。
    rows = []
    # 学习注释：遍历产生循环使用的整数序列，结束值不包含在内，每轮把当前项交给r。
    for r in range(2, ws.max_row + 1):
        # 学习注释：空字典；保存到row。
        row = {}
        # 学习注释：遍历为每一项附上索引/序号，供定位候选或记录结果，每轮把当前项交给ci、h。
        for ci, h in enumerate(header):
            # 学习注释：ws.cell(row=r, column=ci + 1)的value属性（value）；保存到row[h]。
            row[h] = ws.cell(row=r, column=ci + 1).value
        # 学习注释：向查询或表格行（rows）末尾添加row。
        rows.append(row)
    # 学习注释：关闭wb占用的连接或资源。
    wb.close()
    # 学习注释：返回查询或表格行（rows），结束本次函数调用。
    return rows


# 学习注释：按字段清单提取列，返回 [{新列名: 值}]。
def project_rows(rows, keep_map):
    """按字段清单提取列，返回 [{新列名: 值}]。"""
    # 学习注释：空列表；保存到out。
    out = []
    # 学习注释：遍历查询或表格行（rows），每轮把当前项交给row。
    for row in rows:
        # 学习注释：空字典；保存到new。
        new = {}
        # 学习注释：遍历读取keep_map的键和值配对，每轮把当前项交给src、dst。
        for src, dst in keep_map.items():
            # 学习注释：从row读取键src，缺少时取None；保存到new[dst]。
            new[dst] = row.get(src)
        # 学习注释：向out末尾添加new。
        out.append(new)
    # 学习注释：返回out，结束本次函数调用。
    return out


# ============================================================
# 主流程
# ============================================================

# 学习注释：脚本主流程：按顺序准备环境、执行各步骤并保存结果；底部命令行入口会调用它。
def main():
    # 学习注释：调用argparse.ArgumentParser；保存到ap。
    ap = argparse.ArgumentParser()
    # 学习注释：调用ap.add_argument，处理'--source-dir'。
    ap.add_argument("--source-dir", type=Path, required=True, help="包含三份业务工作簿的本地目录")
    # 学习注释：调用ap.add_argument，处理'--events'。
    ap.add_argument("--events", type=int, default=2000, help="抽样事件数")
    # 学习注释：调用ap.add_argument，处理'--out'。
    ap.add_argument("--out", type=str, default="../data/masked")
    # 学习注释：调用ap.add_argument，处理'--seed'。
    ap.add_argument("--seed", type=int, default=42)
    # 学习注释：调用ap.parse_args；保存到args。
    args = ap.parse_args()

    # 学习注释：构造文件路径对象：args的out属性（out）；保存到out_dir。
    out_dir = Path(args.out)
    # 学习注释：创建目录；parents控制父目录创建，exist_ok控制已存在时是否报错。
    out_dir.mkdir(parents=True, exist_ok=True)

    # 学习注释：在命令行输出本步骤结果或进度。
    print("【阶段一】加载原始数据...")
    # 学习注释：调用load_sheet，处理按目录层级拼接文件路径 args.source_dir / '停电用户导出.xlsx'；保存到user_rows。
    user_rows = load_sheet(args.source_dir / "停电用户导出.xlsx")
    # 学习注释：调用load_sheet，处理按目录层级拼接文件路径 args.source_dir / '停电线段导出.xlsx'；保存到seg_rows。
    seg_rows = load_sheet(args.source_dir / "停电线段导出.xlsx")
    # 学习注释：调用load_sheet，处理按目录层级拼接文件路径 args.source_dir / '线路查询结果.xlsx'；保存到line_rows。
    line_rows = load_sheet(args.source_dir / "线路查询结果.xlsx")
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  用户表 {len(user_rows)} 行, 线段表 {len(seg_rows)} 行, 线路台账 {len(line_rows)} 行")

    # 1) 事件 -> 标签（从备注提取【标签】）
    event_tag = {}
    # 学习注释：调用defaultdict，处理list；保存到event_users。
    event_users = defaultdict(list)
    # 学习注释：调用defaultdict，处理list；保存到event_segs。
    event_segs = defaultdict(list)

    # 学习注释：遍历user_rows，每轮把当前项交给row。
    for row in user_rows:
        # 学习注释：清理str(row.get('事件序号', ''))首尾或指定一侧的字符；保存到ev。
        ev = str(row.get("事件序号", "")).strip()
        # 学习注释：只有ev为空或为False才进入这一分支。
        if not ev:
            # 学习注释：跳过这一轮剩余步骤，继续处理下一项或下一次尝试。
            continue
        # 学习注释：把按or短路取第一个真值；都为空/假则取最后一个值（row.get('备注说明', '') or ''）转换为字符串；保存到remark。
        remark = str(row.get("备注说明", "") or "")
        # 学习注释：调用re.match，处理'^【([^】]+)】'；保存到m。
        m = re.match(r"^【([^】]+)】", remark)
        # 学习注释：m为真/非空时取调用m.group，处理1，否则取'(无标签)'；保存到tag。
        tag = m.group(1) if m else "(无标签)"
        # 学习注释：只有ev不包含在event_tag才进入这一分支。
        if ev not in event_tag:
            # 学习注释：tag；保存到event_tag[ev]。
            event_tag[ev] = tag
        # 学习注释：向event_users[ev]末尾添加row。
        event_users[ev].append(row)

    # 学习注释：遍历seg_rows，每轮把当前项交给row。
    for row in seg_rows:
        # 学习注释：清理str(row.get('事件序号', ''))首尾或指定一侧的字符；保存到ev。
        ev = str(row.get("事件序号", "")).strip()
        # 学习注释：只有ev为真/非空才进入这一分支。
        if ev:
            # 学习注释：向event_segs[ev]末尾添加row。
            event_segs[ev].append(row)

    # 学习注释：转为可逐项访问的列表：读取event_tag的全部键；保存到all_events。
    all_events = list(event_tag.keys())
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  独立事件总数: {len(all_events)}")

    # 2) 标签大类归一化
    # 学习注释：函数big_class接收tag；把这一段可复用处理封装起来。
    def big_class(tag):
        # 学习注释：只有检查是否至少有一项条件成立才进入这一分支。
        if any(k in tag for k in ("故障", "接地")):
            # 学习注释：返回'故障类'，结束本次函数调用。
            return "故障类"
        # 学习注释：只有tag包含在8项元组：'技改'、'大修'、'更改'、'消缺'、'运维消缺'、'配网改造专项'、'配网基建'、'架空线入地'才进入这一分支。
        if tag in ("技改", "大修", "更改", "消缺", "运维消缺", "配网改造专项", "配网基建", "架空线入地"):
            # 学习注释：返回'技改/消缺类'，结束本次函数调用。
            return "技改/消缺类"
        # 学习注释：只有tag包含在3项元组：'业扩'、'低压业扩'、'低压业扩配套'才进入这一分支。
        if tag in ("业扩", "低压业扩", "低压业扩配套"):
            # 学习注释：返回'业扩类'，结束本次函数调用。
            return "业扩类"
        # 学习注释：只有tag包含在2项元组：'基建'、'代工'才进入这一分支。
        if tag in ("基建", "代工"):
            # 学习注释：返回'基建/代工类'，结束本次函数调用。
            return "基建/代工类"
        # 学习注释：只有tag包含在2项元组：'调电'、'临时停电'才进入这一分支。
        if tag in ("调电", "临时停电"):
            # 学习注释：返回'调电类'，结束本次函数调用。
            return "调电类"
        # 学习注释：只有tag包含在2项元组：'检修'、'E类检修'才进入这一分支。
        if tag in ("检修", "E类检修"):
            # 学习注释：返回'检修类'，结束本次函数调用。
            return "检修类"
        # 学习注释：只有tag包含在4项元组：'营商'、'用户'、'用户影响'、'用户故障'才进入这一分支。
        if tag in ("营商", "用户", "用户影响", "用户故障"):
            # 学习注释：返回'用户类'，结束本次函数调用。
            return "用户类"
        # 学习注释：返回'其他'，结束本次函数调用。
        return "其他"

    # 3) 分层抽样
    import random
    # 学习注释：调用random.Random，处理args的seed属性（seed）；保存到rng。
    rng = random.Random(args.seed)
    # 学习注释：调用defaultdict，处理list；保存到by_class。
    by_class = defaultdict(list)
    # 学习注释：遍历all_events，每轮把当前项交给ev。
    for ev in all_events:
        # 学习注释：向by_class[big_class(event_tag[ev])]末尾添加ev。
        by_class[big_class(event_tag[ev])].append(ev)

    # 学习注释：args的事件列表属性（events）；保存到total。
    total = args.events
    # 学习注释：30；保存到min_per。
    min_per = 30
    # 学习注释：遍历by_class.items()，将c→min(min_per, len(v))收集为字典；保存到allocated。
    allocated = {c: min(min_per, len(v)) for c, v in by_class.items()}
    # 学习注释：计算 total - sum(allocated.values())；保存到remaining。
    remaining = total - sum(allocated.values())
    # 学习注释：遍历by_class.items()，将c→len(v)收集为字典；保存到class_sizes。
    class_sizes = {c: len(v) for c, v in by_class.items()}
    # 学习注释：累计各项数值（布尔值True按1计数）；保存到total_size。
    total_size = sum(class_sizes.values())
    # 学习注释：只有remaining大于0才进入这一分支。
    if remaining > 0:
        # 学习注释：遍历class_sizes.items()，将c→int(remaining * s / total_size)收集为字典；保存到extra。
        extra = {c: int(remaining * s / total_size) for c, s in class_sizes.items()}
        # 学习注释：计算 remaining - sum(extra.values())；保存到left。
        left = remaining - sum(extra.values())
        # 学习注释：遍历按指定键排序后得到新列表，每轮把当前项交给c。
        for c in sorted(extra, key=lambda x: extra[x]):
            # 学习注释：只有left不大于0才进入这一分支。
            if left <= 0:
                # 学习注释：立即退出当前循环；不会继续处理本循环后面的候选或重试。
                break
            # 学习注释：用1更新extra[c]；下方+=等写法把计算结果存回原变量。
            extra[c] += 1
            # 学习注释：用1更新left；下方+=等写法把计算结果存回原变量。
            left -= 1
        # 学习注释：遍历extra，每轮把当前项交给c。
        for c in extra:
            # 学习注释：调用min，处理计算 allocated[c] + extra[c]；保存到allocated[c]。
            allocated[c] = min(allocated[c] + extra[c], class_sizes[c])

    # 学习注释：转为不重复元素集合；保存到picked_events。
    picked_events = set()
    # 学习注释：遍历读取by_class的键和值配对，每轮把当前项交给c、evs。
    for c, evs in by_class.items():
        # 学习注释：从allocated读取键c，缺少时取0；保存到n。
        n = allocated.get(c, 0)
        # 学习注释：将新字段合并到picked_events，同名字段会覆盖旧值。
        picked_events.update(rng.sample(evs, min(n, len(evs))))
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  抽中事件: {len(picked_events)}")

    # 4) 带出关联记录，按字段清单筛选列
    picked_user_rows = []
    # 学习注释：遍历picked_events，每轮把当前项交给ev。
    for ev in picked_events:
        # 学习注释：把从event_users读取键ev，缺少时取空列表的各元素追加到picked_user_rows。
        picked_user_rows.extend(event_users.get(ev, []))
    # 学习注释：空列表；保存到picked_seg_rows。
    picked_seg_rows = []
    # 学习注释：遍历picked_events，每轮把当前项交给ev。
    for ev in picked_events:
        # 学习注释：把从event_segs读取键ev，缺少时取空列表的各元素追加到picked_seg_rows。
        picked_seg_rows.extend(event_segs.get(ev, []))

    # 涉及到的线路编码集合（用于筛选线路台账）
    involved_lines = set()
    # 学习注释：遍历计算 picked_user_rows + picked_seg_rows，每轮把当前项交给row。
    for row in picked_user_rows + picked_seg_rows:
        # 用户/线段表里没有"线路编码"，用线路名称关联到线路台账
        ln = row.get("线路名称")
        # 学习注释：只有ln为真/非空，并且清理str(ln)首尾或指定一侧的字符才进入这一分支。
        if ln and str(ln).strip():
            # 学习注释：调用involved_lines.add，处理清理str(ln)首尾或指定一侧的字符。
            involved_lines.add(str(ln).strip())

    # 线路台账：只保留"线路名称"出现在抽样事件里的行
    picked_line_rows = [
        row for row in line_rows
        if str(row.get("线路名称", "")).strip() in involved_lines
    ]

    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  筛选后: 用户记录 {len(picked_user_rows)} 条, 线段记录 {len(picked_seg_rows)} 条, 线路 {len(picked_line_rows)} 条")

    # 5) 提取精选列
    user_proj = project_rows(picked_user_rows, USER_KEEP)
    # 学习注释：调用project_rows，处理picked_seg_rows；保存到seg_proj。
    seg_proj = project_rows(picked_seg_rows, SEG_KEEP)
    # 学习注释：调用project_rows，处理picked_line_rows；保存到line_proj。
    line_proj = project_rows(picked_line_rows, LINE_KEEP)
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"  提取列: 用户表 {len(USER_KEEP)} 列, 线段表 {len(SEG_KEEP)} 列, 线路表 {len(LINE_KEEP)} 列")

    # ============================================================
    # 阶段二：分类型脱敏
    # ============================================================
    print("【阶段二】分类型脱敏...")

    # 学习注释：函数mask_user_row接收row；把这一段可复用处理封装起来。
    def mask_user_row(row):
        # 学习注释：返回包含事件序号、供服中心、用户编码、用户名称、单位名称、线段编码的字典，结束本次函数调用。
        return {
            "事件序号": row.get("事件序号"),
            "供服中心": pseudo_supply_center.map(row.get("供服中心名称")),
            "用户编码": hash_id(str(row.get("用户编码", ""))) if row.get("用户编码") else "",
            "用户名称": pseudo_user.map(row.get("用户名称")),
            "单位名称": pseudo_org.map(row.get("单位名称")),
            "线段编码": hash_id(str(row.get("线段编码", ""))) if row.get("线段编码") else "",
            "线段名称": pseudo_segment.map(row.get("线段名称")),
            "线路名称": pseudo_line.map(row.get("线路名称")),
            "变电站名称": pseudo_station.map(row.get("变电站名称")),
            "停电起始时间": row.get("停电起始时间"),
            "停电终止时间": row.get("停电终止时间"),
            "持续时间": row.get("持续时间"),
            "电压等级": row.get("电压等级"),
            "停电性质": row.get("停电性质"),
            "停电设备名称": pseudo_device.map(row.get("停电设备名称")),
            "停电技术名称": row.get("停电技术名称"),
            "停电责任名称": row.get("停电责任名称"),
            "时户数": row.get("时户数"),
            "缺供电量": row.get("缺供电量"),
            "总容量": row.get("总容量"),
            "停电容量": row.get("停电容量"),
            "低压用户数": row.get("低压用户数"),
            "低压停电用户数": row.get("低压停电用户数"),
            "备注说明": mask_text(row.get("备注说明")),
            "修改原因": mask_text(row.get("修改原因")),
        }

    # 学习注释：函数mask_seg_row接收row；把这一段可复用处理封装起来。
    def mask_seg_row(row):
        # 学习注释：返回包含事件序号、供服中心、线段编码、线段名称、线路名称、变电站名称的字典，结束本次函数调用。
        return {
            "事件序号": row.get("事件序号"),
            "供服中心": pseudo_supply_center.map(row.get("供服中心名称")),
            "线段编码": hash_id(str(row.get("线段编码", ""))) if row.get("线段编码") else "",
            "线段名称": pseudo_segment.map(row.get("线段名称")),
            "线路名称": pseudo_line.map(row.get("线路名称")),
            "变电站名称": pseudo_station.map(row.get("变电站名称")),
            "停电起始时间": row.get("停电起始时间"),
            "停电终止时间": row.get("停电终止时间"),
            "持续时间": row.get("持续时间"),
            "电压等级": row.get("电压等级"),
            "停电性质": row.get("停电性质"),
            "停电设备名称": pseudo_device.map(row.get("停电设备名称")),
            "停电技术名称": row.get("停电技术名称"),
            "停电责任名称": row.get("停电责任名称"),
            "停电户数": row.get("停电户数"),
            "时户数": row.get("时户数"),
            "停电容量": row.get("停电容量"),
            "缺供电量": row.get("缺供电量"),
            "备注说明": mask_text(row.get("备注说明")),
            "修改原因": mask_text(row.get("修改原因")),
        }

    # 学习注释：函数mask_line_row接收row；把这一段可复用处理封装起来。
    def mask_line_row(row):
        # 学习注释：返回包含线路编码、线路名称、变电站名称、供服中心、投运日期、线路性质的字典，结束本次函数调用。
        return {
            "线路编码": hash_id(str(row.get("线路编码", ""))) if row.get("线路编码") else "",
            "线路名称": pseudo_line.map(row.get("线路名称")),
            "变电站名称": pseudo_station.map(row.get("变电站名称")),
            "供服中心": pseudo_supply_center.map(row.get("供服中心名称")),
            "投运日期": row.get("投运日期"),
            "线路性质": row.get("线路性质"),
            "供电区域": row.get("供电区域"),
            "线路长度_km": row.get("线路长度_km"),
            "总用户数": row.get("总用户数"),
        }

    # 学习注释：遍历user_proj，将mask_user_row(r)收集为列表；保存到user_masked。
    user_masked = [mask_user_row(r) for r in user_proj]
    # 学习注释：遍历seg_proj，将mask_seg_row(r)收集为列表；保存到seg_masked。
    seg_masked = [mask_seg_row(r) for r in seg_proj]
    # 学习注释：遍历line_proj，将mask_line_row(r)收集为列表；保存到line_masked。
    line_masked = [mask_line_row(r) for r in line_proj]

    # ============================================================
    # 输出
    # ============================================================
    # 学习注释：函数write_csv接收name、查询或表格行（rows）；把这一段可复用处理封装起来。
    def write_csv(name, rows):
        # 学习注释：计算 out_dir / name；保存到path。
        path = out_dir / name
        # 学习注释：只有查询或表格行（rows）为空或为False才进入这一分支。
        if not rows:
            # 学习注释：在命令行输出本步骤结果或进度。
            print(f"  警告: {name} 无数据，跳过")
            # 学习注释：提前结束当前函数，不再执行后面的步骤。
            return
        # 学习注释：转为可逐项访问的列表：读取rows[0]的全部键；保存到fieldnames。
        fieldnames = list(rows[0].keys())
        # 学习注释：按指定读写模式打开文件；with结束时调用退出逻辑，关闭资源或恢复上下文。
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            # 学习注释：调用csv.DictWriter，处理f；保存到w。
            w = csv.DictWriter(f, fieldnames=fieldnames)
            # 学习注释：调用w.writeheader。
            w.writeheader()
            # 学习注释：调用w.writerows，处理查询或表格行（rows）。
            w.writerows(rows)

    # 学习注释：调用write_csv，处理'users_masked.csv'。
    write_csv("users_masked.csv", user_masked)
    # 学习注释：调用write_csv，处理'segments_masked.csv'。
    write_csv("segments_masked.csv", seg_masked)
    # 学习注释：调用write_csv，处理'lines_masked.csv'。
    write_csv("lines_masked.csv", line_masked)

    # 事件清单
    events_out = []
    # 学习注释：遍历picked_events，每轮把当前项交给ev。
    for ev in picked_events:
        # 学习注释：向events_out末尾添加包含事件序号、标签、大类、用户记录数、线段记录数的字典。
        events_out.append({
            "事件序号": ev,
            "标签": event_tag[ev],
            "大类": big_class(event_tag[ev]),
            "用户记录数": len(event_users.get(ev, [])),
            "线段记录数": len(event_segs.get(ev, [])),
        })
    # 学习注释：调用write_csv，处理'events_sample.csv'。
    write_csv("events_sample.csv", events_out)

    # 抽样报告
    report = []
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"抽样事件总数: {len(picked_events)}")
    # 学习注释：向待保存的验收报告（report）末尾添加''。
    report.append("")
    # 学习注释：向待保存的验收报告（report）末尾添加'标签大类分布:'。
    report.append("标签大类分布:")
    # 学习注释：遍历调用Counter((big_class(event_tag[ev]) for ev in picked_events)).most_common，每轮把当前项交给c、cnt。
    for c, cnt in Counter(big_class(event_tag[ev]) for ev in picked_events).most_common():
        # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
        report.append(f"  {c}: {cnt}")
    # 学习注释：向待保存的验收报告（report）末尾添加''。
    report.append("")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"脱敏后用户记录数: {len(user_masked)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"脱敏后线段记录数: {len(seg_masked)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"脱敏后线路台账数: {len(line_masked)}")
    # 学习注释：向待保存的验收报告（report）末尾添加''。
    report.append("")
    # 学习注释：向待保存的验收报告（report）末尾添加'关联记录保留(演示去重/防JOIN放大):'。
    report.append("关联记录保留(演示去重/防JOIN放大):")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  多用户记录事件数: {sum(1 for ev in picked_events if len(event_users.get(ev, [])) > 1)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  多线段记录事件数: {sum(1 for ev in picked_events if len(event_segs.get(ev, [])) > 1)}")
    # 学习注释：向待保存的验收报告（report）末尾添加''。
    report.append("")
    # 学习注释：向待保存的验收报告（report）末尾添加'假名映射规模(跨表一致):'。
    report.append("假名映射规模(跨表一致):")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  供服中心: {len(pseudo_supply_center.mapping)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  线路: {len(pseudo_line.mapping)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  线段: {len(pseudo_segment.mapping)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  设备: {len(pseudo_device.mapping)}")
    # 学习注释：向待保存的验收报告（report）末尾添加把花括号内的变量填入f字符串，生成文字。
    report.append(f"  变电站: {len(pseudo_station.mapping)}")

    # 学习注释：将内容写入指定路径；已存在的目标会被覆盖。
    (out_dir / "sampling_report.txt").write_text("\n".join(report), encoding="utf-8")

    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n=== 抽样与脱敏报告 ===")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("\n".join(report))
    # 学习注释：在命令行输出本步骤结果或进度。
    print(f"\n输出目录: {out_dir}")
    # 学习注释：在命令行输出本步骤结果或进度。
    print("完成。")


# 学习注释：仅在直接运行此脚本时执行入口；被其他模块import时不自动执行下面的演示/命令。
if __name__ == "__main__":
    # 学习注释：调用main。
    main()
