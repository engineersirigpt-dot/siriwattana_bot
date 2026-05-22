# PostgreSQL Smoke Test Guide

This guide is used to verify that the chatbot backend works correctly in PostgreSQL + pgvector mode.

## Branch

```text
migrate-postgresql-pgvector
```

## 1. Start PostgreSQL Local Container

```cmd
docker start siriwattana-postgres-local
docker ps
```

Expected container:

```text
siriwattana-postgres-local
0.0.0.0:5433->5432/tcp
```

## 2. Set PostgreSQL Environment

From project root:

```cmd
cd /d C:\Users\MIS-BPK\Desktop\siriwattana_\siriwattana_bot
backend\.venv\Scripts\activate
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
```

Verify PostgreSQL mode:

```cmd
python -c "import sys; sys.path.insert(0,'backend'); from auth import use_postgres_auth; print(use_postgres_auth())"
```

Expected:

```text
True
```

## 3. Verify PostgreSQL and pgvector

List tables:

```cmd
docker exec -it siriwattana-postgres-local psql -U chatbot -d chatbot_test -c "\dt"
```

Verify pgvector:

```cmd
docker exec -it siriwattana-postgres-local psql -U chatbot -d chatbot_test -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

Expected:

```text
vector
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

## 4. Run Backend Smoke Scripts

Run from project root:

```cmd
python backend\db_pg.py
python backend\rag_pg_adapter_smoke.py
python backend\auth_pg_smoke.py
python backend\chat_pg_smoke.py
python backend\attachment_pg_smoke.py
```

Expected result:

```text
PostgreSQL RAG adapter smoke test passed
PostgreSQL auth adapter smoke test passed
PostgreSQL chat adapter smoke test passed
PostgreSQL attachment adapter smoke test passed
```

## 5. Start Backend API

In a separate terminal:

```cmd
cd /d C:\Users\MIS-BPK\Desktop\siriwattana_\siriwattana_bot\backend
.venv\Scripts\activate
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
python -c "from auth import use_postgres_auth; print(use_postgres_auth())"
uvicorn main:app --reload --host 0.0.0.0 --port 8010
```

Expected:

```text
True
Application startup complete.
```

Open:

```text
http://localhost:8010/docs
```

## 6. Register and Login

In another terminal from project root:

```cmd
cd /d C:\Users\MIS-BPK\Desktop\siriwattana_\siriwattana_bot
backend\.venv\Scripts\activate
set DB_ENGINE=postgres
set DATABASE_URL=postgresql://chatbot:chatbotpass@localhost:5433/chatbot_test
```

Register test user:

```cmd
curl.exe -X POST "http://localhost:8010/auth/register" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  -d "username=pgsmoke&password=pgtest123"
```

Login:

```cmd
curl.exe -X POST "http://localhost:8010/auth/login" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  -d "username=pgsmoke&password=pgtest123"
```

Set token:

```cmd
set TOKEN=<access_token_from_login_response>
```

Verify current user:

```cmd
curl.exe -X GET "http://localhost:8010/auth/me" ^
  -H "Authorization: Bearer %TOKEN%"
```

Expected:

```json
{"id":..., "username":"pgsmoke", "role":"user"}
```

## 7. Test RAG Chat

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

## 8. Test File Upload

Create UTF-8 test file:

```cmd
powershell -Command "Set-Content -Path test_upload_utf8.txt -Value 'บริษัททดสอบไฟล์แนบ PostgreSQL' -Encoding UTF8"
```

Upload file:

```cmd
curl.exe -X POST "http://localhost:8010/chat" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -F "message=ช่วยสรุปไฟล์นี้" ^
  -F "files=@test_upload_utf8.txt;type=text/plain"
```

Expected:

```json
"source": "files"
```

Verify attachment metadata in PostgreSQL:

```cmd
docker exec -it siriwattana-postgres-local psql -U chatbot -d chatbot_test -c "SELECT id, message_id, user_id, filename, content_type, size_bytes FROM attachments ORDER BY id DESC LIMIT 5;"
```

Expected:

```text
test_upload_utf8.txt
```

Clean local test file:

```cmd
del test_upload_utf8.txt
```

## 9. Test Sessions

List sessions:

```cmd
curl.exe -X GET "http://localhost:8010/chat/sessions" ^
  -H "Authorization: Bearer %TOKEN%"
```

Get session detail:

```cmd
curl.exe -X GET "http://localhost:8010/chat/sessions/<session_id>" ^
  -H "Authorization: Bearer %TOKEN%"
```

Rename session:

```cmd
curl.exe -X PATCH "http://localhost:8010/chat/sessions/<session_id>" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"title\":\"postgres smoke test\"}"
```

Save session:

```cmd
curl.exe -X PATCH "http://localhost:8010/chat/sessions/<session_id>/save" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"is_saved\":true}"
```

Search chat:

```cmd
curl.exe -G "http://localhost:8010/chat/search" ^
  -H "Authorization: Bearer %TOKEN%" ^
  --data-urlencode "q=บริษัท"
```

Export session:

```cmd
curl.exe -X GET "http://localhost:8010/chat/sessions/<session_id>/export" ^
  -H "Authorization: Bearer %TOKEN%"
```

Expected CSV header:

```csv
timestamp,question,answer,source
```

## 10. Test Admin Endpoints

Promote smoke user to admin:

```cmd
python -c "import sys; sys.path.insert(0,'backend'); from auth_pg import set_user_role_pg, get_user_by_username_pg; print(set_user_role_pg('pgsmoke','admin')); print(get_user_by_username_pg('pgsmoke'))"
```

Login again and set new admin token:

```cmd
curl.exe -X POST "http://localhost:8010/auth/login" ^
  -H "Content-Type: application/x-www-form-urlencoded" ^
  -d "username=pgsmoke&password=pgtest123"
```

```cmd
set TOKEN=<admin_access_token_from_login_response>
```

Admin knowledge:

```cmd
curl.exe -X GET "http://localhost:8010/admin/knowledge" ^
  -H "Authorization: Bearer %TOKEN%"
```

Admin pending:

```cmd
curl.exe -X GET "http://localhost:8010/admin/pending" ^
  -H "Authorization: Bearer %TOKEN%"
```

Admin chat history:

```cmd
curl.exe -X GET "http://localhost:8010/admin/chat-history" ^
  -H "Authorization: Bearer %TOKEN%"
```

Admin export all:

```cmd
curl.exe -X GET "http://localhost:8010/admin/chat-history/export/all" ^
  -H "Authorization: Bearer %TOKEN%"
```

Expected CSV header:

```csv
username,session_title,timestamp,question,answer,source
```

## 11. Final Git Check

```cmd
git status
```

Expected:

```text
nothing to commit, working tree clean
```

## Notes

- Use `--data-urlencode` for Thai query strings in curl.
- SQLite remains the default mode.
- PostgreSQL mode requires `DB_ENGINE=postgres` and a valid `DATABASE_URL`.
- PostgreSQL stores attachment metadata only. Uploaded files still live in `UPLOAD_DIR`.
- Production must use a strong `JWT_SECRET`.