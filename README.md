# Sirivatana Chatbot

AI Chatbot ภายในบริษัทศิริวัฒนาอินเตอร์พริ้นท์ สำหรับตอบคำถามพนักงานด้วย RAG บนฐานความรู้ขององค์กร รองรับการอัปโหลดไฟล์ และมีหน้า Admin จัดการความรู้

![Logo](Logo_siri.jpg)

---

## ฟีเจอร์หลัก

- **RAG (Retrieval-Augmented Generation)** — ตอบคำถามจากฐานความรู้บริษัทที่ Admin ดูแล
- **Fallback to LLM** — เมื่อไม่พบใน KB จะตอบแบบ ChatGPT ทั่วไป และบันทึกคำถามให้ Admin ตรวจสอบ
- **อัปโหลดไฟล์** — รองรับ PDF, DOCX, XLSX, PPTX, รูปภาพ, และไฟล์โค้ดกว่า 60 นามสกุล
- **หน้า Admin** — ดูคำถามรอตอบ, จัดการ Knowledge Base, ดู Chat History ทั้งหมด
- **Session Management** — บันทึก/bookmark สนทนา, เก็บแค่ 20 แชทล่าสุดอัตโนมัติ, เปลี่ยนชื่อ, ส่งออก CSV
- **ระบบสมาชิก** — JWT Auth, Role-based (admin/user), bcrypt password

---

## สถาปัตยกรรม

```
Next.js Frontend  (login / chat / admin)         http://localhost:3002
        │
        ▼  REST API + JWT
FastAPI Backend   (Python)                        http://localhost:8000
        │
        ├─ SQLite + sqlite-vec   (knowledge, users, pending_questions, chat_history)
        ├─ OpenAI Embedding API  (text-embedding-3-large @ 1024 dim)
        └─ OpenAI Chat API       (gpt-4o-mini)
```

### Flow การตอบคำถาม

```
User ถาม
  ↓
Embed คำถาม (1024 dim)
  ↓
ค้นใน Knowledge Base (sqlite-vec)
  ├─ เจอ (similarity ≥ 0.75) → ตอบจาก KB + source = "rag"
  └─ ไม่เจอ → ตอบแบบ LLM ทั่วไป + source = "llm"
               + บันทึกใน pending_questions (Admin เห็นใน /admin)

Admin → เปิด pending_questions → ตอบ → กดอนุมัติ
      → คำถาม+คำตอบเข้า Knowledge Base → RAG ตอบได้ทันทีครั้งต่อไป
```

---

## โครงสร้างโปรเจกต์

```
Sirivatana_Chatbot/
├── backend/
│   ├── main.py                     # FastAPI app — endpoints ทั้งหมด
│   ├── rag.py                      # Vector embedding + similarity search
│   ├── llm.py                      # OpenAI client + system prompts + model routing
│   ├── db.py                       # SQLite schema + migrations
│   ├── auth.py                     # JWT + bcrypt + role check
│   ├── attachments.py              # File upload + text extraction
│   ├── seed.py                     # Import seed_knowledge.json เข้า DB
│   ├── import_company_kb.py        # แปลง sirivatana_kb.json เข้า RAG
│   ├── seed_knowledge.json         # ข้อมูล HR ตัวอย่าง (ลา, เงินเดือน, OT ฯลฯ)
│   ├── sirivatana_kb.json          # ข้อมูลบริษัท (สำเนาจาก root)
│   ├── requirements.txt
│   ├── .env                        # ★ ไม่ commit — ดู .env.example
│   └── data/
│       ├── chatbot.db              # SQLite database
│       └── uploads/                # ไฟล์แนบจาก user
├── frontend/
│   ├── app/
│   │   ├── page.tsx                # redirect to /chat หรือ /login
│   │   ├── login/page.tsx          # หน้า Login / Register
│   │   ├── chat/page.tsx           # หน้าแชทหลัก
│   │   └── admin/page.tsx          # หน้า Admin
│   ├── lib/api.ts                  # API client (fetch wrapper + auth header)
│   ├── components/                 # React components
│   ├── .env.local                  # ★ ไม่ commit — ดู .env.local.example
│   └── package.json
├── sirivatana_company_chatbot_knowledge.json   # master copy ข้อมูลบริษัท
├── setup.ps1                       # ติดตั้งครั้งแรก (Windows)
├── start-backend.ps1               # รัน backend
├── start-frontend.ps1              # รัน frontend
├── start-backend-prod.ps1          # รัน backend (production mode)
├── .env.example                    # ตัวอย่าง env root
└── .gitignore
```

