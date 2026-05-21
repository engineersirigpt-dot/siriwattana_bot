from pgvector import Vector

from db_pg import init_pg_schema, get_pg_conn
from rag_pg import search_knowledge_pg, log_pending_question_pg


DIM = 1024


def vec(values: list[float]) -> list[float]:
    return values + [0.0] * (DIM - len(values))


def main() -> None:
    init_pg_schema()
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM knowledge
                WHERE source IN ('adapter_smoke_test', 'smoke_test')
                """
            )
            cur.execute(
                """
                DELETE FROM pending_questions
                WHERE question = %s
                """,
                ("คำถามใหม่ที่ยังไม่มีคำตอบ",),
            )

    knowledge_question = "บริษัททำเกี่ยวกับอะไร"
    knowledge_answer = "บริษัทดำเนินธุรกิจเกี่ยวกับการให้บริการและพัฒนาระบบตามข้อมูลขององค์กร"
    knowledge_vec = vec([1.0, 0.0, 0.0])
    query_vec = vec([0.95, 0.05, 0.0])

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge (question, answer, source)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (knowledge_question, knowledge_answer, "adapter_smoke_test"),
            )
            knowledge_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO knowledge_vec (knowledge_id, embedding)
                VALUES (%s, %s)
                """,
                (knowledge_id, Vector(knowledge_vec)),
            )

    result = search_knowledge_pg(query_vec, k=1)
    print("search_knowledge_pg result:")
    print(result)

    pending_question = "คำถามใหม่ที่ยังไม่มีคำตอบ"
    pending_vec = vec([0.0, 1.0, 0.0])
    pending_id = log_pending_question_pg(pending_question, pending_vec)

    print("log_pending_question_pg result:")
    print({"pending_id": pending_id})

    if not result:
        raise RuntimeError("search_knowledge_pg returned None")

    if result["question"] != knowledge_question:
        raise RuntimeError(
            f"Expected question={knowledge_question!r}, got {result['question']!r}"
        )

    if not pending_id:
        raise RuntimeError("log_pending_question_pg did not return pending_id")

    print("PostgreSQL RAG adapter smoke test passed")


if __name__ == "__main__":
    main()