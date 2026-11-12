export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TOKEN_KEY = "chatbot_token";
const ROLE_KEY = "chatbot_role";
const USERNAME_KEY = "chatbot_username";
// Set for a browser that came in from the MI portal (SSO): signing in again —
// after "ออกจากระบบ", an expired session, or a sign-out in another tab — goes
// back there (backend MI_PORTAL_URL) instead of our login page. Kept after
// sign-out on purpose; a login on our own page removes it.
const RETURN_KEY = "chatbot_return_url";

export function saveAuth(token: string, role: string, username: string, returnUrl?: string | null) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
  localStorage.setItem(USERNAME_KEY, username);
  if (returnUrl && /^https?:\/\//i.test(returnUrl)) localStorage.setItem(RETURN_KEY, returnUrl);
  else localStorage.removeItem(RETURN_KEY);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(USERNAME_KEY);
}

// Where a signed-out visitor signs in: the MI portal if this browser came
// from it, else our login page ("/login").
export function loginDestination(): string {
  try {
    return localStorage.getItem(RETURN_KEY) || "/login";
  } catch {
    return "/login";
  }
}

// Sign out; returns where to go next (loginDestination).
export function signOut(): string {
  const dest = loginDestination();
  clearAuth();
  return dest;
}

// Not (or no longer) signed in — a 401, no token: clear what's left and go
// sign in. Used instead of showing the raw {"detail": ...} error.
export function goSignIn(): void {
  if (typeof window === "undefined") return;
  const dest = signOut();
  if (dest.startsWith("/") && window.location.pathname.startsWith(dest)) return;
  window.location.replace(dest);
}

// A response that says the session is gone → sign in again. A message typed
// but not sent yet is kept first (restored by the chat page after sign-in).
const DRAFT_KEY = "chatbot_draft";
let draftSource: (() => string) | null = null;
export function setDraftSource(fn: (() => string) | null): void {
  draftSource = fn;
}
export function keepDraft(text: string): void {
  try {
    if (text.trim()) localStorage.setItem(DRAFT_KEY, text);
  } catch {
    /* storage unavailable */
  }
}
export function takeDraft(): string {
  try {
    const t = localStorage.getItem(DRAFT_KEY) ?? "";
    localStorage.removeItem(DRAFT_KEY);
    return t;
  } catch {
    return "";
  }
}

function checkAuth(res: Response): void {
  if (res.status === 401) {
    if (draftSource) keepDraft(draftSource());
    goSignIn();
  }
}

// What a failed request tells the user — Thai, never raw JSON or an HTML
// error page. The server's own Thai `detail` is used when it sent one.
export async function errorMessage(res: Response): Promise<string> {
  let detail = "";
  try {
    const text = await res.text();
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = "ข้อมูลที่ส่งไม่ถูกต้อง";
    } catch {
      /* not JSON (an HTML page from a proxy, plain text…) */
    }
  } catch {
    /* body unreadable */
  }
  const thai = /[฀-๿]/.test(detail);
  if (res.status === 429) return thai ? detail : "ส่งคำขอถี่เกินไป กรุณารอสักครู่ (ประมาณ 1 นาที) แล้วลองใหม่";
  if (res.status === 413) return "ไฟล์ใหญ่เกินกว่าที่ระบบรับได้ (สูงสุด 100MB ต่อไฟล์)";
  if (res.status === 401) return "หมดเวลาการเข้าสู่ระบบ กรุณาเข้าสู่ระบบใหม่";
  if (res.status >= 502 && res.status <= 504) return "ระบบไม่ตอบสนองชั่วคราว กรุณาลองใหม่อีกครั้งในอีกสักครู่";
  if (thai) return detail;
  if (res.status >= 500) return "ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง";
  if (res.status === 404) return "ไม่พบข้อมูลที่ต้องการ (อาจถูกลบไปแล้ว)";
  if (res.status === 403) return "คุณไม่มีสิทธิ์ทำรายการนี้";
  return `ทำรายการไม่สำเร็จ (รหัส ${res.status})`;
}