---

## Requirements

| Software | Version |
|---|---|
| Python | 3.12+ |
| Node.js | 20+ (LTS) |
| OpenAI API Key | — |

---

## การติดตั้ง (ครั้งแรก)

### 1. Clone / วางโปรเจกต์

```powershell
cd C:\path\to\Sirivatana_Chatbot
```

### 2. รัน setup script

```powershell
.\setup.ps1
```

สร้าง Python venv + ลง backend dependencies + ลง frontend packages (~2–3 นาที)

> **หาก PowerShell บล็อก script:**
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### 3. ตั้งค่า Environment Variables

**Backend** — คัดลอก `backend/.env.example` เป็น `backend/.env` แล้วแก้:

```env
OPENAI_API_KEY=sk-proj-xxxxx          # ★ ใส่ key จริง
JWT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx   # ★ สุ่มยาวอย่างน้อย 32 ตัวอักษร
```

**Frontend** — คัดลอก `frontend/.env.local.example` เป็น `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

### 4. Import ข้อมูลเริ่มต้น (ครั้งแรก)

```powershell
cd backend
.\.venv\Scripts\python.exe seed.py
.\.venv\Scripts\python.exe import_company_kb.py
cd ..
```

### 5. รันระบบ

เปิด **2 PowerShell windows** พร้อมกัน:

```powershell
# Window 1 — Backend
.\start-backend.ps1
# → http://localhost:8000  |  API docs: http://localhost:8000/docs
```

```powershell
# Window 2 — Frontend
.\start-frontend.ps1
# → http://localhost:3002
```

เปิดเบราว์เซอร์ไปที่ **http://localhost:3002** → สมัครบัญชีแรก (เป็น Admin อัตโนมัติ)

---

## การ Import / อัปเดตข้อมูลบริษัท

แก้ไขไฟล์ `sirivatana_company_chatbot_knowledge.json` (master copy ที่ root) จากนั้น:

```powershell
Copy-Item sirivatana_company_chatbot_knowledge.json backend\sirivatana_kb.json -Force
cd backend
.\.venv\Scripts\python.exe import_company_kb.py
```

Script จะแปลงทุก section เป็นคู่ Q&A แล้วใส่เข้า RAG (idempotent — รันซ้ำได้ ข้ามที่มีอยู่แล้ว)

---

## API Endpoints

| Endpoint | สิทธิ์ | คำอธิบาย |
|---|---|---|
| `POST /auth/register` | — | สมัครสมาชิก (คนแรก = admin) |
| `POST /auth/login` | — | รับ JWT token |
| `GET /auth/me` | user | ข้อมูล user ปัจจุบัน |
| `POST /chat` | user | ส่งคำถาม (รองรับ multipart + file) |
| `GET /chat/sessions` | user | รายการ session |
| `GET /chat/sessions/{id}` | user | ข้อความใน session |
| `PATCH /chat/sessions/{id}` | user | เปลี่ยนชื่อ session |
| `PATCH /chat/sessions/{id}/save` | user | toggle บันทึก session |
| `DELETE /chat/sessions/{id}` | user | ลบ session |
| `GET /chat/search` | user | ค้นหาข้อความในประวัติ |
| `GET /chat/sessions/{id}/export` | user | ส่งออก CSV |
| `GET /admin/pending` | admin | คำถามรอตอบ |
| `POST /admin/pending/{id}/answer` | admin | ตอบ → เข้า Knowledge Base |
| `POST /admin/pending/{id}/ignore` | admin | ข้าม |
| `GET /admin/knowledge` | admin | ดู Knowledge Base |
| `POST /admin/knowledge` | admin | เพิ่ม Q&A |
| `DELETE /admin/knowledge/{id}` | admin | ลบ |
| `GET /admin/chat-history` | admin | ประวัติแชททุก user |
| `GET /admin/chat-history/export/all` | admin | ส่งออก CSV ทั้งหมด |

ดู interactive docs ที่ **http://localhost:8000/docs** (Swagger UI)

---

## Environment Variables (Backend)

| ตัวแปร | Default | คำอธิบาย |
|---|---|---|
| `OPENAI_API_KEY` | — | ★ API key จาก platform.openai.com |
| `JWT_SECRET` | — | ★ Random string ≥ 32 chars |
| `JWT_EXPIRES_HOURS` | `8` | อายุ JWT token (ชั่วโมง) |
| `DB_PATH` | `./data/chatbot.db` | path ไฟล์ SQLite |
| `SIMILARITY_THRESHOLD` | `0.75` | ต่ำ = ตอบกว้างขึ้น / สูง = เข้มขึ้น |
| `LLM_MODEL` | `gpt-4o-mini` | เปลี่ยนเป็น `gpt-4o` เพื่อคุณภาพดีขึ้น |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | OpenAI embedding model |
| `EMBEDDING_DIM` | `1024` | ต้องตรงกับ schema ใน db.py |
| `CORS_ORIGIN` | `http://localhost:3002` | URL ของ frontend |
| `UPLOAD_DIR` | `./data/uploads` | โฟลเดอร์เก็บไฟล์แนบ |

