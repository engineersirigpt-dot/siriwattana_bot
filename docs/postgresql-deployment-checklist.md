# PostgreSQL + pgvector Deployment Checklist

## Current Branch

```text
migrate-postgresql-pgvector
```

## Supported Database Modes

### SQLite mode

Default and stable mode.

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

### PostgreSQL mode

PostgreSQL + pgvector mode.

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://user:password@host:5432/database
```

## Required PostgreSQL Setup

Before enabling `DB_ENGINE=postgres`, confirm:

- PostgreSQL server is reachable from the app server
- Database exists
- Application user exists
- Application user has permission to create/read/update/delete required tables
- `pgvector` extension is installed and enabled
- `DATABASE_URL` is configured correctly
- Network rule / `pg_hba.conf` allows the app server

## Local Docker PostgreSQL Example

```cmd
docker run -d --name siriwattana-postgres-local ^
  -e POSTGRES_DB=chatbot_test ^
  -e POSTGRES_USER=chatbot ^
  -e POSTGRES_PASSWORD=chatbotpass ^
  -p 5433:5432 ^
  pgvector/pgvector:pg16
```

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
```

## Test Server Example

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://mi:<password>@<db-host>:5432/chatbot_test
```

## Production Example

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://<user>:<password>@<db-host>:5432/chatbot_prod
```

## Backend Validation Commands

Run after setting `DB_ENGINE` and `DATABASE_URL`.

```cmd
python backend\db_pg.py
python backend\rag_pg_adapter_smoke.py
python backend\auth_pg_smoke.py
python backend\chat_pg_smoke.py
python backend\attachment_pg_smoke.py
```

## Runtime Smoke Test

Start backend:

```cmd
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8010
```

Test endpoints:

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `POST /chat`
- `GET /chat/sessions`
- `GET /chat/sessions/{id}`
- `PATCH /chat/sessions/{id}`
- `PATCH /chat/sessions/{id}/save`
- `GET /chat/search`
- `GET /chat/sessions/{id}/export`
- `GET /admin/knowledge`
- `GET /admin/pending`
- `GET /admin/chat-history`
- `GET /admin/chat-history/export/all`

## PostgreSQL Tables Expected

```text
users
knowledge
knowledge_vec
pending_questions
pending_vec
chat_sessions
chat_history
attachments
embedding_cache
```

## Verify pgvector

```sql
SELECT extname
FROM pg_extension
WHERE extname = 'vector';
```

Expected:

```text
vector
```

## Notes

- SQLite remains the default fallback mode.
- PostgreSQL mode should be enabled only after database and network access are confirmed.
- For production, use a strong `JWT_SECRET`.
- For production, backup strategy for PostgreSQL is required.
- File upload storage still depends on `UPLOAD_DIR`; PostgreSQL stores attachment metadata only.