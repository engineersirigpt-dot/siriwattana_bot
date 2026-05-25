# Production Environment Template

## Project

```text
siriwattana_bot
```

## Branch

```text
migrate-postgresql-pgvector
```

## Purpose

This document is a production environment checklist/template.

Do not commit real production secrets to Git.

Use this document to prepare the real production `backend/.env` file directly on the production server.

---

## Production `.env` Template

```env
OPENAI_API_KEY=<real-openai-api-key>

JWT_SECRET=<strong-random-secret-at-least-32-characters>
JWT_EXPIRES_HOURS=8

DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@localhost:5433/chatbot_prod

SIMILARITY_THRESHOLD=0.6

LLM_MODEL=gpt-4o-mini

EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1024

CORS_ORIGIN=http://<production-frontend-host>

UPLOAD_DIR=./data/uploads
```

---

## Required Values

### OPENAI_API_KEY

Required for LLM and embedding calls.

```env
OPENAI_API_KEY=<real-openai-api-key>
```

Checklist:

- [ ] Real API key is available
- [ ] API key is not committed to Git
- [ ] API key is configured only in production `.env`

---

### JWT_SECRET

Required for signing login tokens.

```env
JWT_SECRET=<strong-random-secret-at-least-32-characters>
```

Checklist:

- [ ] At least 32 characters
- [ ] Random and hard to guess
- [ ] Not equal to default/example value
- [ ] Not committed to Git

Example generation command:

```cmd
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

### DB_ENGINE

Production PostgreSQL mode:

```env
DB_ENGINE=postgres
```

Fallback SQLite mode:

```env
DB_ENGINE=sqlite
```

Checklist:

- [ ] Use `postgres` for PostgreSQL production mode
- [ ] Use `sqlite` only as fallback or temporary deployment mode

---

### DATABASE_URL

Required when:

```env
DB_ENGINE=postgres
```

Self-hosted PostgreSQL container example:

```env
DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@localhost:5433/chatbot_prod
```

Checklist:

- [ ] Username is correct
- [ ] Password is correct
- [ ] Host is correct
- [ ] Port is correct
- [ ] Database name is correct
- [ ] Password is not committed to Git

---

### PostgreSQL Container Password

The PostgreSQL container uses:

```env
POSTGRES_PASSWORD=<strong-postgres-password>
```

This password must match the password inside `DATABASE_URL`.

Checklist:

- [ ] Strong password selected
- [ ] Same password used in PostgreSQL container
- [ ] Same password used in `DATABASE_URL`
- [ ] Password stored securely

---

### CORS_ORIGIN

Set this to the production frontend origin.

Example:

```env
CORS_ORIGIN=http://<production-frontend-host>
```

Checklist:

- [ ] Frontend host is correct
- [ ] Protocol is correct: `http` or `https`
- [ ] Port is included if required
- [ ] Do not use wildcard in production unless explicitly approved

---

### UPLOAD_DIR

Uploaded files are stored on disk.

```env
UPLOAD_DIR=./data/uploads
```

Checklist:

- [ ] Directory exists
- [ ] Backend process has write permission
- [ ] Directory is persistent
- [ ] Directory is included in backup plan

---

## Production PostgreSQL Container Reference

```cmd
docker run -d --name siriwattana-postgres-prod ^
  --restart unless-stopped ^
  -e POSTGRES_DB=chatbot_prod ^
  -e POSTGRES_USER=chatbot ^
  -e POSTGRES_PASSWORD=<strong-postgres-password> ^
  -p 127.0.0.1:5433:5432 ^
  -v siriwattana_pg_prod_data:/var/lib/postgresql/data ^
  pgvector/pgvector:pg16
```

Important:

```text
Bind PostgreSQL to 127.0.0.1 only.
Do not expose PostgreSQL publicly.
```

---

## Production Startup Checklist

Before starting backend:

- [ ] PostgreSQL container is running
- [ ] PostgreSQL volume is persistent
- [ ] `backend/.env` exists on production server
- [ ] `OPENAI_API_KEY` is real
- [ ] `JWT_SECRET` is strong
- [ ] `DB_ENGINE=postgres`
- [ ] `DATABASE_URL` is correct
- [ ] `UPLOAD_DIR` exists
- [ ] Frontend URL is configured in `CORS_ORIGIN`
- [ ] Backup directory exists

---

## Validation Commands

Check backend env mode:

```cmd
python -c "from auth import use_postgres_auth; print(use_postgres_auth())"
```

Expected:

```text
True
```

Check PostgreSQL:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "\dt"
```

Check pgvector:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

Expected:

```text
vector
```

Run smoke scripts:

```cmd
python backend\db_pg.py
python backend\rag_pg_adapter_smoke.py
python backend\auth_pg_smoke.py
python backend\chat_pg_smoke.py
python backend\attachment_pg_smoke.py
```

---

## Backup Checklist

PostgreSQL backup:

```cmd
docker exec siriwattana-postgres-prod pg_dump -U chatbot chatbot_prod > backups\chatbot_prod_backup.sql
```

Upload backup:

```cmd
xcopy backend\data\uploads backups\uploads /E /I /Y
```

Checklist:

- [ ] Database backup command tested
- [ ] Upload backup command tested
- [ ] Backup files stored outside app working directory if possible
- [ ] Backup schedule defined
- [ ] Restore process documented and tested

---

## Security Checklist

- [ ] Real secrets are not committed to Git
- [ ] PostgreSQL is bound to `127.0.0.1`
- [ ] PostgreSQL password is strong
- [ ] JWT secret is strong
- [ ] Production `.env` permissions are restricted
- [ ] CORS origin is restricted
- [ ] Server firewall is configured
- [ ] Backup files are protected
- [ ] Logs do not expose secrets

---

## Notes

- SQLite remains available as fallback.
- PostgreSQL mode is recommended for production if backup and persistence are ready.
- PostgreSQL stores attachment metadata only.
- Uploaded files still depend on `UPLOAD_DIR`.
- Production must backup both PostgreSQL and upload directory.