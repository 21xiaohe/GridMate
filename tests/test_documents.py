from app.kb.documents import Chunk, build_chunks


def test_policy_maps_heading_and_parent_path():
    md = "# 用电业务政策\n\n## 违约金说明\n\n单笔工单满99元缴费,未满收取10元违约金。偏远地区另计。"
    chunks = build_chunks(md, content_type="policy")
    c = [c for c in chunks if c.questions == "违约金说明"][0]
    assert c.category == "用电业务政策"
    assert c.section_path == "用电业务政策 / 违约金说明"
    assert "满99" in c.answer
    assert c.content_type == "policy"
    assert c.is_key_clause == 1  # 含「违约金」关键条款


def test_table_section_splits_by_rows_with_header():
    rows = "\n".join(f"| 业务{i} | {i} |" for i in range(1, 15))
    md = f"# 业务价格表\n\n## 价目\n\n| 业务 | 价格 |\n| --- | --- |\n{rows}"
    chunks = build_chunks(md, content_type="manual", table_max_rows=5)
    price = [c for c in chunks if c.questions == "价目"]
    assert len(price) >= 2  # 14 行按 5 切成 >=3 块
    for c in price:
        assert c.answer.startswith("| 业务 | 价格 |")  # 每块复制表头


def test_returns_chunk_dataclass():
    chunks = build_chunks("# A\n\n## B\n\n正文。", content_type="faq")
    assert isinstance(chunks[0], Chunk)
