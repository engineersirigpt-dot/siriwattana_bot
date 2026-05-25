# Production Self-hosted Deployment Plan

## Project

```text
siriwattana_bot
```

## Branch

```text
migrate-postgresql-pgvector
```

## Goal

Deploy the chatbot system to production using a self-hosted setup.

This plan assumes the application team may need to run PostgreSQL + pgvector without waiting for a centrally managed PostgreSQL server from IT.

---

## Recommended Production Mode

Recommended if managing production database independently:

```text
PostgreSQL + pgvector container with persistent Docker volume
```

Backend mode:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-password>@localhost:5433/chatbot_prod
```

SQLite remains available as fallback:

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

---

## Production Architecture

```text
Production Server
├── Backend API
├── Frontend
├── PostgreSQL + pgvector container
├── Persistent PostgreSQL Docker volume
└── Persistent upload directory
```

PostgreSQL should bind only to localhost:

```text
127.0.0.1:5433 -> 5432
```

This prevents external network access to the database.

---

## PostgreSQL Production Container

Create PostgreSQL + pgvector container:

```cmd
docker run -d --name siriwattana-postgres-prod ^
  --restart unless-stopped ^
  -e POSTGRES_DB=chatbot_prod ^
  -e POSTGRES_USER=chatbot ^
  -e POSTGRES_PASSWORD=<strong-password> ^
  -p 127.0.0.1:5433:5432 ^
  -v siriwattana_pg_prod_data:/var/lib/postgresql/data ^
  pgvector/pgvector:pg16
```

Verify container:

```cmd
docker ps
```

Expected:

```text
siriwattana-postgres-prod
127.0.0.1:5433->5432/tcp
```

---

## Production Backend Environment

Create backend `.env` on production server.

```env
OPENAI_API_KEY=<real-openai-api-key>

JWT_SECRET=<strong-random-secret-at-least-32-characters>
JWT_EXPIRES_HOURS=8

DB_ENGINE=postgres
DATABASE_URL=postgresql://chatbot:<strong-password>@localhost:5433/chatbot_prod

SIMILARITY_THRESHOLD=0.6

LLM_MODEL=gpt-4o-mini

EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1024

CORS_ORIGIN=http://<production-frontend-host>

UPLOAD_DIR=./data/uploads
```

Important:

```text
Do not commit production .env to Git.
```

---

## Required Persistent Storage

Production must persist:

```text
1. PostgreSQL Docker volume
2. Upload directory
3. Backend .env secrets
```

Recommended locations:

```text
PostgreSQL data:
Docker volume siriwattana_pg_prod_data

Uploads:
backend/data/uploads

Secrets:
backend/.env
```

---

## Deploy Code

Pull latest branch:

```cmd
git fetch origin
git checkout migrate-postgresql-pgvector
git pull origin migrate-postgresql-pgvector
```

Install backend dependencies:

```cmd
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux equivalent:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Validate PostgreSQL Mode

From project root:

```cmd
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:<strong-password>@localhost:5433/chatbot_prod
python backend\db_pg.py
```

Run adapter smoke tests:

```cmd
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

## Start Backend

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

Production should ideally run backend through a process manager or container orchestration.

Examples:

```text
Docker Compose
systemd
pm2
supervisor
Windows service manager
```

---

## Start Frontend

```cmd
cd frontend
npm install
npm run build
npm run start
```

Or use the existing Docker/frontend deployment approach if already available.

---

## Production Smoke Test

Follow:

```text
docs/postgresql-smoke-test.md
```

Minimum production validation:

```text
1. Register
2. Login
3. Auth/me
4. Chat RAG
5. File upload chat
6. Chat sessions
7. Chat search
8. Export session
9. Admin knowledge
10. Admin chat history
11. Admin export all
```

---

## Verify PostgreSQL Database

Check pgvector:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

Expected:

```text
vector
```

Check tables:

```cmd
docker exec -it siriwattana-postgres-prod psql -U chatbot -d chatbot_prod -c "\dt"
```

Expected tables:

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

## Backup Plan

Production must backup both database and uploads.

### PostgreSQL backup

```cmd
mkdir backups
docker exec siriwattana-postgres-prod pg_dump -U chatbot chatbot_prod > backups\chatbot_prod_backup.sql
```

### Upload directory backup

Windows:

```cmd
xcopy backend\data\uploads backups\uploads /E /I /Y
```

Linux:

```bash
mkdir -p backups/uploads
rsync -av backend/data/uploads/ backups/uploads/
```

Recommended backup frequency:

```text
Daily minimum
More frequent if production usage is high
```

---

## Restore Plan

### Restore PostgreSQL

```cmd
type backups\chatbot_prod_backup.sql | docker exec -i siriwattana-postgres-prod psql -U chatbot -d chatbot_prod
```

Linux:

```bash
cat backups/chatbot_prod_backup.sql | docker exec -i siriwattana-postgres-prod psql -U chatbot -d chatbot_prod
```

### Restore uploads

Windows:

```cmd
xcopy backups\uploads backend\data\uploads /E /I /Y
```

Linux:

```bash
rsync -av backups/uploads/ backend/data/uploads/
```

---

## Security Checklist

- [ ] Strong `JWT_SECRET`
- [ ] Real `OPENAI_API_KEY`
- [ ] `.env` not committed to Git
- [ ] PostgreSQL bound to `127.0.0.1`
- [ ] PostgreSQL password is strong
- [ ] Upload directory is persistent
- [ ] Backups are configured
- [ ] CORS is restricted to production frontend
- [ ] Server firewall is configured
- [ ] Backend logs are monitored
- [ ] Disk usage is monitored

---

## Rollback Plan

If PostgreSQL mode has production issues, rollback to SQLite mode:

```env
DB_ENGINE=sqlite
DB_PATH=./data/chatbot.db
```

Then restart backend.

Rollback note:

```text
Data created in PostgreSQL mode will not automatically appear in SQLite mode.
```

If rollback is needed, preserve PostgreSQL data for later investigation.

---

## Risks

### Self-hosted PostgreSQL risks

```text
- Database backups must be managed by the app team
- Disk failure can cause data loss if backups are missing
- Container volume must not be deleted
- PostgreSQL upgrades must be handled carefully
- Monitoring is required
```

### Upload risks

```text
- PostgreSQL stores only metadata
- Actual uploaded files live in UPLOAD_DIR
- Upload directory must be backed up separately
```

---

## Recommendation

For production self-hosting:

```text
Use PostgreSQL container only if backup, monitoring, and volume persistence are ready.
```

Long-term preferred production setup:

```text
IT-managed PostgreSQL with pgvector enabled, backup, monitoring, and access control.
```