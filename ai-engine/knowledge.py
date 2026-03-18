"""
ai-engine / knowledge.py
RAG Knowledge Base: ChromaDB + Ollama embeddings (nomic-embed-text).
Хранит одобренные Q&A пары, ищет похожие по косинусному сходству.
"""
import logging
import os
from pathlib import Path

import aiohttp
import chromadb
from chromadb.config import Settings

log = logging.getLogger(__name__)

OLLAMA_URL     = os.environ.get("OLLAMA_URL",    "http://10.0.0.2:11434")
EMBED_MODEL    = os.environ.get("EMBED_MODEL",   "nomic-embed-text")
CHROMA_PATH    = os.environ.get("CHROMA_PATH",   "/opt/ai-engine/chroma_db")
COLLECTION     = "gotruck_kb"
SIM_THRESHOLD  = 0.82   # минимальное сходство для релевантного ответа
MAX_RESULTS    = 3


# ─── Embeddings ───────────────────────────────────────────────────────────────

async def embed(text: str) -> list[float]:
    """Получить эмбеддинг через Ollama nomic-embed-text."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        r = await s.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        data = await r.json()
    return data["embedding"]


# ─── ChromaDB client ──────────────────────────────────────────────────────────

def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


# ─── Public API ───────────────────────────────────────────────────────────────

async def kb_add(entry_id: str, question: str, answer: str, meta: dict | None = None) -> None:
    """
    Добавить/обновить Q&A пару в базу знаний.
    Если entry_id уже существует — перезаписывает.
    """
    vec = await embed(question)
    col = _get_collection()

    # upsert: если id существует — обновляем
    existing = col.get(ids=[entry_id])
    if existing["ids"]:
        col.update(
            ids=[entry_id],
            embeddings=[vec],
            documents=[question],
            metadatas=[{**(meta or {}), "answer": answer}],
        )
        log.info("KB updated: %s", entry_id)
    else:
        col.add(
            ids=[entry_id],
            embeddings=[vec],
            documents=[question],
            metadatas=[{**(meta or {}), "answer": answer}],
        )
        log.info("KB added: %s", entry_id)


async def kb_search(query: str, n: int = MAX_RESULTS) -> list[dict]:
    """
    Найти похожие Q&A. Возвращает список:
    [{"question": str, "answer": str, "score": float, "id": str}]
    """
    try:
        vec = await embed(query)
        col = _get_collection()
        count = col.count()
        if count == 0:
            return []
        results = col.query(
            query_embeddings=[vec],
            n_results=min(n, count),
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = 1.0 - dist   # cosine distance → similarity
            if score >= SIM_THRESHOLD:
                out.append({
                    "id":       results["ids"][0][len(out)],
                    "question": doc,
                    "answer":   meta.get("answer", ""),
                    "score":    round(score, 3),
                })
        return out
    except Exception as e:
        log.error("kb_search error: %s", e)
        return []


async def kb_delete(entry_id: str) -> bool:
    try:
        col = _get_collection()
        col.delete(ids=[entry_id])
        return True
    except Exception:
        return False


def kb_count() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0


async def kb_list(limit: int = 20) -> list[dict]:
    """Список всех записей (для /review)."""
    try:
        col = _get_collection()
        results = col.get(limit=limit, include=["documents", "metadatas"])
        return [
            {"id": i, "question": d, "answer": m.get("answer", ""), "source": m.get("source", "manual")}
            for i, d, m in zip(results["ids"], results["documents"], results["metadatas"])
        ]
    except Exception as e:
        log.error("kb_list error: %s", e)
        return []