---

## ค่าใช้จ่ายโดยประมาณ

สมมติ 100 user × 10 query/วัน × 22 วัน = 22,000 query/เดือน

| ทาง | ค่าใช้จ่าย/เดือน |
|---|---|
| ChatGPT Pro (100 คน) | ~$2,000 (~70,000 บาท) |
| ระบบนี้ (gpt-4o-mini API) | ~$10–15 |

---

## สิ่งที่ต้องแก้ไขก่อน Production

> เพื่อนที่รับโปรเจกต์ต่อ กรุณาแก้ปัญหาเหล่านี้ก่อน deploy จริง

- [ ] **`llm.py`** — แก้ชื่อ model ที่ไม่มีจริง: `gpt-4.1-mini` และ `gpt-5-mini` → เปลี่ยนเป็น `gpt-4o` หรือ `gpt-4o-mini`
- [ ] **`auth.py`** — เพิ่ม startup check ว่า `JWT_SECRET` ถูกเปลี่ยนจาก default แล้ว
- [ ] **`attachments.py`** — เพิ่ม path validation ใน `get_attachment()` ป้องกัน path traversal
- [ ] **`main.py`** — เพิ่ม rate limiting บน `/chat` และ file upload endpoints
- [ ] เพิ่ม structured logging สำหรับ audit trail (admin actions, errors)
- [ ] เพิ่ม embedding cache เพื่อลด OpenAI API cost
- [ ] เพิ่ม Dockerfile + docker-compose สำหรับ deploy บน server

---

## การรัน Automated Tests (Playwright)

```powershell
cd frontend
npx playwright test              # รัน tests ทั้งหมด + เปิด HTML report
npx playwright test tests/login.spec.ts   # รันเฉพาะ login
npx playwright show-report       # เปิด report ครั้งถัดไป
```

**Tests ที่มี (9 tests):**

| ไฟล์ | ครอบคลุม |
|---|---|
| `tests/login.spec.ts` | Login ปกติ, password ผิด, username ไม่มีในระบบ |
| `tests/chat.spec.ts` | ส่งข้อความ, แชทใหม่, ลบแชท |
| `tests/auth.spec.ts` | เข้า /chat ไม่มี token → redirect, ล้าง token → redirect |

> **หมายเหตุ:** ต้องรัน backend + frontend ก่อนรัน tests และต้องมี user `admin` / password `admin1234` ในระบบ

---

## Troubleshooting

| ปัญหา | วิธีแก้ |
|---|---|
| `python` ไม่ใช่คำสั่ง | ตอนลง Python ติ๊ก "Add to PATH" หรือ restart PowerShell |
| Port 3002 / 8000 ถูกใช้อยู่ | แก้ port ใน `start-backend.ps1` / `start-frontend.ps1` + `backend/.env` (`CORS_ORIGIN`) |
| `OPENAI_API_KEY` ใช้ไม่ได้ | เช็คที่ platform.openai.com → API keys ว่า valid และมี credit |
| `script execution disabled` | เปิด PowerShell (Admin) → `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `sqlite-vec` error ตอน import | ตรวจว่าใช้ Python 3.12 และ install ใน venv ที่ถูกต้อง |
| Frontend ไม่เชื่อมต่อ backend | ตรวจ `NEXT_PUBLIC_API_BASE` ใน `frontend/.env.local` และ `CORS_ORIGIN` ใน `backend/.env` |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS |
| Backend | Python 3.12, FastAPI, Uvicorn |
| Database | SQLite, sqlite-vec (vector search) |
| AI | OpenAI API (gpt-4o-mini, text-embedding-3-large) |
| Auth | JWT (python-jose), bcrypt (passlib) |
| File Processing | PyMuPDF, python-docx, openpyxl, python-pptx |
