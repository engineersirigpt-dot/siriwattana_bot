from db_pg import init_pg_schema, get_pg_conn
from pgvector import Vector

DIM = 1024


def vec(values: list[float]) -> list[float]:
    """Pad a short vector to 1024 dimensions for local pgvector smoke test."""
    return values + [0.0] * (DIM - len(values))


def main() -> None:
    init_pg_schema()

    question = "บริษัททำเกี่ยวกับอะไร"
    answer = "บริษัทดำเนินธุรกิจเกี่ยวกับการให้บริการและพัฒนาระบบตามข้อมูลขององค์กร"
    embedding = Vector(vec([1.0, 0.0, 0.0]))
    query_vec = Vector(vec([0.95, 0.05, 0.0]))

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge (question, answer, source)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (question, answer, "smoke_test"),
            )
            knowledge_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO knowledge_vec (knowledge_id, embedding)
                VALUES (%s, %s);
                """,
                (knowledge_id, embedding),
            )

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
                LIMIT 3;
                """,
                (query_vec, query_vec),
            )

            rows = cur.fetchall()

    print("RAG PostgreSQL smoke test result:")
    for row in rows:
        print(
            {
                "id": row[0],
                "question": row[1],
                "answer": row[2],
                "distance": float(row[3]),
            }
        )


if __name__ == "__main__":
    main()