// The connection dropped mid-answer. The server usually finishes and saves
// the answer, but not if it restarts (a deploy) at that moment — so no promise.
export const CONNECTION_LOST_NOTE =
  "การเชื่อมต่อขาดระหว่างตอบ — ระบบจะพยายามตอบต่อให้จนจบ เปิดแชทนี้อีกครั้งในอีกสักครู่เพื่อดูคำตอบ (ถ้ายังไม่มีคำตอบ ให้ถามใหม่อีกครั้ง)";

// A fetch that never got an answer (offline, connection dropped) → Thai text.
export function friendlyError(e: unknown): string {
  if (e instanceof TypeError || (e instanceof Error && /fetch|network|load failed/i.test(e.message)))
    return "เชื่อมต่อระบบไม่ได้ กรุณาตรวจสอบอินเทอร์เน็ตแล้วลองใหม่";
  return e instanceof Error && e.message ? e.message : "เกิดข้อผิดพลาด กรุณาลองใหม่";
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getRole(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ROLE_KEY);
}

export function getUsername(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(USERNAME_KEY);
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    checkAuth(res);
    throw new Error(await errorMessage(res));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// The role in this browser is the one from sign-in; the server's is current
// (an admin may have been demoted since) — keep the server's.
export function rememberRole(role: string | null | undefined): void {
  if (typeof window === "undefined" || !role) return;
  try {
    localStorage.setItem(ROLE_KEY, role);
  } catch {
    /* storage unavailable */
  }
}

export async function refreshRole(): Promise<string | null> {
  const me = await api<{ role?: string }>("/auth/me");
  rememberRole(me.role);
  return me.role ?? null;
}

// Public sign-in settings: the MI portal an SSO user goes back to (or null).
export async function getAuthConfig(): Promise<{ mi_portal_url: string | null }> {
  const res = await fetch(`${API_BASE}/auth/config`);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

// Same limit as the backend (main.MAX_MESSAGE_CHARS): a longer paste is
// refused before sending, with a hint to attach it as a file instead.
export const MAX_MESSAGE_CHARS = 120_000;
export function messageTooLong(text: string): string | null {
  if (text.length <= MAX_MESSAGE_CHARS) return null;
  return `ข้อความยาวเกินไป (${text.length.toLocaleString()} ตัวอักษร — สูงสุด ${MAX_MESSAGE_CHARS.toLocaleString()}) กรุณาบันทึกเป็นไฟล์ (Word, PDF หรือ .txt) แล้วแนบไฟล์แทนการวาง`;
}

// One id per question asked: หยุด names the answer to stop by it, and a
// reload or closed tab (no หยุด) lets the server finish the answer.
export function newClientId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  }
}

// หยุด: tell the server to stop that answer (keepalive: still sent if the page
// is closing). Never throws — aborting the request is the fallback.
export async function stopTurn(clientId: string): Promise<void> {
  const token = getToken();
  try {
    await fetch(`${API_BASE}/chat/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ client_id: clientId }),
      keepalive: true,
    });
  } catch {
    /* offline — the aborted request still ends it on old servers */
  }
}

export async function login(username: string, password: string) {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error("เข้าสู่ระบบไม่สำเร็จ");
  return res.json() as Promise<{ access_token: string; role: string; username: string }>;
}

// Voice input: send a recorded audio blob to be transcribed to text.
export async function transcribeAudio(blob: Blob): Promise<string> {
  const fd = new FormData();
  const ext = blob.type.includes("mp4") ? "mp4" : blob.type.includes("ogg") ? "ogg" : "webm";
  fd.append("audio", blob, `voice.${ext}`);
  const token = getToken();
  const res = await fetch(`${API_BASE}/transcribe`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const b = await res.json();
      detail = typeof b?.detail === "string" ? b.detail : "";
    } catch {
      detail = "";
    }
    throw new Error(detail || "ถอดเสียงไม่สำเร็จ");
  }
  const data = (await res.json()) as { text: string };
  return data.text ?? "";
}

// The SSO exchange normally answers in well under a second. Without a timeout
// a hung backend leaves the /sso page spinning forever with no error.
const SSO_TIMEOUT_MS = 20_000;

// Exchange an MI-issued SSO token (from the central MI portal) for our own
// session. The /sso page calls this with the ?token= it received from MI.
export async function miSsoLogin(token: string) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), SSO_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/auth/mi-sso`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
      signal: ctrl.signal,
    });
  } catch {
    // Timed out, or the backend is unreachable.
    throw new Error(
      "ระบบ SiriGPT ไม่ตอบสนอง กรุณาลองเข้าผ่านเมนู MI อีกครั้งในอีกสักครู่ หรือแจ้งผู้ดูแลระบบ",
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      detail = "";
    }
    throw new Error(detail || "เข้าสู่ระบบผ่าน MI ไม่สำเร็จ");
  }
  return res.json() as Promise<{ access_token: string; role: string; username: string; return_url?: string | null }>;
}

