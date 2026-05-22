# Test Server Deployment Plan

## Project

```text
siriwattana_bot
```

## Branch

```text
migrate-postgresql-pgvector
```

## Goal

Deploy the chatbot backend/frontend to the test server and verify that the system works with the selected database mode.

Supported modes:

```text
1. SQLite mode
2. PostgreSQL container on app test server
3. External PostgreSQL server managed by IT
```

---

## Recommended Test Deployment Mode

For immediate testing without waiting for IT database access:

```text
Use PostgreSQL + pgvector container on the app test server.
```

This avoids waiting for:

```text
pg_hba.conf rule
database name
database user privilege
pgvector extension on central DB
```

---

## Test Server Options

## Option A: SQLite Mode

Use this if the goal is only to deploy quickly and verify app runtime.

### Environment

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

### Pros

- Fastest to deploy
- No PostgreSQL server required
- No database network rule required

### Cons

- Does not verify PostgreSQL + pgvector mode
- Not ideal for production scale
- Requires file-level backup for SQLite DB

---

## Option B: PostgreSQL Container on App Test Server

Use this if the goal is to test PostgreSQL mode immediately.

### PostgreSQL Container

```cmd
docker run -d --name siriwattana-postgres-test ^
  -e POSTGRES_DB=chatbot_test ^
  -e POSTGRES_USER=chatbot ^
  -e POSTGRES_PASSWORD=<strong-password> ^
  -p 5433:5432 ^
  -v siriwattana_pg_test_data:/var/lib/postgresql/data ^
  pgvector/pgvector:pg16
```

### Environment

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-password>@localhost:5433/chatbot_test
```

### Pros

- Does not require central IT PostgreSQL
- Verifies PostgreSQL + pgvector mode
- Uses persistent Docker volume
- Good for test/POC

### Cons

- App team must manage DB container
- Backup strategy is still required
- Production should preferably use managed PostgreSQL

---

## Option C: External PostgreSQL Server Managed by IT

Use this when IT has prepared PostgreSQL access.

### Required Information from IT

```text
Database host
Database port
Database name
Database username
Database password
SSL requirement
pgvector extension status
Network rule / pg_hba.conf status
```

### Environment Example

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://mi:<password>@<db-host>:5432/chatbot_test
```

### Required Confirmations

- App server can reach PostgreSQL host and port
- `pg_hba.conf` or firewall allows the app server
- Database exists
- User has required privileges
- `vector` extension is enabled
- Connection string works from the app server

---

## Deployment Steps

## 1. Pull Latest Branch

```cmd
cd /path/to/siriwattana_bot
git fetch origin
git checkout migrate-postgresql-pgvector
git pull origin migrate-postgresql-pgvector
```

## 2. Prepare Backend Environment

Create or update backend `.env`.

### SQLite Example

```env
OPENAI_API_KEY=<openai-api-key>
JWT_SECRET=<strong-random-secret>
JWT_EXPIRES_HOURS=8
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
SIMILARITY_THRESHOLD=0.6
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1024
CORS_ORIGIN=http://<frontend-host>:3000
UPLOAD_DIR=./data/uploads
```

### PostgreSQL Container Example

```env
OPENAI_API_KEY=<openai-api-key>
JWT_SECRET=<strong-random-secret>
JWT_EXPIRES_HOURS=8
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-password>@localhost:5433/chatbot_test
SIMILARITY_THRESHOLD=0.6
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1024
CORS_ORIGIN=http://<frontend-host>:3000
UPLOAD_DIR=./data/uploads
```

---

## 3. Install Backend Dependencies

```cmd
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

For Linux server:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4. Validate PostgreSQL Mode

Run from project root after environment is configured:

```cmd
python backend\db_pg.py
python backend\rag_pg_adapter_smoke.py
python backend\auth_pg_smoke.py
python backend\chat_pg_smoke.py
python backend\attachment_pg_smoke.py
```

Expected:

```text
PostgreSQL RAG adapter smoke test passed
PostgreSQL auth adapter smoke test passed
PostgreSQL chat adapter smoke test passed
PostgreSQL attachment adapter smoke test passed
```

---

## 5. Start Backend

Windows:

```cmd
cd backend
.venv\Scripts\activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

Linux:

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open API docs:

```text
http://<backend-host>:8000/docs
```

---

## 6. Start Frontend

```cmd
cd frontend
npm install
npm run dev
```

Or if using Docker Compose, use the existing project deployment command.

---

## 7. Runtime Verification

Verify these flows:

```text
register
login
auth/me
chat RAG
chat file upload
session list
session detail
rename session
save session
chat search
session export
admin knowledge
admin pending
admin chat history
admin export all
```

Refer to:

```text
docs/postgresql-smoke-test.md
```

---

## 8. PostgreSQL Verification Queries

Check pgvector:

```sql
SELECT extname
FROM pg_extension
WHERE extname = 'vector';
```

Check tables:

```sql
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
```

Check chat data:

```sql
SELECT id, user_id, title, created_at, updated_at
FROM chat_sessions
ORDER BY id DESC
LIMIT 10;
```

Check attachments:

```sql
SELECT id, message_id, user_id, filename, content_type, size_bytes
FROM attachments
ORDER BY id DESC
LIMIT 10;
```

---

## Backup Notes

If using PostgreSQL container on test server:

```text
Backup Docker volume:
siriwattana_pg_test_data
```

Also backup upload directory:

```text
backend/data/uploads
```

Production must have a formal backup plan for:

```text
PostgreSQL database
UPLOAD_DIR
.env secrets
```

---

## Security Notes

Production must not use default secrets.

Required:

```env
JWT_SECRET=<strong-random-secret-at-least-32-characters>
```

Do not commit real secrets to Git.

---

## Rollback Plan

If PostgreSQL mode has issues, switch back to SQLite mode:

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

Restart backend after changing environment.

---

## Deployment Decision Summary

Recommended sequence:

```text
1. Deploy test server with PostgreSQL container mode
2. Run full smoke test
3. Validate frontend behavior
4. If stable, prepare PR/merge
5. For production, prefer IT-managed PostgreSQL
6. Use SQLite mode only as a temporary fallback if needed
```