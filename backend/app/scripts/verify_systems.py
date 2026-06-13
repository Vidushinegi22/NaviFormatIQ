"""Subsystem verification with all services live.

    python -m app.scripts.verify_systems
"""
from __future__ import annotations

import sys
import uuid


def check(name, fn) -> bool:
    try:
        msg = fn()
        print(f"  PASS  {name}: {msg}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {e!r}")
        return False


def main() -> int:
    from app.core.config import get_settings

    s = get_settings()
    results: list[bool] = []

    def cfg():
        assert s.azure_openai_configured(), "azure chat not configured"
        assert s.azure_embeddings_configured(), "azure embeddings not configured"
        assert s.qdrant_configured(), "qdrant not configured"
        assert s.r2_configured(), "r2 not configured"
        assert s.effective_storage_backend() == "r2", s.effective_storage_backend()
        return (
            f"storage=r2 emb_dim={s.embedding_dim} chat={s.azure_openai_deployment} "
            f"emb={s.azure_openai_embedding_deployment}"
        )

    results.append(check("config flags", cfg))

    def r2():
        from app.storage import get_storage

        st = get_storage()
        assert type(st).__name__ == "R2Storage", type(st).__name__
        key = f"_verify/{uuid.uuid4().hex}.txt"
        payload = b"navispark r2 roundtrip"
        obj = st.put(payload, key=key, content_type="text/plain")
        assert st.get(obj.uri) == payload, "roundtrip mismatch"
        url = st.presign_get(obj.uri, expires=120)
        assert url and url.startswith("https://"), "presign failed"
        try:
            st.client().delete_object(Bucket=st.bucket, Key=key)
        except Exception:
            pass
        return f"put/get/presign ok ({obj.uri[:46]}...)"

    results.append(check("R2 storage", r2))

    def emb():
        from app.rag.embedder import embed_sync

        v = embed_sync(["hello world", "second text"])
        assert len(v) == 2, "batch size"
        assert len(v[0]) == s.embedding_dim, f"dim {len(v[0])} != {s.embedding_dim}"
        return f"{len(v)} vectors, dim={len(v[0])}"

    results.append(check("embeddings (Azure)", emb))

    def qd():
        from app.rag.embedder import embed_sync
        from app.vectorstore.base import VectorRecord
        from app.vectorstore.factory import domain_collection, get_sync_vectorstore

        vs = get_sync_vectorstore()
        assert type(vs).__name__ == "QdrantVectorStore", type(vs).__name__
        coll = domain_collection("_verify")
        texts = ["the cat sat on the mat", "financial risk disclosure statement"]
        vecs = embed_sync(texts)
        vs.upsert_sync(
            collection=coll,
            records=[
                VectorRecord(id=f"v{i}", vector=vecs[i], text=texts[i], metadata={"chunk_id": f"v{i}"})
                for i in range(len(texts))
            ],
        )
        hits = vs.search_sync(collection=coll, query_vector=embed_sync(["risk"])[0], k=2)
        assert hits, "no hits"
        try:
            vs.client.delete_collection(coll)
        except Exception:
            pass
        return f"{len(hits)} hits, top='{hits[0].text[:32]}' score={hits[0].score:.3f}"

    results.append(check("Qdrant vector store", qd))

    def llm():
        from app.llm.adapters import chat_text

        out = chat_text("You are terse.", "Reply with the single word: OK")
        assert out, "no LLM response"
        return f"chat -> '{out[:40]}'"

    results.append(check("Azure chat (gpt-5-chat)", llm))

    print(f"\n{sum(results)}/{len(results)} subsystems passing")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
