# Production Runbook

## Project

```text
siriwattana_bot
```

## Production Branch

```text
main
```

## Purpose

This runbook collects common production operation commands for self-hosted deployment.

It covers:

- Start services
- Stop services
- Check status
- Check PostgreSQL
- Deploy from main
- Start backend
- Run smoke tests
- Backup database
- Backup uploads
- Restore test
- Common troubleshooting

---

## Important Production Database URL

For Windows + Docker self-hosted PostgreSQL, use `127.0.0.1` instead of `localhost`.

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@127.0.0.1:5434/chatbot_prod
```

Do not use:

```env
DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@localhost:5434/chatbot_prod
```

`localhost` may be slow or hang on some Windows + Docker environments.

### Current prod-style local URL format

For the current self-hosted production-style container, use this format:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<test-or-production-password>@127.0.0.1:5434/chatbot_prod
```

For real production, replace `<test-or-production-password>` with a strong random password and update both:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`

Generate a strong password:

```cmd
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 1. Start Docker Services

From project root:

```cmd
cd /d C:\Users\MIS-BPK\Desktop\siriwattana_\siriwattana_bot
backend\.venv\Scripts\activate

docker start siriwattana-postgres-prod
docker ps
```

Expected PostgreSQL container:

```text
siriwattana-postgres-prod
127.0.0.1:5434->5432/tcp
```

Optional local dev database:

```cmd
docker start siriwattana-postgres-local
```

---

## 2. Check Docker Containers

```cmd
docker ps
```

Expected important containers:

```text
siriwattana-postgres-prod
siriwattana-postgres-local
siriwattana-backend
siriwattana-frontend
```

Note:

```text
Production-style PostgreSQL:
siriwattana-postgres-prod
127.0.0.1:5434 -> chatbot_prod
```

---

## 3. Deploy from main

Production deployment should use `main`.

```cmd
git checkout main
git pull origin main
git status
```

Expected:

```text
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
```

If temporarily deploying directly from the PostgreSQL migration branch:

```cmd
git checkout migrate-postgresql-pgvector
git pull origin migrate-postgresql-pgvector
```

Recommended production branch:

```text
main
```

---

## 4. Set Backend Environment

For production-style PostgreSQL:

```cmd
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@127.0.0.1:5434/chatbot_prod
```

For real production, replace `<strong-postgres-password>` with a strong random password.

Do not commit real production secrets to Git.

---

## 5. Production `.env` Template

Create or update:

```text
backend\.env
```

Example:

```env
OPENAI_API_KEY=<real-openai-api-key>

JWT_SECRET=<strong-random-secret-at-least-32-characters>
JWT_EXPIRES_HOURS=8

DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@127.0.0.1:5434/chatbot_prod

SIMILARITY_THRESHOLD=0.6

LLM_MODEL=gpt-4o-mini

EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1024

CORS_ORIGIN=http://<production-frontend-host>

UPLOAD_DIR=./data/uploads
```

Generate a strong `JWT_SECRET`:

```cmd
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Important:

```text
Do not use example secrets in production.
Do not commit backend\.env to Git.
```

---

## 6. Validate PostgreSQL Connection

From project root:

```cmd
python backend\db_pg.py
```

Expected:

```text
pgvector = vector
tables = users, knowledge, knowledge_vec, pending_questions, pending_vec, chat_sessions, chat_history, attachments, embedding_cache
```

Direct database check:

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

---

## 7. Start Backend Manually

Open a dedicated backend terminal:

```cmd
cd /d C:\Users\MIS-BPK\Desktop\siriwattana_\siriwattana_bot\backend
.venv\Scripts\activate

set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:<strong-postgres-password>@127.0.0.1:5434/chatbot_prod

python -c "from auth import use_postgres_auth; print(use_postgres_auth())"

uvicorn main:app --host 0.0.0.0 --port 8010
```

Expected:

```text
True
Application startup complete.
```

For real production, prefer running backend through a service manager or Docker Compose instead of a manual terminal.

---

## 8. Check Backend API

From another terminal:

```cmd
curl.exe -I "http://localhost:8010/docs"
```

Expected:

```text
HTTP/1.1 200 OK
```

---

## 9. Login Test User

Use an existing test user only for validation.

```cmd
curl.exe -X POST "http://localhost:8010/auth/login" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  -d "username=<test-username>&password=<test-password>"
```

Set token:

```cmd
set TOKEN=<access_token>
```

Check user:

```cmd
curl.exe -X GET "http://localhost:8010/auth/me" ^
  -H "Authorization: Bearer %TOKEN%"
```

---

## 10. Test RAG Chat

```cmd
curl.exe -X POST "http://localhost:8010/chat" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  --data-urlencode "message=บริษัททำเกี่ยวกับอะไร"
```

Expected:

```json
"source": "rag"
```

---

## 11. Check Production Data

Users:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, username, role FROM users ORDER BY id DESC LIMIT 5;"
```

Knowledge:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, question, source FROM knowledge ORDER BY id DESC LIMIT 5;"
```

Chat sessions:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, user_id, title, created_at, updated_at FROM chat_sessions ORDER BY id DESC LIMIT 5;"
```