export async function sendChat(opts: {
  message: string;
  sessionId: number | null;
  files: File[];
  mode?: "normal" | "company";
  signal?: AbortSignal;
  clientId?: string;
}): Promise<{
  answer: string;
  source: string;
  session_id: number;
  session_title: string;
  attachments: Array<{ id: number; filename: string; content_type: string; size_bytes: number; generated?: boolean }>;
  // RAG hits carry the source document so the UI can offer a "📎 ดาวน์โหลด
  // เอกสารต้นฉบับ" button. Null for LLM-only answers and blocked questions.
  source_knowledge_id?: number | null;
  source_file?: string | null;
  // Per-session quota — frontend renders "X/20" counter from these.
  turn_count?: number | null;
  turn_limit?: number | null;
  // chat_history row id of this answer, for attaching 👍/👎 feedback.
  message_id?: number | null;
}> {
  const fd = new FormData();
  fd.append("message", opts.message);
  if (opts.sessionId !== null) fd.append("session_id", String(opts.sessionId));
  if (opts.mode && opts.mode !== "normal") fd.append("mode", opts.mode);
  for (const f of opts.files) fd.append("files", f, f.name);
  if (opts.clientId) fd.append("client_id", opts.clientId);

  const token = getToken();
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
    signal: opts.signal,
  });
  if (!res.ok) {
    checkAuth(res);
    throw new Error(await errorMessage(res));
  }
  return res.json();
}

// /chat/stream refused to start (status): 501 → the caller uses /chat instead;
// anything else (429, 400…) is shown — sending it again won't help.
export class StreamStartError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type StreamDone = {
  type: "done";
  source: string;
  session_id: number;
  session_title: string;
  message_id: number | null;
  source_knowledge_id?: number | null;
  source_file?: string | null;
  turn_count?: number | null;
  turn_limit?: number | null;
  // Present only for answers that produced files — AI images (source=
  // "image_gen") or Code Interpreter output (source="code", generated=true):
  // rendered inline on the bot bubble.
  attachments?: Array<{
    id: number;
    filename: string;
    content_type: string;
    size_bytes: number;
    generated?: boolean;
  }>;
};

// Streaming twin of sendChat for text-only questions. Calls onDelta for each
// token chunk and onDone with the final metadata. Throws if the request can't
// start (e.g. 501 on sqlite, 4xx) so the caller can fall back to sendChat.
export async function sendChatStream(
  opts: {
    message: string;
    sessionId: number | null;
    mode?: "normal" | "company";
    signal?: AbortSignal;
    clientId?: string;
  },
  handlers: {
    onDelta: (text: string) => void;
    onDone: (meta: StreamDone) => void;
    onError: (detail: string, code?: string) => void;
    // Progress signal before any token (e.g. "generating_image" during the
    // slow image-generation step). Optional.
    onStatus?: (state: string) => void;
    // The chat the answer goes into, sent before the answer starts — a new
    // chat that is stopped mid-answer is still that chat. Optional.
    onSession?: (s: { session_id: number; session_title: string }) => void;
  },
): Promise<void> {
  const fd = new FormData();
  fd.append("message", opts.message);
  if (opts.sessionId !== null) fd.append("session_id", String(opts.sessionId));
  if (opts.mode && opts.mode !== "normal") fd.append("mode", opts.mode);
  if (opts.clientId) fd.append("client_id", opts.clientId);

  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    checkAuth(res);
    throw new StreamStartError(res.status, await errorMessage(res));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  const handleLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let evt: Record<string, unknown>;
    try {
      evt = JSON.parse(trimmed);
    } catch {
      return; // ignore malformed partial lines
    }
    if (evt.type === "delta") handlers.onDelta(String(evt.v ?? ""));
    else if (evt.type === "status") handlers.onStatus?.(String(evt.state ?? ""));
    else if (evt.type === "session")
      handlers.onSession?.({ session_id: Number(evt.session_id), session_title: String(evt.session_title ?? "") });
    else if (evt.type === "done") handlers.onDone(evt as unknown as StreamDone);
    else if (evt.type === "error")
      handlers.onError(String(evt.detail ?? "เกิดข้อผิดพลาด"), evt.code as string | undefined);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      handleLine(buf.slice(0, nl));
      buf = buf.slice(nl + 1);
    }
  }
  if (buf.trim()) handleLine(buf);
}

