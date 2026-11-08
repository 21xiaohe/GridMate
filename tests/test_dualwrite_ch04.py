import uuid

import pytest

from app.kb import dualwrite, milvus_client as mc
from app.kb.documents import Chunk

URI = "http://localhost:19530"


async def test_vectorize_writes_bm25_and_metadata(db_session_factory, monkeypatch):
    # 嵌入 mock 成定长向量
    async def fake_embed(texts):
        return [[0.05] * 1024 for _ in texts]
    monkeypatch.setattr("app.kb.dualwrite.embeddings.embed_texts", fake_embed)

    chunks = [Chunk(category="违约金", questions="违约金怎么算", answer="满99缴费 型号无关",
                    section_path="违约金政策", content_type="faq", is_key_clause=1)]
    await dualwrite.write_pending(chunks)

    client = mc.get_client(uri=URI)
    name = f"test_kb_{uuid.uuid4().hex[:8]}"
    mc.ensure_collection(client, collection=name)
    done = await dualwrite.vectorize_pending(client, collection=name)
    client.load_collection(name)
    import time; time.sleep(2)
    assert done == 1
    hits = mc.bm25_search(client, "违约金", top_k=1, collection=name)
    assert hits and hits[0]["section_path"] == "违约金政策" and hits[0]["category"] == "违约金"
    mc.drop(client, name)
