# PostgreSQL Migration PR Checklist

## Branch

```text
migrate-postgresql-pgvector
```

## Purpose

This PR adds optional PostgreSQL + pgvector support while keeping SQLite as the default fallback mode.

## Database Modes

### Default SQLite Mode

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

### PostgreSQL Mode

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://user:password@host:5432/database
```

## Major Changes

- Added PostgreSQL connection and schema initialization
- Added pgvector support for semantic search
- Added PostgreSQL adapters for:
  - RAG / knowledge / pending questions
  - auth / users
  - chat sessions / chat history
  - attachments metadata
  - admin knowledge / pending
  - admin chat history / exports
- Added DB engine switching using `DB_ENGINE`
- Kept SQLite as default mode
- Added PostgreSQL deployment and smoke test documentation

## Verified Flows

The following flows passed local PostgreSQL smoke testing:

- Register
- Login
- Auth current user
- RAG chat
- File upload chat
- Chat session creation
- Chat session list/detail
- Rename session
- Save/unsave session
- Delete session
- Chat search
- Session export
- Admin knowledge list
- Admin pending list
- Admin pending ignore
- Admin knowledge verify
- Admin chat history
- Admin export all chat history

## Smoke Test Evidence

Local PostgreSQL mode was tested with:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
```

Local PostgreSQL container:

```text
pgvector/pgvector:pg16
```

Expected tables verified:

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

## Documentation Added

```text
docs/postgresql-deployment-checklist.md
docs/postgresql-smoke-test.md
docs/postgresql-migration-summary.md
docs/test-server-deployment-plan.md
```

## Environment Example Updated

```text
backend/.env.example
```

## Important Notes

- SQLite remains default.
- PostgreSQL mode requires `DB_ENGINE=postgres`.
- PostgreSQL mode requires a valid `DATABASE_URL`.
- External PostgreSQL requires DB/network readiness:
  - database exists
  - database user exists
  - pgvector enabled
  - network rule / pg_hba.conf allows app server
  - correct user privileges
- PostgreSQL stores attachment metadata only.
- Uploaded files still depend on `UPLOAD_DIR`.
- Production requires a strong `JWT_SECRET`.
- Production requires backup for both PostgreSQL and upload directory.

## Deployment Recommendation

For test server:

```text
Use PostgreSQL + pgvector container on app test server if central IT DB is not ready.
```

For production:

```text
Prefer IT-managed PostgreSQL with pgvector enabled.
```

## Rollback

To rollback to SQLite mode:

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

Restart backend after changing environment variables.

## Review Checklist

- [ ] SQLite mode still works
- [ ] PostgreSQL mode works locally
- [ ] `.env.example` reviewed
- [ ] Documentation reviewed
- [ ] No secrets committed
- [ ] Test server deployment plan reviewed
- [ ] Upload directory persistence reviewed
- [ ] PostgreSQL backup plan reviewed
- [ ] Production JWT secret plan reviewed