export function attachmentUrl(id: number): string {
  return `${API_BASE}/attachments/${id}`;
}

export async function sendFeedback(
  messageId: number,
  vote: "up" | "down",
  reason?: string,
): Promise<void> {
  await api(`/chat/feedback`, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId, vote, reason: reason ?? null }),
  });
}

export type ShareUser = { id: number; username: string };

export function listShareableUsers(): Promise<ShareUser[]> {
  return api<ShareUser[]>(`/users/list`);
}

export function getSessionRecipients(sessionId: number): Promise<number[]> {
  return api<{ recipient_ids: number[] }>(
    `/chat/sessions/${sessionId}/recipients`,
  ).then((d) => d.recipient_ids);
}

// Targeted share: pass the recipient user ids. Empty list = unshare.
export async function shareSession(
  sessionId: number,
  recipientIds: number[],
): Promise<{ shared: boolean; token: string | null; url: string | null }> {
  const data = await api<{
    shared: boolean;
    token: string | null;
    path: string | null;
  }>(`/chat/sessions/${sessionId}/share`, {
    method: "POST",
    body: JSON.stringify({ recipient_ids: recipientIds }),
  });
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return {
    shared: data.shared,
    token: data.token,
    url: data.path ? `${origin}${data.path}` : null,
  };
}

// ── V4.2 long-term memory (the user's own; available only when MEMORY_MODE is on) ──
export type MemoryItem = { id: number; content: string; created_at: string | null };
export type MemoryState = {
  available: boolean;
  enabled: boolean;
  items: MemoryItem[];
  limit: number;
};

export function getMemory(): Promise<MemoryState> {
  return api<MemoryState>("/memory");
}

