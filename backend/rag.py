import hashlib
import json
import os
from functools import lru_cache

from openai import OpenAI

from audit import audit_log
from db import get_db, serialize_vector

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.75"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
CLUSTER_THRESHOLD = 0.85  # tighter than RAG hit — only cluster near-duplicates


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _normalize_embedding_text(text: str) -> str:
    """
    Normalize text before hashing.

    This makes these inputs share one cache entry:
    - "สวัสดีครับ"
    - "  สวัสดีครับ  "
    - "สวัสดีครับ\n"
    """
    return " ".join((text or "").strip().lower().split())


def _embedding_hash(text: str, model: str, dimensions: int) -> str:
    """
    Hash normalized text together with model and dimensions.

    We include model/dimensions because the same text can produce different vectors
    if model or dimensions change.
    """
    normalized = _normalize_embedding_text(text)
    raw = f"{model}:{dimensions}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cached_embedding(text: str) -> list[float] | None:
    text_hash = _embedding_hash(text, EMBEDDING_MODEL, EMBEDDING_DIM)

    conn = get_db()
    row = conn.execute(
        """
        SELECT embedding
        FROM embedding_cache
        WHERE text_hash = ?
          AND model = ?
          AND dimensions = ?
        """,
        (text_hash, EMBEDDING_MODEL, EMBEDDING_DIM),
    ).fetchone()

    if not row:
        return None

    try:
        vector = json.loads(row["embedding"])
    except Exception:
        return None

    conn.execute(
        """
        UPDATE embedding_cache
        SET hit_count = hit_count + 1,
            last_used_at = datetime('now')
        WHERE text_hash = ?
        """,
        (text_hash,),
    )
    conn.commit()

    audit_log(
        "embedding_cache_hit",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
        },
    )

    return vector


def _save_cached_embedding(text: str, vector: list[float]) -> None:
    text_hash = _embedding_hash(text, EMBEDDING_MODEL, EMBEDDING_DIM)
    normalized = _normalize_embedding_text(text)

    conn = get_db()
    conn.execute(
        """
        INSERT OR REPLACE INTO embedding_cache
        (text_hash, text, embedding, model, dimensions, created_at, last_used_at, hit_count)
        VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 0)
        """,
        (
            text_hash,
            normalized,
            json.dumps(vector),
            EMBEDDING_MODEL,
            EMBEDDING_DIM,
        ),
    )
    conn.commit()

    audit_log(
        "embedding_cache_saved",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
        },
    )


def embed(text: str) -> list[float]:
    """
    Return embedding vector for text.

    Uses local SQLite cache first to reduce OpenAI API calls and cost.
    """
    cached = _get_cached_embedding(text)
    if cached is not None:
        return cached

    audit_log(
        "embedding_cache_miss",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
        },
    )

    res = _client().embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        dimensions=EMBEDDING_DIM,
    )

    vector = res.data[0].embedding
    _save_cached_embedding(text, vector)
    return vector


def _distance_to_similarity(distance: float) -> float:
    # sqlite-vec default distance is L2 on normalized vectors; convert to cosine sim.
    return 1.0 - (distance * distance) / 2.0


def search_knowledge(question_vec: list[float], k: int = 1) -> dict | None:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT k.id, k.question, k.answer, kv.distance
        FROM knowledge_vec kv
        JOIN knowledge k ON k.id = kv.knowledge_id
        WHERE kv.embedding MATCH ? AND k = ?
        ORDER BY kv.distance
        """,
        (serialize_vector(question_vec), k),
    ).fetchall()

    if not rows:
        return None

    top = rows[0]
    sim = _distance_to_similarity(top["distance"])

    if sim < SIMILARITY_THRESHOLD:
        return None

    conn.execute(
        "UPDATE knowledge SET hit_count = hit_count + 1 WHERE id = ?",
        (top["id"],),
    )
    conn.commit()

    return {
        "id": top["id"],
        "question": top["question"],
        "answer": top["answer"],
        "similarity": sim,
    }


def log_pending_question(question: str, question_vec: list[float]) -> int:
    """Save question; merge into existing cluster if very similar."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT pv.pending_id, pv.distance
        FROM pending_vec pv
        JOIN pending_questions p ON p.id = pv.pending_id
        WHERE pv.embedding MATCH ? AND k = 1 AND p.status = 'pending'
        ORDER BY pv.distance
        """,
        (serialize_vector(question_vec),),
    ).fetchall()

    if rows and _distance_to_similarity(rows[0]["distance"]) >= CLUSTER_THRESHOLD:
        pid = rows[0]["pending_id"]
        conn.execute(
            """
            UPDATE pending_questions
            SET ask_count = ask_count + 1,
                last_asked_at = datetime('now')
            WHERE id = ?
            """,
            (pid,),
        )
        conn.commit()
        return pid

    cur = conn.execute(
        "INSERT INTO pending_questions (question) VALUES (?)",
        (question,),
    )
    pid = cur.lastrowid

    conn.execute(
        "INSERT INTO pending_vec (pending_id, embedding) VALUES (?, ?)",
        (pid, serialize_vector(question_vec)),
    )
    conn.commit()

    return pid


def add_knowledge(
    question: str,
    answer: str,
    approved_by: int | None,
    source: str = "admin",
) -> int:
    conn = get_db()
    vec = embed(question)

    cur = conn.execute(
        """
        INSERT INTO knowledge (question, answer, approved_by, source)
        VALUES (?, ?, ?, ?)
        """,
        (question, answer, approved_by, source),
    )
    kid = cur.lastrowid

    conn.execute(
        "INSERT INTO knowledge_vec (knowledge_id, embedding) VALUES (?, ?)",
        (kid, serialize_vector(vec)),
    )
    conn.commit()

    return kid


def resolve_pending(pending_id: int, answer: str, approved_by: int) -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT question FROM pending_questions WHERE id = ?",
        (pending_id,),
    ).fetchone()

    if not row:
        raise ValueError("pending question not found")

    kid = add_knowledge(row["question"], answer, approved_by)

    conn.execute(
        "UPDATE pending_questions SET status = 'answered' WHERE id = ?",
        (pending_id,),
    )
    conn.execute(
        "DELETE FROM pending_vec WHERE pending_id = ?",
        (pending_id,),
    )
    conn.commit()

    return kid