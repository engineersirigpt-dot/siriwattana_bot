import hashlib
import json
import os
from functools import lru_cache

from openai import OpenAI
from pgvector import Vector

from audit import audit_log
from db_pg import get_pg_conn

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.75"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
CLUSTER_THRESHOLD = 0.85


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _normalize_embedding_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _embedding_hash(text: str, model: str, dimensions: int) -> str:
    normalized = _normalize_embedding_text(text)
    raw = f"{model}:{dimensions}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cached_embedding_pg(text: str) -> list[float] | None:
    text_hash = _embedding_hash(text, EMBEDDING_MODEL, EMBEDDING_DIM)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT embedding
                FROM embedding_cache
                WHERE text_hash = %s
                  AND model = %s
                  AND dimensions = %s
                """,
                (text_hash, EMBEDDING_MODEL, EMBEDDING_DIM),
            )
            row = cur.fetchone()

            if not row:
                return None

            try:
                vector = json.loads(row[0])
            except Exception:
                return None

            cur.execute(
                """
                UPDATE embedding_cache
                SET hit_count = hit_count + 1,
                    last_used_at = now()
                WHERE text_hash = %s
                """,
                (text_hash,),
            )

    audit_log(
        "embedding_cache_hit",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
            "db": "postgresql",
        },
    )

    return vector


def _save_cached_embedding_pg(text: str, vector: list[float]) -> None:
    text_hash = _embedding_hash(text, EMBEDDING_MODEL, EMBEDDING_DIM)
    normalized = _normalize_embedding_text(text)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO embedding_cache
                    (text_hash, text, embedding, model, dimensions, created_at, last_used_at, hit_count)
                VALUES
                    (%s, %s, %s, %s, %s, now(), now(), 0)
                ON CONFLICT (text_hash) DO UPDATE SET
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model,
                    dimensions = EXCLUDED.dimensions,
                    last_used_at = now()
                """,
                (
                    text_hash,
                    normalized,
                    json.dumps(vector),
                    EMBEDDING_MODEL,
                    EMBEDDING_DIM,
                ),
            )

    audit_log(
        "embedding_cache_saved",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
            "db": "postgresql",
        },
    )


def embed_pg(text: str) -> list[float]:
    cached = _get_cached_embedding_pg(text)
    if cached is not None:
        return cached

    audit_log(
        "embedding_cache_miss",
        detail={
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
            "text_length": len(text or ""),
            "db": "postgresql",
        },
    )

    res = _client().embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        dimensions=EMBEDDING_DIM,
    )

    vector = res.data[0].embedding
    _save_cached_embedding_pg(text, vector)
    return vector


def _distance_to_similarity(distance: float) -> float:
    return 1.0 - (distance * distance) / 2.0


def search_knowledge_pg(question_vec: list[float], k: int = 1) -> dict | None:
    qv = Vector(question_vec)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    k.id,
                    k.question,
                    k.answer,
                    kv.embedding <-> %s::vector AS distance
                FROM knowledge_vec kv
                JOIN knowledge k ON k.id = kv.knowledge_id
                ORDER BY kv.embedding <-> %s::vector
                LIMIT %s
                """,
                (qv, qv, k),
            )
            rows = cur.fetchall()

            if not rows:
                return None

            top = rows[0]
            distance = float(top[3])
            sim = _distance_to_similarity(distance)

            if sim < SIMILARITY_THRESHOLD:
                return None

            cur.execute(
                "UPDATE knowledge SET hit_count = hit_count + 1 WHERE id = %s",
                (top[0],),
            )

    return {
        "id": top[0],
        "question": top[1],
        "answer": top[2],
        "similarity": sim,
    }


def log_pending_question_pg(question: str, question_vec: list[float]) -> int:
    qv = Vector(question_vec)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    pv.pending_id,
                    pv.embedding <-> %s::vector AS distance
                FROM pending_vec pv
                JOIN pending_questions p ON p.id = pv.pending_id
                WHERE p.status = 'pending'
                ORDER BY pv.embedding <-> %s::vector
                LIMIT 1
                """,
                (qv, qv),
            )
            rows = cur.fetchall()

            if rows and _distance_to_similarity(float(rows[0][1])) >= CLUSTER_THRESHOLD:
                pending_id = rows[0][0]
                cur.execute(
                    """
                    UPDATE pending_questions
                    SET ask_count = ask_count + 1,
                        last_asked_at = now()
                    WHERE id = %s
                    """,
                    (pending_id,),
                )
                return pending_id

            cur.execute(
                """
                INSERT INTO pending_questions (question)
                VALUES (%s)
                RETURNING id
                """,
                (question,),
            )
            pending_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO pending_vec (pending_id, embedding)
                VALUES (%s, %s)
                """,
                (pending_id, Vector(question_vec)),
            )

    return pending_id


def add_knowledge_pg(
    question: str,
    answer: str,
    approved_by: int | None,
    source: str = "admin",
) -> int:
    vec = embed_pg(question)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge (question, answer, approved_by, source)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (question, answer, approved_by, source),
            )
            knowledge_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO knowledge_vec (knowledge_id, embedding)
                VALUES (%s, %s)
                """,
                (knowledge_id, Vector(vec)),
            )

    return knowledge_id


def resolve_pending_pg(pending_id: int, answer: str, approved_by: int) -> int:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT question FROM pending_questions WHERE id = %s",
                (pending_id,),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError("pending question not found")

    knowledge_id = add_knowledge_pg(row[0], answer, approved_by)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_questions SET status = 'answered' WHERE id = %s",
                (pending_id,),
            )
            cur.execute(
                "DELETE FROM pending_vec WHERE pending_id = %s",
                (pending_id,),
            )

    return knowledge_id