// Throws an Error whose message is the server's Thai reason (e.g. a password isn't kept).
export async function addMemory(content: string): Promise<string> {
  try {
    const r = await api<{ message: string }>("/memory", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    return r.message;
  } catch (e) {
    let msg = e instanceof Error ? e.message : String(e);
    try {
      msg = JSON.parse(msg).detail ?? msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
}

export async function deleteMemory(id: number): Promise<void> {
  await api(`/memory/${id}`, { method: "DELETE" });
}

export async function clearMemory(): Promise<void> {
  await api("/memory", { method: "DELETE" });
}

export async function setMemoryEnabled(enabled: boolean): Promise<void> {
  await api("/memory/settings", { method: "PUT", body: JSON.stringify({ enabled }) });
}

export async function revokeSessionShare(sessionId: number): Promise<void> {
  await api(`/chat/sessions/${sessionId}/share`, { method: "DELETE" });
}

export async function forkSharedSession(
  token: string,
): Promise<{ session_id: number }> {
  return api<{ session_id: number }>(`/chat/shared/${token}/fork`, {
    method: "POST",
  });
}

export async function exportAnswerPdf(opts: {
  content: string;
  title?: string;
  userQuestion?: string;
  filename?: string;
}): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/export-pdf`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      content: opts.content,
      title: opts.title,
      user_question: opts.userQuestion,
    }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  triggerDownload(await res.blob(), opts.filename ?? "Sirivatana_chat.pdf");
}

export async function exportAnswerXlsx(opts: {
  content: string;
  title?: string;
  filename?: string;
}): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/export-xlsx`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content: opts.content, title: opts.title }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  triggerDownload(await res.blob(), opts.filename ?? "Sirivatana_table.xlsx");
}

export async function exportAnswerDocx(opts: {
  content: string;
  title?: string;
  filename?: string;
}): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/export-docx`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content: opts.content, title: opts.title }),
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  triggerDownload(await res.blob(), opts.filename ?? "Sirivatana_chat.docx");
}

export async function downloadSourceFile(knowledgeId: number): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/source/${knowledgeId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  // Preserve the server-provided filename so Thai-named docs land with the
  // right name on the user's disk instead of "download.docx".
  const cd = res.headers.get("Content-Disposition") || "";
  const filename = parseFilenameFromContentDisposition(cd) || "source-document";
  triggerDownload(await res.blob(), filename);
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a beat to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function parseFilenameFromContentDisposition(cd: string): string | null {
  // RFC 5987 filename* takes precedence for non-ASCII names (Thai!).
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      // fall through
    }
  }
  const ascii = /filename="?([^";]+)"?/i.exec(cd);
  return ascii ? ascii[1] : null;
}

export async function fetchAttachmentBlobUrl(id: number): Promise<string> {
  const token = getToken();
  const res = await fetch(attachmentUrl(id), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) throw new Error("download failed");
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ── V5 chat UI ──────────────────────────────────────────────────────────────

// Which chat page this user gets (backend UI_V5_MODE). Any failure → classic.
// role: the current one on the server (see rememberRole).
export async function getUiConfig(): Promise<{ ui_v5: boolean; role?: string }> {
  return api<{ ui_v5: boolean; role?: string }>("/ui/config");
}

// An attachment fetch that says WHY it failed (gone / not yours / other), so a
// file card can show a useful message instead of failing silently.
export class AttachmentError extends Error {
  constructor(public status: number) {
    super(
      status === 404 || status === 410
        ? "ไฟล์นี้ไม่มีแล้ว (อาจถูกลบหรือหมดอายุ)"
        : status === 403
          ? "คุณไม่มีสิทธิ์เปิดไฟล์นี้"
          : "เปิดไฟล์ไม่สำเร็จ ลองใหม่อีกครั้ง",
    );
  }
}

export async function fetchAttachment(id: number): Promise<Blob> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(attachmentUrl(id), {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
  } catch {
    throw new AttachmentError(0);
  }
  if (!res.ok) {
    checkAuth(res);
    throw new AttachmentError(res.status);
  }
  return res.blob();
}

export function saveBlob(blob: Blob, filename: string) {
  triggerDownload(blob, filename);
}

export async function register(username: string, password: string) {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error("สมัครไม่สำเร็จ (อาจมีชื่อผู้ใช้นี้แล้ว)");
  return res.json() as Promise<{ ok: boolean; role: string }>;
}


// ---------------------------------------------------------------------------
// Document translation (Phase 1) — upload a PDF, translate in background,
// poll status, download the Thai Word file.
// ---------------------------------------------------------------------------
export type TranslateJob = {
  id: string;
  filename: string;
  status: "queued" | "running" | "done" | "error";
  done: number;
  total: number;
  translated_pages: number;
  exceeds_cap: boolean;
  max_pages: number;
  review_flagged: number;
  cost_usd: number;
  pdf: string | null;
  error: string | null;
};

export async function startTranslation(file: File): Promise<TranslateJob> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  const token = getToken();
  const res = await fetch(`${API_BASE}/translate/start`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  return res.json();
}

export function getTranslationStatus(jobId: string): Promise<TranslateJob> {
  return api<TranslateJob>(`/translate/status/${jobId}`);
}

export function listTranslationJobs(): Promise<TranslateJob[]> {
  return api<TranslateJob[]>(`/translate/jobs`);
}

// FileResponse needs the Bearer token, so a plain <a href> won't authenticate.
// Fetch with auth, then trigger a browser download via an object URL.
export async function downloadTranslation(
  jobId: string,
  fmt: "docx" | "pdf",
  filename: string,
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/translate/download/${jobId}?fmt=${fmt}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


export type TranslateReview = {
  pages: { page: number; issues: string[] }[];
  raw: string;
};

export function getTranslationReview(jobId: string): Promise<TranslateReview> {
  return api<TranslateReview>(`/translate/review/${jobId}`);
}


export function deleteTranslation(jobId: string): Promise<void> {
  return api(`/translate/jobs/${jobId}`, { method: "DELETE" });
}
