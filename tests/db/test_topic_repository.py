"""classifier 地基:topic_classifications ORM 与 repository 归类方法。"""
from app.db import repository


async def test_insert_and_exclude_classified(db_session_factory):
    qid = await repository.insert_low_confidence(None, "表箱买大了想退", "retrieval_low_conf", None)
    rid = await repository.insert_review_item("表箱买大了能不能退费", None)
    await repository.set_matched_review(qid, rid)
    rows = await repository.list_unclassified_questions()
    assert any(r["question_id"] == qid for r in rows)

    n = await repository.insert_topic_classifications(
        [{"question_id": qid, "labels": ["计量电表", "退费退补"]}])
    assert n == 1
    rows = await repository.list_unclassified_questions()
    assert not any(r["question_id"] == qid for r in rows)


async def test_unmerged_excluded_from_classify(db_session_factory):
    """未归并(无 matched_review_id)的问题不进分类旁路——只归归并过的。"""
    qid = await repository.insert_low_confidence(None, "旧电表回收吗", "retrieval_low_conf", None)
    rows = await repository.list_unclassified_questions()
    assert not any(r["question_id"] == qid for r in rows)


async def test_text_prefers_normalized(db_session_factory):
    qid = await repository.insert_low_confidence(None, "这个还能退吗", "retrieval_low_conf", None)
    rid = await repository.insert_review_item("智能电能表能否七天无理由退费", None)
    await repository.set_matched_review(qid, rid)
    rows = await repository.list_pool_texts()
    row = next(r for r in rows if r["question_id"] == qid)
    assert row["text"] == "智能电能表能否七天无理由退费"


async def test_topic_distribution_counts_all_17(db_session_factory):
    qid = await repository.insert_low_confidence(None, "表箱买大了想退", "retrieval_low_conf", None)
    await repository.insert_topic_classifications(
        [{"question_id": qid, "labels": ["计量电表", "退费退补"]}])
    dist = await repository.topic_distribution()
    assert dist["total"] == 1
    assert len(dist["classes"]) == 17
    by_label = {c["label"]: c for c in dist["classes"]}
    assert by_label["计量电表"]["count"] == 1 and by_label["退费退补"]["count"] == 1
    assert by_label["停电报修"]["count"] == 0
    assert by_label["计量电表"]["samples"] == ["表箱买大了想退"]
    assert dist["latest"] is not None
