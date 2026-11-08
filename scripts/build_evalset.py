"""评估集生成器:从 data/kb/*.md 的真实小节生成 300 题五桶评估集。
为什么用脚本生成而不是手写:expect_points 是机械子串匹配(见 validate_evalset.py),
要点必须逐字来自知识库正文——从解析结果里直接抽句,天然保证对得上,不靠人眼誊抄。
五桶:A_policy 政策问答 / B_model 型号精确词 / C_colloquial 口语化 / E_multi 跨文档 / D_absent 不可答。
运行:uv run python scripts/build_evalset.py(改完知识库后重跑,再 make eval-check 自检)。"""
import json
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_KB = _ROOT / "data" / "kb"
_OUT = _ROOT / "tests" / "data" / "eval_hybrid_strategies.jsonl"

POLICY_DOCS = {"refund-policy.md", "tariff-policy.md", "billing-payment.md", "service-manual.md"}


def parse_sections(md: str) -> list[dict]:
    """按标题层级切成叶子小节:返回 {title, path, body, doc}。与 chunking.split_sections 同思路。"""
    out, stack = [], {}
    body_lines: list[str] = []
    cur: dict | None = None

    def flush():
        nonlocal cur, body_lines
        if cur and "\n".join(body_lines).strip():
            out.append({**cur, "body": "\n".join(body_lines).strip()})
        body_lines = []

    for line in md.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2)
            stack = {k: v for k, v in stack.items() if k < level}
            stack[level] = text
            path = [stack[k] for k in sorted(stack)]
            cur = {"title": text, "path": path, "doc": None}
        elif cur is not None:
            body_lines.append(line)
    flush()
    return out


def sentences(body: str) -> list[str]:
    """按句号切句,取非空句(评估要点从句子里抽,保证逐字匹配)。"""
    body = re.sub(r"\s+", "", body)
    return [s for s in re.split(r"(?<=[。!?])", body) if len(s) >= 8]


def label_of(sec: dict) -> str:
    """查询用的自然称呼:h3 用「父标题的+小节名」,h2 直接用标题。"""
    return sec["path"][-1] if len(sec["path"]) <= 1 else f"{sec['path'][-2]}的{sec['path'][-1]}"


