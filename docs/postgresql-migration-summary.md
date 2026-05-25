# PostgreSQL + pgvector Migration Summary

## Project

```text
siriwattana_bot
```

## Branch

```text
migrate-postgresql-pgvector
```

## Migration Goal

Migrate the chatbot backend from SQLite-only storage to optional PostgreSQL + pgvector mode while keeping SQLite as the default fallback mode.

The PostgreSQL mode is enabled by:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://user:password@host:5432/database
```

SQLite remains the default mode:

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

---

## Current Status

PostgreSQL + pgvector migration has passed local full smoke testing.

The backend can now run in PostgreSQL mode for the main chatbot flows, including auth, RAG, chat history, admin views, and attachment metadata.

---

## Completed Work

### Database / pgvector

- Added PostgreSQL connection support.
- Added PostgreSQL schema initialization.
- Enabled pgvector extension.
- Added vector search support using pgvector.
- Added local Docker PostgreSQL + pgvector test setup.

Expected PostgreSQL tables:

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

---

### RAG / Semantic Search

Completed PostgreSQL support for:

- Embedding cache
- Knowledge records
- Knowledge vectors
- Pending questions
- Pending vectors
- Semantic search using pgvector
- RAG answer retrieval
- Pending question logging
- Knowledge creation from admin/pending resolution

Verified result:

```text
/chat response source = rag
similarity = 1.0
```

---

### Auth / Users

Completed PostgreSQL support for:

- User registration
- User login
- Password hash verification
- JWT token creation
- Current user lookup
- Admin role checks

Verified endpoints:

```text
POST /auth/register
POST /auth/login
GET /auth/me
```

---

### Chat Sessions / Chat History

Completed PostgreSQL support for:

- Creating chat sessions
- Saving chat history
- Loading chat history context
- Listing user sessions
- Getting session details
- Renaming sessions
- Saving/unsaving sessions
- Deleting sessions
- Searching chat history
- Exporting a session as CSV

Verified endpoints:

```text
POST /chat
GET /chat/sessions
GET /chat/sessions/{id}
PATCH /chat/sessions/{id}
PATCH /chat/sessions/{id}/save
DELETE /chat/sessions/{id}
GET /chat/search
GET /chat/sessions/{id}/export
```

---

### File Upload / Attachments

Completed PostgreSQL support for attachment metadata.

PostgreSQL stores:

```text
message_id
user_id
filename
content_type
size_bytes
file_path
extracted_text
```

The actual uploaded files are still stored on disk using:

```env
UPLOAD_DIR=./data/uploads
```

Verified result:

```text
/chat upload source = files
attachments returned in response
attachment metadata inserted into PostgreSQL
```

---

### Admin Endpoints

Completed PostgreSQL support for:

- Admin knowledge list
- Admin pending list
- Admin pending ignore
- Admin knowledge verify
- Admin knowledge delete
- Admin chat history list
- Admin session detail
- Admin export all chat history

Verified endpoints:

```text
GET /admin/knowledge
GET /admin/pending
POST /admin/pending/{pending_id}/answer
POST /admin/pending/{pending_id}/ignore
POST /admin/knowledge/{kid}/verify
POST /admin/knowledge
DELETE /admin/knowledge/{kid}
GET /admin/chat-history
GET /admin/chat-history/{session_id}
GET /admin/chat-history/export/all
```

---

## Documentation Added

Added deployment and smoke test documentation:

```text
docs/postgresql-deployment-checklist.md
docs/postgresql-smoke-test.md
```

Updated environment example:

```text
backend/.env.example
```

---

## Local PostgreSQL Test Configuration

Local Docker PostgreSQL example:

```cmd
docker run -d --name siriwattana-postgres-local ^
  -e POSTGRES_DB=chatbot_test ^
  -e POSTGRES_USER=chatbot ^
  -e POSTGRES_PASSWORD=chatbotpass ^
  -p 5433:5432 ^
  pgvector/pgvector:pg16
```

Local PostgreSQL environment:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
```

---

## Smoke Tests Passed

The following smoke scripts passed locally:

```cmd
python backend\db_pg.py
python backend\rag_pg_adapter_smoke.py
python backend\auth_pg_smoke.py
python backend\chat_pg_smoke.py
python backend\attachment_pg_smoke.py
```

Full API regression test passed for:

```text
register
login
auth/me
chat RAG
chat file upload
sessions list/detail
rename session
save session
chat search
session export
admin knowledge
admin pending
admin chat history
admin export all
```

---

## Known Notes

### SQLite remains supported

SQLite is still the default fallback mode. This is intentional.

```env
DB_ENGINE=sqlite
```

### PostgreSQL mode requires database readiness

To use an external PostgreSQL server, the following must be ready:

- Database exists
- Database user exists
- Network access is allowed
- `pg_hba.conf` or equivalent firewall rule allows the app server
- `pgvector` extension is installed and enabled
- `DATABASE_URL` is correct
- Application user has enough privileges

### File storage

PostgreSQL stores attachment metadata only.

Uploaded files still live in:

```env
UPLOAD_DIR=./data/uploads
```

Production must include backup strategy for both:

- PostgreSQL database
- Upload directory

### JWT secret

Production must use a strong random JWT secret.

```env
JWT_SECRET=change-me-to-a-long-random-string-at-least-32-chars
```

Do not use the default value in production.

---

## Remaining Work

### Recommended Next Phase

Prepare test server deployment.

Suggested next steps:

1. Prepare test server `.env`
2. Decide database mode for test server:
   - SQLite mode
   - PostgreSQL container on app server
   - External PostgreSQL server managed by IT
3. Run smoke test guide on test server
4. Validate frontend with backend in PostgreSQL mode
5. Validate upload directory persistence
6. Validate backup plan

### Optional cleanup/refactor

The current implementation is working, but future cleanup can improve maintainability:

- Reduce duplicated SQLite/PostgreSQL branching in `main.py`
- Move more endpoint logic into service/adapters
- Add automated tests
- Add migration/seed command for production setup
- Add admin cleanup tools for old test sessions

---

## Deployment Recommendation

### For Test Server

Recommended:

```text
Use PostgreSQL + pgvector container on the app test server if IT database is not ready.
```

This allows full PostgreSQL mode testing without waiting for the central DB server.

### For Production

Recommended long-term:

```text
Use IT-managed PostgreSQL with pgvector enabled.
```

Production should have:

- Managed backup
- Monitoring
- Disk capacity planning
- Secure credentials
- Strong JWT secret
- Stable upload storage

If IT database is not ready, production can temporarily run in SQLite mode or use a PostgreSQL container, but this should be treated as a temporary deployment decision.

---

## Final Local Status

```text
PostgreSQL backend migration core flow: passed
Full local smoke test: passed
Git branch: migrate-postgresql-pgvector
Git status after latest work: clean
```