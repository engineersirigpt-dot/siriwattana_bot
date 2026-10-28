// Mock of the backend endpoints the chat page uses — for the V5 UI tests (no
// OpenAI, no database). Plain Node, no dependencies. State lives in memory and
// can be reset/changed by tests through /__mock/*.
//
//   node e2e-ui/mock-api.mjs            (port 4010, or MOCK_PORT)

import http from "node:http";
import zlib from "node:zlib";

const PORT = Number(process.env.MOCK_PORT || 4010);

// ── tiny file makers ────────────────────────────────────────────────────────
const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function png(w, h, [r, g, b], stripe = [255, 255, 255]) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 3 + 1)] = 0;
    for (let x = 0; x < w; x++) {
      const bar = x % 40 < 28 && y > h * (0.3 + 0.5 * ((x / 40) % 3) / 3);
      const [cr, cg, cb] = bar ? [r, g, b] : stripe;
      const o = y * (w * 3 + 1) + 1 + x * 3;
      raw[o] = cr; raw[o + 1] = cg; raw[o + 2] = cb;
    }
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
function pdf(text) {
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    null,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  const stream = `BT /F1 24 Tf 72 760 Td (${text}) Tj ET`;
  objs[3] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  let out = "%PDF-1.4\n";
  const offs = [];
  objs.forEach((o, i) => { offs.push(out.length); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offs.map((o) => String(o).padStart(10, "0") + " 00000 n \n").join("");
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const FILES = {
  501: { filename: "ยอดขาย_Q1.xlsx", content_type: XLSX, owner: 1, body: Buffer.from("PK fake xlsx"), generated: true },
  502: { filename: "รายงานยอดขาย.docx", content_type: DOCX, owner: 1, body: Buffer.from("PK fake docx"), generated: true },
  503: { filename: "รายงานยอดขาย_ตัวอย่าง.png", content_type: "image/png", owner: 1, body: png(640, 420, [91, 44, 159]), generated: true },
  504: { filename: "สรุปผล.pdf", content_type: "application/pdf", owner: 1, body: pdf("Sales summary Q1"), generated: true },
  505: { filename: "ลูกค้า.csv", content_type: "text/csv", owner: 1, body: Buffer.from("เดือน,ยอดขาย,หมายเหตุ\nม.ค.,\"41,650\",สูงสุด\nก.พ.,\"33,400\",\nมี.ค.,\"22,600\",ต่ำสุด\n"), generated: true },
  506: { filename: "กราฟยอดขาย.png", content_type: "image/png", owner: 1, body: png(720, 400, [124, 58, 237]), generated: true },
  507: { filename: "ไฟล์เก่า_หมดอายุ.xlsx", content_type: XLSX, owner: 1, gone: true, generated: true },
  508: { filename: "ของคนอื่น.pptx", content_type: PPTX, owner: 9, body: Buffer.from("PK"), generated: true },
  509: { filename: "โปสเตอร์.png", content_type: "image/png", owner: 1, body: png(300, 300, [234, 88, 12]) },
  510: { filename: "ใบเสนอราคา.pdf", content_type: "application/pdf", owner: 1, body: pdf("Quotation") },
};
const meta = (id) => {
  const f = FILES[id];
  return { id: Number(id), filename: f.filename, content_type: f.content_type, size_bytes: f.body ? f.body.length : 20480, generated: !!f.generated };
};

const RICH_ANSWER = [
  "## สรุปยอดขายไตรมาส 1/2569",
  "ยอดขายรวม **97,650 บาท** ลดลงต่อเนื่องจาก ม.ค. ถึง มี.ค. ดูรายละเอียดที่ https://example.com/reports/sales/2569/q1/very-long-path-that-should-wrap-instead-of-overflowing-the-chat-column-0123456789",
  "",
  "| เดือน | กล่องลูกฟูก | ฉลากสินค้า | งานรวมเล่ม | งานพิมพ์ปฏิทิน | งานพิมพ์โบรชัวร์ | ค่าขนส่ง | รวม (บาท) |",
  "|---|---:|---:|---:|---:|---:|---:|---:|",
  "| ม.ค. | 12,000 | 11,250 | 10,000 | 4,000 | 3,000 | 1,400 | 41,650 |",
  "| ก.พ. | 8,400 | 13,650 | 4,000 | 3,500 | 2,800 | 1,050 | 33,400 |",
  "| มี.ค. | 10,800 | 7,200 | 3,000 | 800 | 600 | 200 | 22,600 |",
  "",
  "```python",
  "import pandas as pd",
  "df = pd.read_excel('ยอดขาย_Q1.xlsx', sheet_name=None)",
  "total = sum(s['ยอดขาย'].sum() for s in df.values())",
  "print(f'{total:,.0f}')",
  "```",
  "",
  "- ม.ค. สูงสุด",
  "- มี.ค. ต่ำสุด — ควรเร่งแผนกระตุ้นยอดขาย",
].join("\n");

function freshState() {
  const sessions = [
    { id: 101, title: "รายงานยอดขายไตรมาส 1", is_saved: 1 },
    { id: 102, title: "แชทยาว 18 คำถามเรื่องการผลิต", is_saved: 0 },
    ...Array.from({ length: 24 }, (_, i) => ({ id: 200 + i, title: [`ขั้นตอนเบิกค่าเดินทาง ${i + 1}`, `ร่างอีเมลแจ้งลูกค้า ${i + 1}`, `สรุปเอกสาร ISO ${i + 1}`][i % 3], is_saved: 0 })),
  ].map((s) => ({ created_at: "2026-10-28T08:00:00", updated_at: "2026-10-28T09:00:00", message_count: 2, last_preview: null, ...s }));
  const messages = {
    101: [
      { id: 7001, question: "สรุปยอดขายไตรมาส 1 พร้อมตารางและไฟล์", answer: RICH_ANSWER, source: "document",
        attachments: [501, 502, 503, 504, 505, 506, 507, 508].map(meta) },
      { id: 7002, question: "ช่วยสรุปไฟล์นี้", answer: "ไฟล์โปสเตอร์และใบเสนอราคาเรียบร้อยดีค่ะ", source: "files",
        attachments: [509, 510].map((id) => ({ ...meta(id), generated: false })) },
    ],
    102: Array.from({ length: 18 }, (_, i) => ({
      id: 8000 + i, question: `คำถามที่ ${i + 1} เรื่องการวางแผนการผลิต`, source: "llm",
      answer: `### คำตอบที่ ${i + 1}\n` + Array.from({ length: 6 }, (_, j) => `- ประเด็นที่ ${j + 1}: วางแผนกำลังการผลิตให้สอดคล้องกับคำสั่งซื้อและสต็อกวัตถุดิบ เพื่อลดของเสียและเวลารอ`).join("\n"),
    })),
  };
  for (let i = 0; i < 24; i++) messages[200 + i] = [{ id: 9000 + i, question: "คำถามทดสอบ", answer: "คำตอบทดสอบ", source: "llm" }];
  return {
    ui: { 1: true, 2: false },
    memory: { 1: { available: true, enabled: true, items: [{ id: 1, content: "ฉันอยู่ฝ่ายผลิต กะเช้า", created_at: null }] }, 2: { available: false, enabled: false, items: [] } },
    sessions, messages, nextSid: 900, nextMid: 7100, nextAtt: 600, requests: [],
  };
}
let S = freshState();

const USERS = { "tok-admin": { id: 1, username: "admin", role: "admin" }, "tok-staff": { id: 2, username: "staff", role: "user" } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function send(res, status, body, type = "application/json") {
  res.writeHead(status, { "Content-Type": type, "Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "Content-Disposition" });
  res.end(type === "application/json" ? JSON.stringify(body) : body);
}
async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}
function formFields(buf, contentType) {
  const m = /boundary=(.+)$/.exec(contentType || "");
  const out = { fields: {}, files: [] };
  if (!m) return out;
  for (const part of buf.toString("latin1").split("--" + m[1])) {
    const name = /name="([^"]+)"/.exec(part)?.[1];
    if (!name) continue;
    const filename = /filename="([^"]*)"/.exec(part)?.[1];
    const ctype = /Content-Type: ([^\r\n]+)/i.exec(part)?.[1] || "";
    const body = part.split("\r\n\r\n").slice(1).join("\r\n\r\n").replace(/\r\n$/, "");
    if (filename !== undefined) out.files.push({ filename: Buffer.from(filename, "latin1").toString("utf8"), content_type: ctype, size: body.length });
    else out.fields[name] = Buffer.from(body, "latin1").toString("utf8");
  }
  return out;
}

async function streamAnswer(req, res, user, message, sid) {
  res.writeHead(200, { "Content-Type": "application/x-ndjson", "Access-Control-Allow-Origin": "*" });
  let closed = false;
  req.on("close", () => (closed = true));
  const emit = (o) => !closed && res.write(JSON.stringify(o) + "\n");
  const done = (extra = {}) => {
    const sessionId = sid || S.nextSid++;
    if (!S.sessions.find((s) => s.id === sessionId)) S.sessions.unshift({ id: sessionId, title: message.slice(0, 40), is_saved: 0, created_at: "", updated_at: "", message_count: 1, last_preview: null });
    emit({ type: "done", source: "llm", session_id: sessionId, session_title: message.slice(0, 40), message_id: S.nextMid++, turn_count: 1, turn_limit: 20, ...extra });
  };
  if (message.includes("ค้นเว็บ")) {
    emit({ type: "status", state: "agent_searching" }); await sleep(700);
    emit({ type: "status", state: "agent_writing" }); await sleep(1200);
  }
  if (message.includes("รูป")) {
    emit({ type: "status", state: "generating_image" }); await sleep(900);
    const id = S.nextAtt++;
    FILES[id] = { filename: "ภาพที่สร้าง.png", content_type: "image/png", owner: user.id, body: png(512, 512, [16, 185, 129]), generated: true };
    emit({ type: "delta", v: "สร้างรูปให้แล้วค่ะ" });
    return done({ source: "image_gen", attachments: [meta(id)] }) || res.end();
  }
  if (message.includes("เอกสาร")) {
    emit({ type: "status", state: "agent_document" }); await sleep(900);
    emit({ type: "delta", v: "ทำรายงานให้แล้วค่ะ\n\n**ตรวจไฟล์ก่อนส่ง**\n- ✅ รายงานยอดขาย.docx — 2 หน้า, ตาราง 1" });
    done({ source: "document", attachments: [meta(502), meta(503)] });
    return res.end();
  }
  const slow = message.includes("ช้า");
  const text = slow ? "คำตอบยาวที่ส่งมาช้า ๆ ".repeat(60) : message.includes("ตาราง") ? RICH_ANSWER : "ได้เลยค่ะ นี่คือคำตอบสำหรับคำถามของคุณ พร้อม **ตัวหนา** และรายการ:\n\n- ข้อหนึ่ง\n- ข้อสอง";
  for (let i = 0; i < text.length && !closed; i += 4) {
    emit({ type: "delta", v: text.slice(i, i + 4) });
    await sleep(slow ? 60 : 8);
  }
  if (!closed) {
    done();
    res.end();
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;
  if (req.method === "OPTIONS") {
    res.writeHead(204, { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE" });
    return res.end();
  }
  if (path === "/health") return send(res, 200, { ok: true });
  if (path === "/__mock/reset") { S = freshState(); return send(res, 200, { ok: true }); }
  if (path === "/__mock/state" && req.method === "POST") {
    const b = JSON.parse((await readBody(req)).toString() || "{}");
    if (b.ui) Object.assign(S.ui, b.ui);
    if (b.memory) Object.assign(S.memory, b.memory);
    return send(res, 200, { ok: true });
  }
  if (path === "/__mock/requests") return send(res, 200, S.requests);

  const user = USERS[(req.headers.authorization || "").replace("Bearer ", "")];
  if (!user) return send(res, 401, { detail: "unauthorized" });
  S.requests.push(`${req.method} ${path}`);

  if (path === "/ui/config") return send(res, 200, { ui_v5: !!S.ui[user.id] });
  if (path === "/auth/me") return send(res, 200, user);
  if (path === "/memory" && req.method === "GET") {
    const m = S.memory[user.id];
    return send(res, 200, m.available ? { available: true, enabled: m.enabled, items: m.items, limit: 50 } : { available: false, enabled: false, items: [], limit: 50 });
  }
  if (path === "/memory" && req.method === "POST") {
    const m = S.memory[user.id];
    if (!m.available) return send(res, 404, { detail: "memory is not available" });
    const { content } = JSON.parse((await readBody(req)).toString());
    if (/รหัสผ่าน|password/i.test(content)) return send(res, 400, { detail: "ไม่บันทึกค่ะ — ข้อความนี้มีรหัสผ่านหรือรหัสลับ" });
    m.items.push({ id: Date.now(), content, created_at: null });
    m.enabled = true;
    return send(res, 200, { ok: true, message: `จำไว้แล้วค่ะ: “${content}”` });
  }
  if (path.startsWith("/memory/") && req.method === "DELETE") {
    const m = S.memory[user.id];
    const id = Number(path.split("/")[2]);
    const before = m.items.length;
    m.items = m.items.filter((i) => i.id !== id);
    return before === m.items.length ? send(res, 404, { detail: "not found" }) : send(res, 200, { ok: true });
  }
  if (path === "/memory" && req.method === "DELETE") { S.memory[user.id].items = []; return send(res, 200, { ok: true }); }
  if (path === "/memory/settings") { S.memory[user.id].enabled = JSON.parse((await readBody(req)).toString()).enabled; return send(res, 200, { ok: true }); }

  if (path === "/chat/sessions" && req.method === "GET") return send(res, 200, user.id === 1 ? S.sessions : []);
  const sm = /^\/chat\/sessions\/(\d+)$/.exec(path);
  if (sm && req.method === "GET") {
    const msgs = user.id === 1 ? S.messages[sm[1]] : undefined;
    if (!msgs) return send(res, 404, { detail: "session not found" });
    if (sm[1] === "102") await sleep(400); // a slower one, to see the loading state
    return send(res, 200, { messages: msgs, mode: "normal", turn_count: msgs.length, shared_token: null });
  }
  if (sm && req.method === "PATCH") {
    const b = JSON.parse((await readBody(req)).toString());
    const s = S.sessions.find((x) => x.id === Number(sm[1]));
    if (s) s.title = b.title;
    return send(res, 200, { ok: true });
  }
  if (sm && req.method === "DELETE") { S.sessions = S.sessions.filter((x) => x.id !== Number(sm[1])); return send(res, 200, { ok: true }); }
  if (/^\/chat\/sessions\/\d+\/save$/.test(path)) {
    const s = S.sessions.find((x) => x.id === Number(path.split("/")[3]));
    if (s) s.is_saved = s.is_saved ? 0 : 1;
    return send(res, 200, { ok: true });
  }
  if (/^\/chat\/sessions\/\d+\/export$/.test(path)) return send(res, 200, "q,a\n", "text/csv");
  if (path === "/chat/shared-sessions") return send(res, 200, [{ id: 301, title: "วิธีใช้เครื่องตัด", user_id: 5, username: "somchai", shared_token: "tok301", updated_at: null, message_count: 2 }]);
  if (path === "/chat/shared/tok301") return send(res, 200, { messages: [{ id: 1, question: "เครื่องตัดใช้ยังไง", answer: "ปรับใบมีดก่อนเสมอ", source: "llm" }], mode: "normal" });
  if (path === "/admin/chat-history") return user.role === "admin"
    ? send(res, 200, [{ id: 401, title: "แชทของพนักงาน A", user_id: 3, username: "staffA", created_at: "", updated_at: "", message_count: 1 }])
    : send(res, 403, { detail: "forbidden" });
  if (path === "/chat/search") {
    const q = (url.searchParams.get("q") || "").toLowerCase();
    const hits = [];
    for (const [sid, msgs] of Object.entries(S.messages)) {
      for (const m of msgs) if ((m.question + m.answer).toLowerCase().includes(q)) {
        hits.push({ id: m.id, session_id: Number(sid), session_title: S.sessions.find((s) => s.id === Number(sid))?.title ?? "", question: m.question, answer: m.answer, asked_at: "" });
      }
    }
    return send(res, 200, user.id === 1 ? hits.slice(0, 20) : []);
  }
  if (path === "/chat/feedback") return send(res, 200, { ok: true });
  const am = /^\/attachments\/(\d+)$/.exec(path);
  if (am) {
    const f = FILES[am[1]];
    if (!f || f.gone) return send(res, 404, { detail: "attachment not found" });
    if (f.owner !== user.id && user.role !== "admin") return send(res, 403, { detail: "forbidden" });
    if (f.owner !== user.id) return send(res, 403, { detail: "forbidden" }); // the mock: even admins can't open file 508
    res.writeHead(200, { "Content-Type": f.content_type, "Access-Control-Allow-Origin": "*" });
    return res.end(f.body);
  }
  if (path === "/chat/stream" && req.method === "POST") {
    const form = formFields(await readBody(req), req.headers["content-type"]);
    return streamAnswer(req, res, user, form.fields.message || "", Number(form.fields.session_id) || null);
  }
  if (path === "/chat" && req.method === "POST") {
    const form = formFields(await readBody(req), req.headers["content-type"]);
    await sleep(500);
    const ups = form.files.map((f) => {
      const id = S.nextAtt++;
      FILES[id] = { filename: f.filename, content_type: f.content_type, owner: user.id, body: Buffer.alloc(f.size) };
      return meta(id);
    });
    const gen = (form.fields.message || "").includes("excel") ? [meta(501)] : [];
    return send(res, 200, {
      answer: "อ่านไฟล์แล้วค่ะ สรุปได้ว่าเอกสารเรียบร้อยดี", source: gen.length ? "code" : "files",
      session_id: Number(form.fields.session_id) || S.nextSid++, session_title: "ไฟล์", attachments: [...ups, ...gen],
      message_id: S.nextMid++, turn_count: 1, turn_limit: 20,
    });
  }
  send(res, 404, { detail: `mock: no route ${req.method} ${path}` });
});

server.listen(PORT, "127.0.0.1", () => console.log(`mock api on http://127.0.0.1:${PORT}`));
