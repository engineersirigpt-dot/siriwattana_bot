export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TOKEN_KEY = "chatbot_token";
const ROLE_KEY = "chatbot_role";
const USERNAME_KEY = "chatbot_username";

export function saveAuth(token: string, role: string, username: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
  localStorage.setItem(USERNAME_KEY, username);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(USERNAME_KEY);
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
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
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

export async function sendChat(opts: {
  message: string;
  sessionId: number | null;
  files: File[];
  mode?: "normal" | "company";
  signal?: AbortSignal;
}): Promise<{
  answer: string;
  source: string;
  session_id: number;
  session_title: string;
  attachments: Array<{ id: number; filename: string; content_type: string; size_bytes: number }>;
  // RAG hits carry the source document so the UI can offer a "📎 ดาวน์โหลด
  // เอกสารต้นฉบับ" button. Null for LLM-only answers and blocked questions.
  source_knowledge_id?: number | null;
  source_file?: string | null;
  // Per-session quota — frontend renders "X/20" counter from these.
  turn_count?: number | null;
  turn_limit?: number | null;
}> {
  const fd = new FormData();
  fd.append("message", opts.message);
  if (opts.sessionId !== null) fd.append("session_id", String(opts.sessionId));
  if (opts.mode && opts.mode !== "normal") fd.append("mode", opts.mode);
  for (const f of opts.files) fd.append("files", f, f.name);

  const token = getToken();
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
    signal: opts.signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export function attachmentUrl(id: number): string {
  return `${API_BASE}/attachments/${id}`;
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
    const text = await res.text();
    throw new Error(text || "สร้าง PDF ไม่สำเร็จ");
  }
  triggerDownload(await res.blob(), opts.filename ?? "Sirivatana_chat.pdf");
}

export async function downloadSourceFile(knowledgeId: number): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/source/${knowledgeId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "ดาวน์โหลดเอกสารต้นฉบับไม่สำเร็จ");
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