Chat history:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, user_id, session_id, question, source, asked_at FROM chat_history ORDER BY id DESC LIMIT 5;"
```

Attachments:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, message_id, user_id, filename, content_type, size_bytes FROM attachments ORDER BY id DESC LIMIT 5;"
```

---

## 12. Backup PostgreSQL

Run production PostgreSQL backup script:

```cmd
scripts\backup_postgres_prod.bat
```

Expected:

```text
Backup completed successfully.
```

Backup files are created in:

```text
backups\
```

Example:

```text
backups\chatbot_prod_backup_YYYY-MM-DD_HHMMSS.sql
```

---

## 13. Backup Uploads

Run uploads backup script:

```cmd
scripts\backup_uploads_prod.bat
```

Expected:

```text
Upload backup completed successfully.
```

Backup folder example:

```text
backups\uploads_YYYY-MM-DD_HHMMSS
```

---

## 14. Backup All

Run both database and uploads backup:

```cmd
scripts\backup_postgres_prod.bat
scripts\backup_uploads_prod.bat
```

Verify backup files:

```cmd
dir backups
```

Production backup must include both:

```text
1. PostgreSQL dump file
2. Uploads backup folder
```

PostgreSQL backup alone is not enough because uploaded files are stored on disk.

---

## 15. Restore Test

Never restore directly into `chatbot_prod` without testing first.

Use restore test script:

```cmd
scripts\restore_postgres_test.bat backups\chatbot_prod_backup_YYYY-MM-DD_HHMMSS.sql
```

This restores into:

```text
chatbot_restore_test
```

It does not overwrite production database.

After test, cleanup restore database:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d postgres -c "DROP DATABASE IF EXISTS chatbot_restore_test;"
```

---

## 16. Stop Backend

If backend is running manually in CMD:

```text
Ctrl + C
```

If it does not stop, check port:

```cmd
netstat -ano | findstr :8010
```

Kill process by PID:

```cmd
taskkill /PID <PID> /F
```

---

## 17. Stop PostgreSQL Container

```cmd
docker stop siriwattana-postgres-prod
```

Optional local dev database:

```cmd
docker stop siriwattana-postgres-local
```

---

## 18. Restart PostgreSQL Container

```cmd
docker restart siriwattana-postgres-prod
```

Check status:

```cmd
docker ps
```

---

## 19. Troubleshooting

### Backend cannot connect to PostgreSQL

Check that the container is running:

```cmd
docker ps
```

Check that the port is correct:

```text
127.0.0.1:5434->5432
```

Check `DATABASE_URL`:

```cmd
echo %DATABASE_URL%
```

Correct value:

```env
postgresql://chatbot:<password>@127.0.0.1:5434/chatbot_prod
```

Avoid:

```env
localhost:5434
```

---

### Port 8010 is already in use

```cmd
netstat -ano | findstr :8010
```

Kill the PID:

```cmd
taskkill /PID <PID> /F
```

---

### Login/register is slow

First check PostgreSQL connection speed:

```cmd
python -c "import os, psycopg, time; t=time.time(); conn=psycopg.connect(os.getenv('DATABASE_URL'), connect_timeout=5); print('connected seconds=', time.time()-t); conn.close()"
```

If it is slow with `localhost`, use:

```env
127.0.0.1
```

---

### RAG returns llm instead of rag

Check knowledge:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT id, question, source FROM knowledge ORDER BY id DESC LIMIT 5;"
```

Check vectors:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT knowledge_id FROM knowledge_vec ORDER BY knowledge_id DESC LIMIT 5;"
```

Add knowledge if needed:

```cmd
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:<password>@127.0.0.1:5434/chatbot_prod

python -c "import sys; sys.path.insert(0,'backend'); from rag import add_knowledge; print(add_knowledge('บริษัททำเกี่ยวกับอะไร', 'บริษัทดำเนินธุรกิจเกี่ยวกับการให้บริการด้านสิ่งพิมพ์ บรรจุภัณฑ์ และงานพิมพ์ครบวงจรตามข้อมูลขององค์กร', None, 'prod_seed'))"
```

---

## 20. Daily Operation Checklist

Recommended daily checks:

```text
1. docker ps
2. backend /docs responds
3. login works
4. /chat works
5. PostgreSQL backup script runs
6. Upload backup script runs
7. Disk space is healthy
```

Disk check:

```cmd
dir
```

For Docker usage:

```cmd
docker system df
```

---

## 21. Important Notes

- Use `127.0.0.1` for Docker PostgreSQL connection on Windows.
- Do not use `localhost:5434` for the PostgreSQL Docker connection on Windows.
- Do not commit real `.env` secrets.
- Do not commit real API keys, JWT secrets, or database passwords.
- Production must use a strong PostgreSQL password.
- Production must use a strong `JWT_SECRET`.
- PostgreSQL backup does not include uploaded files.
- Uploads must be backed up separately.
- Restore should be tested before real recovery.