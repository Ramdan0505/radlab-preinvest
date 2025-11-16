from typing import List, Dict, Any

from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings

# Simple global singleton – fine for this use
_model = SentenceTransformer("all-MiniLM-L6-v2")

_client = chromadb.Client(
    Settings(
        chroma_api_impl="rest",
        chroma_server_host="chroma",
        chroma_server_http_port=8000,
    )
)


def _get_collection(case_id: str):
    return _client.get_or_create_collection(
        name=f"case_{case_id}",
        metadata={"hnsw:space": "cosine"},
    )


def embed_texts(case_id: str, texts: List[str], metadata_list: List[Dict[str, Any]]):
    """Index a batch of texts for a single case."""
    if not texts:
        return

    coll = _get_collection(case_id)
    embeddings = _model.encode(texts).tolist()
    ids = [f"{case_id}_{i}" for i in range(len(texts))]

    coll.add(
        ids=ids,
        documents=texts,
        metadatas=metadata_list,
        embeddings=embeddings,
    )


def semantic_search(case_id: str, query: str, top_k: int = 5) -> Dict[str, Any]:
    """Query a case's collection semantically."""
    coll = _get_collection(case_id)
    q_emb = _model.encode(query).tolist()
    res = coll.query(query_embeddings=[q_emb], n_results=top_k)

    # Normalize into a cleaner response
    hits = []
    for i in range(len(res["ids"][0])):
        hits.append(
            {
                "id": res["ids"][0][i],
                "score": res["distances"][0][i],
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
            }
        )
    return {"results": hits}
