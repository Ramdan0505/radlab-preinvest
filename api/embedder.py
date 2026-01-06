# api/embedder.py
from typing import List, Dict, Any
import os
import uuid

import chromadb
from sentence_transformers import SentenceTransformer

# Use a single model for BOTH indexing and querying
_model = SentenceTransformer("all-MiniLM-L6-v2")

CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma")  # docker-compose service name
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

# Cosine distance threshold: 0 = identical, ~1 = very far
# Tune this. Good starting point: 0.60–0.75
SEARCH_MAX_DISTANCE = float(os.getenv("SEARCH_MAX_DISTANCE", "0.70"))


def _make_client():
    """
    Prefer Chroma server, but allow fallback to local PersistentClient if server fails.
    NOTE: If you see fallback logs, you're NOT using the chroma container.
    """
    try:
        return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    except Exception as e:
        print(f"[embedder] HttpClient failed ({e}); falling back to PersistentClient('/data').")
        return chromadb.PersistentClient(path="/data")


_client = _make_client()


def _get_collection(case_id: str):
    return _client.get_or_create_collection(
        name=f"case_{case_id}",
        metadata={"hnsw:space": "cosine"},
    )


def embed_texts(case_id: str, texts: List[str], metadata_list: List[Dict[str, Any]]) -> None:
    if not texts:
        return
    if len(texts) != len(metadata_list):
        raise ValueError("texts and metadata_list must have same length")

    coll = _get_collection(case_id)

    # Normalize vectors so cosine distances behave correctly
    embeddings = _model.encode(texts, normalize_embeddings=True).tolist()

    # Unique IDs avoid collisions across multiple ingests/reindexes
    ids = [f"{case_id}_{uuid.uuid4().hex}" for _ in texts]

    coll.add(
        ids=ids,
        documents=texts,
        metadatas=metadata_list,
        embeddings=embeddings,
    )


def semantic_search(case_id: str, query: str, top_k: int = 5) -> Dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"results": []}

    coll = _get_collection(case_id)

    q_emb = _model.encode([query], normalize_embeddings=True)[0].tolist()

    res = coll.query(
        query_embeddings=[q_emb],
        n_results=top_k,
        include=["documents", "metadatas", "distances", "ids"],
    )

    hits = []
    ids0 = (res.get("ids") or [[]])[0]
    dists0 = (res.get("distances") or [[]])[0]
    docs0 = (res.get("documents") or [[]])[0]
    metas0 = (res.get("metadatas") or [[]])[0]

    for i in range(len(ids0)):
        dist = dists0[i]
        if dist is None:
            continue
        # KEY FIX: filter irrelevant results
        if dist > SEARCH_MAX_DISTANCE:
            continue

        hits.append(
            {
                "id": ids0[i],
                "distance": dist,
                "text": docs0[i],
                "metadata": metas0[i],
            }
        )

    return {"results": hits}
