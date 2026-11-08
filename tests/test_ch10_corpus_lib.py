"""classifier 语料纯函数:脱敏 / 去重 / 分层划分。"""
from scripts.classifier.corpus_lib import dedupe, desensitize, split_dataset


def test_desensitize_masks_sensitive_keeps_model_no():
    assert desensitize("我手机13812345678帮我查下") == "我手机[手机号]帮我查下"
    assert desensitize("工单202601180001234567还没发") == "工单[单号]还没发"
    assert desensitize("加我微信 cat_lover2026 聊") == "加我微信[账号]聊"
    # 业务型号不许误伤
    assert "MH-LP100" in desensitize("MH-LP100 电表箱废砂盒多久倒一次")


def test_dedupe_keeps_first():
    out = dedupe([{"text": "a", "origin": "pool"}, {"text": "a", "origin": "simulated"},
                  {"text": "b", "origin": "pool"}])
    assert [s["text"] for s in out] == ["a", "b"]
    assert out[0]["origin"] == "pool"


def _make_samples():
    samples = []
    for cls, n in (("退费退补", 40), ("停电报修", 40), ("计量电表", 30)):
        samples += [{"text": f"{cls}问题{i}", "labels": [cls]} for i in range(n)]
    samples += [{"text": f"多诉求{i}", "labels": ["计量电表", "退费退补"]} for i in range(12)]
    samples += [{"text": f"小组合{i}", "labels": ["停电报修", "退费退补"]} for i in range(3)]
    return samples


def test_split_ratio_and_no_leakage():
    train, val, test = split_dataset(_make_samples())
    total = len(train) + len(val) + len(test)
    assert total == 125
    assert 0.72 <= len(train) / total <= 0.88
    texts = [s["text"] for s in train + val + test]
    assert len(texts) == len(set(texts))


def test_split_every_class_in_every_split():
    train, val, test = split_dataset(_make_samples())
    for split in (train, val, test):
        found = {lb for s in split for lb in s["labels"]}
        assert {"退费退补", "停电报修", "计量电表"} <= found


def test_split_deterministic():
    a = split_dataset(_make_samples(), seed=42)
    b = split_dataset(_make_samples(), seed=42)
    assert a == b
