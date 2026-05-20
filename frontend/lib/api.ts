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
}): Promise<{
  answer: string;
  source: string;
  session_id: number;
  session_title: string;
  attachments: Array<{ id: number; filename: string; content_type: string; size_bytes: number }>;
}> {
  const fd = new FormData();
  fd.append("message", opts.message);
  if (opts.sessionId !== null) fd.append("session_id", String(opts.sessionId));
  for (const f of opts.files) fd.append("files", f, f.name);

  const token = getToken();
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: fd,
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