def main() -> None:
    secs: list[dict] = []
    for f in sorted(_KB.glob("*.md")):
        for s in parse_sections(f.read_text(encoding="utf-8")):
            s["doc"] = f.name
            secs.append(s)
    assert len(secs) >= 40, f"知识库小节过少({len(secs)}),先检查 kb 文件"

    policy = [s for s in secs if s["doc"] in POLICY_DOCS]
    specs = [s for s in secs if s["doc"] == "meter-specs.md"]
    rows: list[dict] = []

    # ---- A_policy:政策问答,每节两问(直问 + 要点问) ----
    i = 0
    for s in [x for x in policy if sentences(x["body"])] * 3:
        if len(rows) >= 60:
            break
        t, sents = label_of(s), sentences(s["body"])
        tpl = (f"{t}有哪些规定", f"{t}的具体规则是什么", f"办理时{t}要注意什么")[i % 3]
        rows.append({"id": f"A{i+1}", "bucket": "A_policy", "query": tpl,
                     "expect_section": [s["title"]],
                     "expect_points": sents[:2], "should_refuse": False})
        i += 1

    # ---- B_model:型号精确词,电表/互感器各角度问 ----
    i = 0
    codes = [s for s in specs if re.search(r"[A-Z]{3,}", s["title"])]
    plain = [s for s in specs if s not in codes]
    for s in codes:
        if len(rows) >= 120:
            break
        code = re.search(r"[A-Z][A-Z0-9\-]+", s["title"]).group(0)
        sents = sentences(s["body"])
        for tpl in (f"{code} 的参数是多少", f"{code} 什么规格", f"帮我查下 {code} 这个型号",
                    f"{code} 用在什么场景", f"{code} 准确度等级是多少",
                    f"{code} 大概多少钱", f"{code} 的应用范围", f"{code} 是单相还是三相"):
            if len(rows) >= 120:
                break
            rows.append({"id": f"B{i+1}", "bucket": "B_model", "query": tpl,
                         "expect_section": [s["title"]],
                         "expect_points": sents[:2], "should_refuse": False})
            i += 1
    for s in [x for x in plain if sentences(x['body'])] * 3:
        if len(rows) >= 120:
            break
        t, sents = label_of(s), sentences(s["body"])
        tpl = (f"{t}有什么标准", f"{t}怎么规定的", f"关于{t}的要求")[i % 3]
        rows.append({"id": f"B{i+1}", "bucket": "B_model", "query": tpl,
                     "expect_section": [s["title"]],
                     "expect_points": sents[:2], "should_refuse": False})
        i += 1

    # ---- C_colloquial:口语化改写,句子照样能命中 ----
    collo = ["我想问下{}", "{}咋办理的啊", "问一下哈,{}", "{}是啥意思呀",
             "麻烦说下{}", "{}这种事找谁问", "我不太懂{},给讲讲", "{}咋回事啊"]
    i = 0
    used: set[str] = set()
    for s in [x for x in secs if sentences(x["body"])] * 3:
        if len(rows) >= 180:
            break
        sents = sentences(s["body"])
        t = s["title"] if s["title"] not in used else label_of(s)
        q = collo[i % len(collo)].format(t)
        if q in used:
            continue
        used.add(q)
        rows.append({"id": f"C{i+1}", "bucket": "C_colloquial", "query": q,
                     "expect_section": [s["title"]],
                     "expect_points": sents[:2], "should_refuse": False})
        i += 1

    # ---- E_multi:跨文档双证据,两组都必须命中 ----
    by_doc: dict[str, list[dict]] = {}
    for s in secs:
        by_doc.setdefault(s["doc"], []).append(s)
    docs = sorted(by_doc)
    i, used_e = 0, set()
    for a in range(len(docs)):
        for b in range(a + 1, len(docs)):
            for s1, s2 in zip([x for x in by_doc[docs[a]] if sentences(x['body'])] * 2,
                              [x for x in by_doc[docs[b]] if sentences(x['body'])] * 2):
                if len(rows) >= 240:
                    break
                t1, t2 = s1["title"], s2["title"]
                p1, p2 = sentences(s1["body"])[:1], sentences(s2["body"])[:1]
                if not p1 or not p2:
                    continue
                tpl = (f"{t1}和{t2}分别是什么规定", f"帮我对比下{t1}和{t2}")[(i // 20) % 2]
                if tpl in used_e:
                    continue
                used_e.add(tpl)
                rows.append({"id": f"E{i+1}", "bucket": "E_multi", "query": tpl,
                             "expect_section": [s1["title"], s2["title"]],
                             "expect_sections_all": [[s1["title"]], [s2["title"]]],
                             "expect_points": p1 + p2, "should_refuse": False})
                i += 1
                if i >= 60:
                    break
            if i >= 60:
                break
        if i >= 60:
            break

    # ---- D_absent:知识库里没有的业务,一律应拒答 ----
    absent_topics = ["光伏并网发电", "充电桩报装", "储能电池并网", "变压器维修", "输电线路迁改",
                     "变电站选址", "售电公司注册", "电力交易入市", "高压线辐射", "电缆沟施工",
                     "发电机组检修", "电力资质考试", "电价听证会", "配电网规划", "电力环保验收",
                     "风力发电并网", "微电网建设", "电力设备招标", "输变电工程监理", "电力调度值班"]
    ask = ["{}怎么申请", "{}的办理条件是什么", "{}找哪个部门"]
    i = 0
    for t in absent_topics:
        for q in ask:
            rows.append({"id": f"D{i+1}", "bucket": "D_absent", "query": q.format(t),
                         "expect_section": [], "expect_points": [], "should_refuse": True})
            i += 1

    assert len(rows) == 300 and all(
        sum(1 for r in rows if r["bucket"] == b) == 60
        for b in ("A_policy", "B_model", "C_colloquial", "E_multi", "D_absent")), f"题数不对:{len(rows)}"

    _OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"✅ 评估集 300 题已生成 → {_OUT}(五桶各 60)")


if __name__ == "__main__":
    main()
