"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bookmark,
  BookmarkCheck,
  ChevronDown,
  ChevronRight,
  Check,
  ClipboardList,
  Copy,
  Download,
  Eye,
  FileDown,
  FileText,
  Link2,
  Link2Off,
  Loader2,
  Share2,
  Folder,
  FolderOpen,
  ImageIcon,
  LogOut,
  MessageSquare,
  Paperclip,
  Pencil,
  Plus,
  Search,
  Send,
  Settings,
  Square,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  API_BASE,
  api,
  clearAuth,
  downloadSourceFile,
  exportAnswerPdf,
  fetchAttachmentBlobUrl,
  forkSharedSession,
  getRole,
  getToken,
  getUsername,
  revokeSessionShare,
  sendChat,
  shareSession,
} from "@/lib/api";
import { AlertModal, ConfirmModal, PromptModal } from "@/components/Modal";
import { MarkdownMessage } from "@/components/MarkdownMessage";

type Attachment = {
  id: number;
  filename: string;
  content_type: string;
  size_bytes: number;
};

type Msg = {
  role: "user" | "bot";
  text: string;
  source?: string;
  attachments?: Attachment[];
  // For bot messages backed by a KB chunk: lets the UI surface the
  // "📎 ดาวน์โหลดเอกสารต้นฉบับ" button. Optional — LLM-only answers won't
  // have these fields and the button won't render.
  source_knowledge_id?: number | null;
  source_file?: string | null;
  // Carries the matching user question (the previous Msg) so the export
  // PDF can include it in the document header without re-deriving from
  // message order.
  question?: string;
};

type Session = {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_preview: string | null;
  is_saved: number;
};

type TeamSession = {
  id: number;
  title: string;
  user_id: number;
  username: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

// A chat someone in the team explicitly shared. Visible to every signed-in
// user (not just admins). `shared_token` opens the read-only /chat/shared view.
type SharedTeamSession = {
  id: number;
  title: string;
  user_id: number;
  username: string;
  shared_token: string;
  updated_at: string | null;
  message_count: number;
};

type SearchHit = {
  id: number;
  session_id: number;
  session_title: string;
  question: string;
  answer: string;
  asked_at: string;
};

const OFFICE_MIMES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]);

const TEXT_EXTS = new Set([
  ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
  ".ini", ".env", ".cfg", ".conf", ".toml",
  ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx",
  ".html", ".htm", ".css", ".scss", ".sass",
  ".sql", ".yaml", ".yml", ".xml",
  ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
  ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs",
  ".rb", ".php", ".kt", ".swift", ".dart", ".lua", ".r", ".scala",
  ".vue", ".svelte", ".gradle", ".pl", ".pm",
]);

const FILE_ACCEPT_ATTR = [
  "image/*",
  ".pdf", ".docx", ".xlsx", ".pptx",
  ...Array.from(TEXT_EXTS),
].join(",");

function fileExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function isAcceptedFile(f: File): boolean {
  if (f.type.startsWith("image/")) return true;
  if (OFFICE_MIMES.has(f.type)) return true;
  return TEXT_EXTS.has(fileExtension(f.name));
}

function groupBySaved(sessions: Session[]) {
  const groups: Record<string, Session[]> = {
    บันทึกไว้: [],
    ล่าสุด: [],
  };
  for (const s of sessions) {
    if (s.is_saved) groups["บันทึกไว้"].push(s);
    else groups["ล่าสุด"].push(s);
  }
  return groups;
}

function groupByUser<T extends { username: string }>(sessions: T[]) {
  const groups: Record<string, T[]> = {};
  for (const s of sessions) {
    if (!groups[s.username]) groups[s.username] = [];
    groups[s.username].push(s);
  }
  return groups;
}

export default function ChatPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [teamSessions, setTeamSessions] = useState<TeamSession[]>([]);
  const [sharedTeamSessions, setSharedTeamSessions] = useState<
    SharedTeamSession[]
  >([]);
  const [currentSid, setCurrentSid] = useState<number | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [role, setRole] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<SearchHit[] | null>(null);
  const [renameTarget, setRenameTarget] = useState<Session | null>(null);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);
  const [adminDeleteTarget, setAdminDeleteTarget] = useState<{
    sid: number;
    owner: string;
    title: string;
  } | null>(null);
  const [alertMsg, setAlertMsg] = useState<string | null>(null);
  const [teamExpanded, setTeamExpanded] = useState(false);
  const [sharedTeamExpanded, setSharedTeamExpanded] = useState(false);
  // When viewing a teammate's shared chat read-only, this holds its share
  // token so the "รับเป็นแชทของฉัน" (fork) button knows what to clone.
  const [readOnlyToken, setReadOnlyToken] = useState<string | null>(null);
  const [forkingInline, setForkingInline] = useState(false);
  const [folderOpen, setFolderOpen] = useState<Record<string, boolean>>({
    บันทึกไว้: true,
    ล่าสุด: true,
  });
  const [readOnlyOwner, setReadOnlyOwner] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [typingIndex, setTypingIndex] = useState<number | null>(null);
  const [typedChars, setTypedChars] = useState(0);
  const [chatMode, setChatMode] = useState<"normal" | "company">("normal");
  // Per-session question budget. Server is the source of truth: every /chat
  // response refreshes turnCount, and loadSession seeds it from the GET.
  // turnLimit comes from the server so we don't hardcode it in the UI.
  const [turnCount, setTurnCount] = useState<number>(0);
  const [turnLimit, setTurnLimit] = useState<number>(20);
  // Share state. sharedToken is null when the chat isn't shared. shareOpen
  // controls the visibility of the Share modal.
  const [sharedToken, setSharedToken] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setRole(getRole());
    setUsername(getUsername());
    refreshSessions();
    // Every user — not just admins — sees the team's shared chats.
    refreshSharedTeamSessions();

    // Deep-link support: /chat?sid=N opens a specific session immediately.
    // Used by the fork flow (/chat/shared/{token}/fork redirects here) so
    // the user lands in the cloned chat instead of an empty new chat screen.
    //
    // Read directly from window.location instead of next/navigation's
    // useSearchParams to avoid the Next.js 16 static-prerender requirement
    // that useSearchParams sit inside a Suspense boundary — this useEffect
    // only runs client-side, so window.location is always defined here.
    if (typeof window !== "undefined") {
      const sidParam = new URLSearchParams(window.location.search).get("sid");
      if (sidParam) {
        const sid = parseInt(sidParam, 10);
        if (!Number.isNaN(sid)) {
          loadSession(sid);
          window.history.replaceState({}, "", "/chat");
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  useEffect(() => {
    if (role === "admin") refreshTeamSessions();
  }, [role]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, typedChars, sending]);

  // Auto-resize chat input textarea based on content (capped at ~10 lines).
  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`;
  }, [input]);

  function handleInputKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter alone sends; Shift+Enter inserts newline.
    // isComposing prevents accidental send while typing Thai via IME.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      e.currentTarget.closest("form")?.requestSubmit();
    }
  }

  // Typewriter effect — only animates the currently-streaming bot message.
  useEffect(() => {
    if (typingIndex === null) return;
    const msg = messages[typingIndex];
    if (!msg || msg.role !== "bot") {
      setTypingIndex(null);
      return;
    }
    if (typedChars >= msg.text.length) {
      setTypingIndex(null);
      return;
    }
    // Chunk size scales with message length so long answers don't take forever.
    const step = msg.text.length > 400 ? 4 : msg.text.length > 150 ? 2 : 1;
    const timer = setTimeout(() => {
      setTypedChars((c) => Math.min(c + step, msg.text.length));
    }, 14);
    return () => clearTimeout(timer);
  }, [typingIndex, typedChars, messages]);

  async function refreshSessions() {
    try {
      const list = await api<Session[]>("/chat/sessions");
      setSessions(list);
    } catch {
      // ignore
    }
  }

  async function refreshTeamSessions() {
    try {
      const list = await api<TeamSession[]>("/admin/chat-history");
      setTeamSessions(list);
    } catch {
      // ignore
    }
  }

  async function refreshSharedTeamSessions() {
    try {
      const list = await api<SharedTeamSession[]>("/chat/shared-sessions");
      setSharedTeamSessions(list);
    } catch {
      // ignore — endpoint is postgres-only; on sqlite it 501s and we just
      // render an empty panel.
    }
  }

  type LoadedMessage = {
    question: string;
    answer: string;
    source: string;
    attachments?: Attachment[];
    source_knowledge_id?: number | null;
    source_file?: string | null;
  };

  function hydrateMessages(loaded: LoadedMessage[]): Msg[] {
    const result: Msg[] = [];
    for (const m of loaded) {
      result.push({ role: "user", text: m.question, attachments: m.attachments ?? [] });
      result.push({
        role: "bot",
        text: m.answer,
        source: m.source,
        source_knowledge_id: m.source_knowledge_id ?? null,
        source_file: m.source_file ?? null,
      });
    }
    return result;
  }

  async function loadSession(sid: number) {
    setCurrentSid(sid);
    setSearchResults(null);
    setReadOnlyOwner(null);
    setReadOnlyToken(null);
    setTypingIndex(null);
    try {
      const data = await api<{
        messages: LoadedMessage[];
        mode?: "normal" | "company";
        turn_count?: number;
        shared_token?: string | null;
      }>(`/chat/sessions/${sid}`);
      setMessages(hydrateMessages(data.messages));
      // Restore the toggle to whatever mode the user was last in on this session.
      setChatMode(data.mode === "company" ? "company" : "normal");
      // Reseed the per-session counter from server so the UI is correct even
      // if the user reopens a chat from another tab/device.
      setTurnCount(data.turn_count ?? 0);
      setSharedToken(data.shared_token ?? null);
    } catch {
      setMessages([]);
      setChatMode("normal");
      setTurnCount(0);
      setSharedToken(null);
    }
  }

  async function loadTeamSession(sid: number, owner: string) {
    setCurrentSid(sid);
    setSearchResults(null);
    setReadOnlyOwner(owner);
    setReadOnlyToken(null);
    setTypingIndex(null);
    setChatMode("normal");
    try {
      const data = await api<{ messages: LoadedMessage[] }>(`/admin/chat-history/${sid}`);
      setMessages(hydrateMessages(data.messages));
    } catch {
      setMessages([]);
    }
  }

  // Open a teammate's shared chat read-only via its share token (any user).
  // Unlike loadTeamSession (admin-only /admin endpoint) this goes through the
  // public /chat/shared/{token} route and keeps the token so the user can fork.
  async function loadSharedTeamSession(
    sid: number,
    owner: string,
    token: string,
  ) {
    setCurrentSid(sid);
    setSearchResults(null);
    setReadOnlyOwner(owner);
    setReadOnlyToken(token);
    setTypingIndex(null);
    try {
      const data = await api<{
        messages: LoadedMessage[];
        mode?: "normal" | "company";
      }>(`/chat/shared/${token}`);
      setMessages(hydrateMessages(data.messages));
      setChatMode(data.mode === "company" ? "company" : "normal");
    } catch {
      // The owner revoked the share (or deleted the chat) since the panel was
      // last refreshed — the token no longer resolves. Tell the user, drop the
      // read-only view, and re-fetch the list so the stale entry disappears.
      setReadOnlyOwner(null);
      setReadOnlyToken(null);
      setCurrentSid(null);
      setMessages([]);
      setChatMode("normal");
      setAlertMsg(`${owner} ยกเลิกการแชร์แชทนี้แล้ว — เปิดดูไม่ได้อีก`);
      refreshSharedTeamSessions();
    }
  }

  // Clone the shared chat being viewed into the user's own session list, then
  // jump into the editable copy.
  async function handleForkInline() {
    if (!readOnlyToken || forkingInline) return;
    setForkingInline(true);
    try {
      const { session_id } = await forkSharedSession(readOnlyToken);
      setReadOnlyOwner(null);
      setReadOnlyToken(null);
      await refreshSessions();
      await loadSession(session_id);
    } catch (e: unknown) {
      setAlertMsg(
        "รับเป็นแชทของฉันไม่สำเร็จ: " +
          (e instanceof Error ? e.message : "เกิดข้อผิดพลาด"),
      );
    } finally {
      setForkingInline(false);
    }
  }

  // Owner removes their own chat from the team-shared panel (keeps the chat,
  // just kills the link). Admins use the hard-delete flow instead.
  async function unshareFromPanel(sid: number) {
    try {
      await revokeSessionShare(sid);
      if (currentSid === sid && readOnlyToken) {
        setReadOnlyOwner(null);
        setReadOnlyToken(null);
        setCurrentSid(null);
        setMessages([]);
      }
      if (sid === currentSid) setSharedToken(null);
      refreshSharedTeamSessions();
    } catch (e: unknown) {
      setAlertMsg(
        "ยกเลิกการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""),
      );
    }
  }

  function newChat(mode: "normal" | "company" = "normal") {
    setCurrentSid(null);
    setMessages([]);
    setSearchResults(null);
    setReadOnlyOwner(null);
    setReadOnlyToken(null);
    setInput("");
    setPendingFiles([]);
    setTypingIndex(null);
    setChatMode(mode);
    setTurnCount(0);
    setSharedToken(null);
  }

  async function handleShareToggle() {
    if (!currentSid) return;
    try {
      if (sharedToken) {
        await revokeSessionShare(currentSid);
        setSharedToken(null);
      } else {
        const { token } = await shareSession(currentSid);
        setSharedToken(token);
      }
      // Keep the team-shared panel in sync with what the user just (un)shared.
      refreshSharedTeamSessions();
    } catch (e: unknown) {
      setAlertMsg(
        "ทำรายการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""),
      );
    }
  }

  function addFiles(files: File[]) {
    if (readOnlyOwner) return;

    const valid: File[] = [];

    for (const f of files) {
      if (f.size > 100 * 1024 * 1024) {
        setAlertMsg(`ไฟล์ "${f.name}" ใหญ่เกิน 100MB`);
        continue;
      }

      if (!isAcceptedFile(f)) {
        setAlertMsg(`ไฟล์ "${f.name}" ไม่รองรับ`);
        continue;
      }

      valid.push(f);
    }

    if (valid.length === 0) return;

    setPendingFiles((prev) => {
      const next = [...prev, ...valid].slice(0, 5);

      if (prev.length + valid.length > 5) {
        setAlertMsg("แนบไฟล์ได้สูงสุด 5 ไฟล์ต่อข้อความ");
      }

      return next;
    });
  }

  function handleFilePick(e: React.ChangeEvent<HTMLInputElement>) {
    addFiles(Array.from(e.target.files ?? []));
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();

    if (readOnlyOwner || sending) return;

    e.dataTransfer.dropEffect = "copy";
    setIsDragging(true);
  }

  function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();

    const currentTarget = e.currentTarget;
    const relatedTarget = e.relatedTarget as Node | null;

    if (!relatedTarget || !currentTarget.contains(relatedTarget)) {
      setIsDragging(false);
    }
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();

    setIsDragging(false);

    if (readOnlyOwner || sending) return;

    addFiles(Array.from(e.dataTransfer.files ?? []));
  }

  function removePendingFile(idx: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  function exitReadOnly() {
    setReadOnlyOwner(null);
    setReadOnlyToken(null);
    setCurrentSid(null);
    setMessages([]);
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if ((!text && pendingFiles.length === 0) || sending || readOnlyOwner) return;

    const filesToSend = pendingFiles;
    const optimisticAtts: Attachment[] = filesToSend.map((f, i) => ({
      id: -(i + 1),
      filename: f.name,
      content_type: f.type,
      size_bytes: f.size,
    }));
    setMessages((m) => [
      ...m,
      { role: "user", text: text || "[ไฟล์แนบ]", attachments: optimisticAtts },
    ]);
    setInput("");
    setPendingFiles([]);
    setSending(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await sendChat({
        message: text,
        sessionId: currentSid,
        files: filesToSend,
        mode: chatMode,
        signal: controller.signal,
      });
      let newBotIdx = 0;
      setMessages((m) => {
        const copy = [...m];
        const lastUserIdx = copy.length - 1;
        if (copy[lastUserIdx]?.role === "user") {
          copy[lastUserIdx] = { ...copy[lastUserIdx], attachments: res.attachments };
        }
        copy.push({
          role: "bot",
          text: res.answer,
          source: res.source,
          source_knowledge_id: res.source_knowledge_id ?? null,
          source_file: res.source_file ?? null,
        });
        newBotIdx = copy.length - 1;
        return copy;
      });
      setTypedChars(0);
      setTypingIndex(newBotIdx);
      setCurrentSid(res.session_id);
      // Server-driven counter so the badge stays accurate even if export-offer
      // messages don't count against the quota.
      if (typeof res.turn_count === "number") setTurnCount(res.turn_count);
      if (typeof res.turn_limit === "number") setTurnLimit(res.turn_limit);
      refreshSessions();
      if (role === "admin") refreshTeamSessions();
    } catch (e: unknown) {
      const isAbort =
        e instanceof DOMException && e.name === "AbortError";
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: isAbort
            ? "ยกเลิกการถามแล้ว"
            : `เกิดข้อผิดพลาด: ${e instanceof Error ? e.message : ""}`,
        },
      ]);
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  function cancelSend() {
    abortRef.current?.abort();
  }

  async function performRename(sid: number, newTitle: string) {
    try {
      await api(`/chat/sessions/${sid}`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle }),
      });
      refreshSessions();
    } catch (e: unknown) {
      setAlertMsg("เปลี่ยนชื่อไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  async function performDelete(sid: number) {
    try {
      await api(`/chat/sessions/${sid}`, { method: "DELETE" });
      if (sid === currentSid) newChat();
      refreshSessions();
    } catch (e: unknown) {
      setAlertMsg("ลบไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  async function performAdminDelete(sid: number) {
    try {
      await api(`/admin/chat-history/${sid}`, { method: "DELETE" });
      if (sid === currentSid && readOnlyOwner) {
        exitReadOnly();
      }
      refreshTeamSessions();
      refreshSharedTeamSessions();
    } catch (e: unknown) {
      setAlertMsg("ลบไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  async function performToggleSave(sid: number, currentSaved: boolean) {
    try {
      await api(`/chat/sessions/${sid}/save`, {
        method: "PATCH",
        body: JSON.stringify({ is_saved: !currentSaved }),
      });
      refreshSessions();
    } catch (e: unknown) {
      setAlertMsg("บันทึกไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  function exportSession(sid: number) {
    const token = getToken();
    fetch(`${API_BASE}/chat/sessions/${sid}/export`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.blob())
      .then((b) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = `session-${sid}.csv`;
        a.click();
      });
  }

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = search.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    try {
      const hits = await api<SearchHit[]>(`/chat/search?q=${encodeURIComponent(q)}`);
      setSearchResults(hits);
    } catch {
      setSearchResults([]);
    }
  }

  function logout() {
    clearAuth();
    router.replace("/login");
  }

  const groups = groupBySaved(sessions);
  const teamByUser = groupByUser(teamSessions);
  const sharedByUser = groupByUser(sharedTeamSessions);
  const currentTitle = currentSid
    ? readOnlyOwner
      ? teamSessions.find((s) => s.id === currentSid)?.title ?? "บทสนทนา"
      : sessions.find((s) => s.id === currentSid)?.title ?? "แชท"
    : "แชทใหม่";
  const avatarLetter = (username ?? "?").charAt(0).toUpperCase();

  return (
    <div
      className="relative flex h-screen w-full bg-gray-50"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDragging && !readOnlyOwner && !sending && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-purple-900/20 backdrop-blur-sm">
          <div className="rounded-3xl border-2 border-dashed border-purple-400 bg-white/95 px-10 py-8 text-center shadow-2xl">
            <Paperclip size={36} className="mx-auto mb-3 text-purple-500" />
            <p className="text-lg font-semibold text-purple-700">
              วางไฟล์ที่นี่เพื่อแนบในแชท
            </p>
            <p className="mt-1 text-sm text-gray-500">
              รองรับรูปภาพ, PDF, Word, Excel, PowerPoint และไฟล์ข้อความ สูงสุด 5 ไฟล์
            </p>
          </div>
        </div>
      )}

      {/* Sidebar */}
      <aside className="w-80 bg-gradient-to-b from-purple-500 via-purple-600 to-purple-700 flex flex-col shadow-2xl">
        <div className="p-4 space-y-2">
          <button
            onClick={() => newChat("normal")}
            className="w-full flex items-center justify-center gap-2 bg-white/10 hover:bg-white/20 text-white py-3 px-4 rounded-xl transition-all backdrop-blur-sm border border-white/20 shadow-lg"
          >
            <Plus size={20} />
            <span>แชทใหม่</span>
          </button>
          <button
            onClick={() => newChat("company")}
            className="w-full flex items-center justify-center gap-2 bg-amber-400/30 hover:bg-amber-400/50 text-white py-3 px-4 rounded-xl transition-all backdrop-blur-sm border border-amber-200/40 shadow-lg"
          >
            <span className="text-base">📘</span>
            <span>คู่มือบริษัท</span>
          </button>
        </div>

        <form onSubmit={runSearch} className="px-4 pb-3">
          <div className="relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 text-white/85"
              size={18}
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ค้นหาในประวัติ…"
              className="w-full pl-10 pr-4 py-2.5 bg-white/10 border border-white/20 rounded-lg text-white placeholder-white/70 focus:outline-none focus:ring-2 focus:ring-white/30 transition-all backdrop-blur-sm text-sm"
            />
          </div>
        </form>

        {/* Team chat — admin only */}
        {role === "admin" && (
          <div className="px-4 pb-3">
            <button
              onClick={() => setTeamExpanded(!teamExpanded)}
              className="w-full flex items-center justify-between gap-2 bg-white/5 hover:bg-white/10 text-white hover:text-white py-2.5 px-3 rounded-lg transition-all border border-white/10"
            >
              <span className="flex items-center gap-2 text-sm">
                <Users size={16} />
                แชทของทีม
                <span className="bg-white/20 text-white text-xs px-1.5 py-0.5 rounded-full">
                  {teamSessions.length}
                </span>
              </span>
              {teamExpanded ? (
                <ChevronDown size={16} />
              ) : (
                <ChevronRight size={16} />
              )}
            </button>

            {teamExpanded && (
              <div className="mt-2 max-h-64 overflow-y-auto space-y-2 bg-black/20 rounded-lg p-2">
                {Object.entries(teamByUser).length === 0 && (
                  <p className="text-white/85 text-xs text-center py-3">
                    ยังไม่มีบทสนทนา
                  </p>
                )}
                {Object.entries(teamByUser).map(([uname, list]) => (
                  <div key={uname}>
                    <div className="text-white/85 text-xs px-2 py-1 flex items-center justify-between">
                      <span className="font-medium">{uname}</span>
                      <span className="bg-white/10 text-white text-[10px] px-1.5 rounded">
                        {list.length}
                      </span>
                    </div>
                    {list.map((s) => {
                      const active = s.id === currentSid && readOnlyOwner === uname;
                      return (
                        <div
                          key={s.id}
                          onClick={() => loadTeamSession(s.id, uname)}
                          className={`group w-full flex items-center gap-2 px-2 py-1.5 rounded text-left transition-all text-xs cursor-pointer ${
                            active
                              ? "bg-purple-400 text-white"
                              : "text-white hover:bg-white/10"
                          }`}
                          title={s.title}
                        >
                          <Eye size={12} className="flex-shrink-0 opacity-70" />
                          <span className="flex-1 truncate">{s.title}</span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setAdminDeleteTarget({
                                sid: s.id,
                                owner: uname,
                                title: s.title,
                              });
                            }}
                            className="hidden group-hover:flex p-0.5 text-white/85 hover:text-red-300 flex-shrink-0"
                            title={`ลบบทสนทนานี้ของ ${uname}`}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Team shared chats — visible to EVERY user. Lists only chats people
            explicitly shared. View read-only + fork; you can unshare your own,
            admins can hard-delete any. */}
        <div className="px-4 pb-3">
          <button
            onClick={() => setSharedTeamExpanded(!sharedTeamExpanded)}
            className="w-full flex items-center justify-between gap-2 bg-white/5 hover:bg-white/10 text-white hover:text-white py-2.5 px-3 rounded-lg transition-all border border-white/10"
          >
            <span className="flex items-center gap-2 text-sm">
              <Share2 size={16} />
              แชร์ร่วมกันในทีม
              <span className="bg-white/20 text-white text-xs px-1.5 py-0.5 rounded-full">
                {sharedTeamSessions.length}
              </span>
            </span>
            {sharedTeamExpanded ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>

          {sharedTeamExpanded && (
            <div className="mt-2 max-h-64 overflow-y-auto space-y-2 bg-black/20 rounded-lg p-2">
              {Object.entries(sharedByUser).length === 0 && (
                <p className="text-white/85 text-xs text-center py-3">
                  ยังไม่มีแชทที่ถูกแชร์
                </p>
              )}
              {Object.entries(sharedByUser).map(([uname, list]) => {
                const isMine = uname === username;
                return (
                  <div key={uname}>
                    <div className="text-white/85 text-xs px-2 py-1 flex items-center justify-between">
                      <span className="font-medium">
                        {uname}
                        {isMine && (
                          <span className="ml-1 text-purple-200">(คุณ)</span>
                        )}
                      </span>
                      <span className="bg-white/10 text-white text-[10px] px-1.5 rounded">
                        {list.length}
                      </span>
                    </div>
                    {list.map((s) => {
                      const active =
                        s.id === currentSid && readOnlyToken === s.shared_token;
                      return (
                        <div
                          key={s.id}
                          onClick={() =>
                            loadSharedTeamSession(s.id, uname, s.shared_token)
                          }
                          className={`group w-full flex items-center gap-2 px-2 py-1.5 rounded text-left transition-all text-xs cursor-pointer ${
                            active
                              ? "bg-purple-400 text-white"
                              : "text-white hover:bg-white/10"
                          }`}
                          title={s.title}
                        >
                          <Share2
                            size={12}
                            className="flex-shrink-0 opacity-70"
                          />
                          <span className="flex-1 truncate">{s.title}</span>
                          {isMine ? (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                unshareFromPanel(s.id);
                              }}
                              className="hidden group-hover:flex p-0.5 text-white/85 hover:text-amber-300 flex-shrink-0"
                              title="ยกเลิกการแชร์ (แชทยังอยู่ของคุณ)"
                            >
                              <Link2Off size={12} />
                            </button>
                          ) : (
                            role === "admin" && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setAdminDeleteTarget({
                                    sid: s.id,
                                    owner: uname,
                                    title: s.title,
                                  });
                                }}
                                className="hidden group-hover:flex p-0.5 text-white/85 hover:text-red-300 flex-shrink-0"
                                title={`ลบบทสนทนานี้ของ ${uname}`}
                              >
                                <Trash2 size={12} />
                              </button>
                            )
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Own sessions / search results */}
        <div className="flex-1 overflow-y-auto px-4 space-y-4">
          {searchResults ? (
            <div>
              <div className="mb-2 text-white/85 text-xs uppercase tracking-wide">
                ผลค้นหา {searchResults.length} รายการ
              </div>
              <div className="space-y-1">
                {searchResults.map((h) => (
                  <button
                    key={h.id}
                    onClick={() => {
                      setSearchResults(null);
                      setSearch("");
                      loadSession(h.session_id);
                    }}
                    className="w-full text-left bg-white/5 hover:bg-white/15 rounded-lg p-3 transition-all"
                  >
                    <div className="text-xs text-white/85 truncate">
                      {h.session_title}
                    </div>
                    <div className="text-white text-sm truncate mt-0.5">{h.question}</div>
                  </button>
                ))}
                {searchResults.length === 0 && (
                  <div className="text-white/85 text-sm px-1">ไม่พบ</div>
                )}
              </div>
            </div>
          ) : (
            Object.entries(groups).map(
              ([label, list]) => {
                const isOpen = folderOpen[label] ?? true;
                const isSavedFolder = label === "บันทึกไว้";
                return (
                  list.length > 0 && (
                  <div key={label} className="mb-3">
                    <button
                      onClick={() =>
                        setFolderOpen((prev) => ({ ...prev, [label]: !isOpen }))
                      }
                      className={`w-full flex items-center gap-2 mb-1.5 px-2 py-1.5 rounded-lg transition-all ${
                        isSavedFolder
                          ? "bg-yellow-400/15 hover:bg-yellow-400/25 border border-yellow-300/30"
                          : "bg-white/5 hover:bg-white/10 border border-white/10"
                      }`}
                    >
                      {isOpen ? (
                        <ChevronDown size={14} className="text-white/85 flex-shrink-0" />
                      ) : (
                        <ChevronRight size={14} className="text-white/85 flex-shrink-0" />
                      )}
                      {isSavedFolder ? (
                        isOpen ? (
                          <FolderOpen size={16} className="text-yellow-300 flex-shrink-0" />
                        ) : (
                          <Folder size={16} className="text-yellow-300 flex-shrink-0" />
                        )
                      ) : isOpen ? (
                        <FolderOpen size={16} className="text-white/85 flex-shrink-0" />
                      ) : (
                        <Folder size={16} className="text-white/85 flex-shrink-0" />
                      )}
                      <span className="flex-1 text-left text-white text-sm font-semibold">
                        {label}
                      </span>
                      <span className="text-xs text-white/85 bg-white/10 rounded-full px-2 py-0.5">
                        {list.length}
                      </span>
                    </button>
                    {isOpen && (
                    <div className="space-y-1 pl-3 border-l-2 border-white/10 ml-3">
                      {list.map((s) => {
                        const active = s.id === currentSid && !readOnlyOwner;
                        return (
                          <div
                            key={s.id}
                            onClick={() => loadSession(s.id)}
                            className={`group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-all ${
                              active
                                ? "bg-purple-400 text-white shadow-lg"
                                : "text-white hover:bg-white/10"
                            }`}
                          >
                            <MessageSquare size={16} className="flex-shrink-0" />
                            <span className="flex-1 truncate text-sm">{s.title}</span>
                            <div className="hidden group-hover:flex gap-1 flex-shrink-0">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  performToggleSave(s.id, !!s.is_saved);
                                }}
                                className={`p-1 ${s.is_saved ? "text-yellow-300" : "text-white/85 hover:text-yellow-300"}`}
                                title={s.is_saved ? "เลิกบันทึก" : "บันทึกแชทนี้ไว้"}
                              >
                                {s.is_saved ? <BookmarkCheck size={14} /> : <Bookmark size={14} />}
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setRenameTarget(s);
                                }}
                                className="p-1 text-white/85 hover:text-white"
                                title="เปลี่ยนชื่อ"
                              >
                                <Pencil size={14} />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  exportSession(s.id);
                                }}
                                className="p-1 text-white/85 hover:text-white"
                                title="ดาวน์โหลด CSV"
                              >
                                <Download size={14} />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setDeleteTargetId(s.id);
                                }}
                                className="p-1 text-white/85 hover:text-red-300"
                                title="ลบ"
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    )}
                  </div>
                  )
                );
              },
            )
          )}
        </div>

        {/* Profile + actions */}
        <div className="p-4 border-t border-white/20 space-y-2">
          <div className="flex items-center gap-3 px-2 py-2 rounded-xl bg-white/5 border border-white/10">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-300 to-purple-500 flex items-center justify-center text-white font-medium shadow-md flex-shrink-0">
              {avatarLetter}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-white text-sm font-medium truncate">
                {username ?? "—"}
              </p>
              <p className="text-white/85 text-xs">
                {role === "admin" ? "⭐ Administrator" : "User"}
              </p>
            </div>
          </div>
          {role === "admin" && (
            <button
              onClick={() => router.push("/admin")}
              className="w-full flex items-center gap-2 text-white hover:text-white hover:bg-white/10 py-2 px-3 rounded-lg transition-all text-sm"
            >
              <Settings size={16} />
              <span>Admin Dashboard</span>
            </button>
          )}
          <button
            onClick={logout}
            className="w-full flex items-center gap-2 text-white hover:text-white hover:bg-white/10 py-2 px-3 rounded-lg transition-all text-sm"
          >
            <LogOut size={16} />
            <span>ออกจากระบบ</span>
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col">
        <header className="bg-white border-b border-gray-200 px-8 py-4 shadow-sm flex items-center justify-between gap-4">
          <h2 className="text-gray-800 font-medium truncate flex-1">{currentTitle}</h2>
          <div className="flex items-center gap-3">
            {!readOnlyOwner && currentSid !== null && (
              <TurnCounter count={turnCount} limit={turnLimit} onNewChat={() => newChat(chatMode)} />
            )}
            {!readOnlyOwner && currentSid !== null && messages.length > 0 && (
              <button
                onClick={() => setShareOpen(true)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-all border ${
                  sharedToken
                    ? "bg-green-50 text-green-700 border-green-300 hover:bg-green-100"
                    : "bg-white text-gray-700 border-gray-200 hover:bg-gray-50 hover:border-gray-300"
                }`}
                title={sharedToken ? "แชทนี้ถูกแชร์อยู่ — กดเพื่อจัดการ" : "แชร์แชทนี้ให้เพื่อน"}
              >
                <Share2 size={14} />
                <span>{sharedToken ? "แชร์อยู่" : "แชร์"}</span>
              </button>
            )}
            {chatMode === "company" && !readOnlyOwner && (
              <button
                onClick={() => setChatMode("normal")}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-100 hover:bg-amber-200 text-amber-800 rounded-lg text-sm transition-all border border-amber-300"
                title="ปิดโหมดคู่มือบริษัท (ยังอยู่ในแชทเดิม)"
              >
                <span>📘</span>
                <span>โหมด: คู่มือบริษัท</span>
                <X size={14} />
              </button>
            )}
            {readOnlyOwner && (
              <button
                onClick={exitReadOnly}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-50 hover:bg-amber-100 text-amber-700 rounded-lg text-sm transition-all border border-amber-200"
              >
                <X size={14} />
                ออกจากโหมดอ่าน
              </button>
            )}
            <div className="flex items-center gap-2.5 pl-2 pr-4 py-1.5 bg-purple-500 rounded-xl">
              <div className="bg-white rounded-lg p-0.5">
                <img
                  src="/Logo_siri.jpg"
                  alt="Sirivatana"
                  className="w-8 h-8 rounded-md object-cover"
                />
              </div>
              <span className="text-white font-semibold text-sm whitespace-nowrap">
                ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน)
              </span>
            </div>
          </div>
        </header>

        {readOnlyOwner && (
          <div className="bg-amber-50 border-b border-amber-200 px-8 py-2.5 text-sm text-amber-800 flex items-center gap-2">
            <Eye size={16} />
            <span className="flex-1">
              คุณกำลังดูบทสนทนาของ <strong>{readOnlyOwner}</strong> — โหมดอ่านอย่างเดียว
            </span>
            {/* Fork is offered only when viewing via a share token (team-shared
                panel) and the chat isn't the user's own. */}
            {readOnlyToken && readOnlyOwner !== username && (
              <button
                onClick={handleForkInline}
                disabled={forkingInline}
                className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1 bg-gradient-to-r from-purple-500 to-purple-600 text-white rounded-lg text-xs font-medium hover:from-purple-600 hover:to-purple-700 transition-all shadow-sm disabled:opacity-60"
              >
                {forkingInline ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <ClipboardList size={13} />
                )}
                รับเป็นแชทของฉัน
              </button>
            )}
          </div>
        )}

        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto bg-gradient-to-b from-purple-50/30 to-white"
        >
          <div className="max-w-4xl mx-auto px-8 py-8 space-y-6">
            {messages.length === 0 && (
              <div className="text-center text-gray-400 mt-20">
                <img
                  src="/Logo_siri.jpg"
                  alt="Sirivatana"
                  className="mx-auto w-20 h-20 rounded-2xl object-cover mb-4 shadow-md"
                />
                <p>พิมพ์คำถามเกี่ยวกับบริษัทหรือคำถามทั่วไปได้เลย</p>
              </div>
            )}
            {messages.map((m, i) => {
              const isTyping = i === typingIndex;
              const shownText = isTyping ? m.text.slice(0, typedChars) : m.text;

              if (m.role === "user") {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-2xl px-6 py-4 rounded-2xl shadow-md whitespace-pre-wrap bg-gradient-to-r from-purple-400 to-purple-500 text-white">
                      {m.attachments && m.attachments.length > 0 && (
                        <AttachmentList attachments={m.attachments} onUserBubble={true} />
                      )}
                      {m.text && <p className="leading-relaxed">{m.text}</p>}
                    </div>
                  </div>
                );
              }

              return (
                <div key={i} className="flex justify-start gap-3">
                  <img
                    src="/Logo_siri.jpg"
                    alt="Sirivatana"
                    className="w-9 h-9 rounded-full object-cover flex-shrink-0 shadow-sm border border-gray-200"
                  />
                  <div className="flex-1 min-w-0 pt-1">
                    {m.attachments && m.attachments.length > 0 && (
                      <AttachmentList attachments={m.attachments} onUserBubble={false} />
                    )}
                    {m.text && (
                      isTyping ? (
                        <p className="leading-relaxed text-gray-800 whitespace-pre-wrap">
                          {shownText}
                          <span className="inline-block w-0.5 h-4 bg-purple-500 ml-0.5 align-middle animate-pulse" />
                        </p>
                      ) : (
                        <MarkdownMessage text={shownText} />
                      )
                    )}
                    {/* Export controls — only when the bot's reply is an
                        export offer (user asked "ขอ PDF" etc.). Buttons act
                        on the most recent prior bot answer (the actual
                        content the user wants saved). */}
                    {m.text && !isTyping && m.source === "export_offer" && (
                      <ExportOfferActions
                        targetMessage={findPriorBotMessage(messages, i)}
                        targetQuestion={findPriorUserQuestion(messages, i)}
                        onAlert={(msg) => setAlertMsg(msg)}
                      />
                    )}
                  </div>
                </div>
              );
            })}
            {sending && (
              <div className="flex justify-start gap-3">
                <img
                  src="/Logo_siri.jpg"
                  alt="Sirivatana"
                  className="w-9 h-9 rounded-full object-cover flex-shrink-0 shadow-sm border border-gray-200"
                />
                <div className="flex items-center gap-1.5 pt-3">
                  <span
                    className="w-2 h-2 bg-purple-400 rounded-full animate-bounce"
                    style={{ animationDelay: "0ms" }}
                  />
                  <span
                    className="w-2 h-2 bg-purple-400 rounded-full animate-bounce"
                    style={{ animationDelay: "150ms" }}
                  />
                  <span
                    className="w-2 h-2 bg-purple-400 rounded-full animate-bounce"
                    style={{ animationDelay: "300ms" }}
                  />
                  <span className="ml-2 text-sm text-gray-500">กำลังคิด…</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="bg-white border-t border-gray-200 px-8 py-6 shadow-lg">
          <form onSubmit={send} className="max-w-4xl mx-auto">
            {pendingFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-3">
                {pendingFiles.map((f, i) => (
                  <PendingFileChip
                    key={i}
                    file={f}
                    onRemove={() => removePendingFile(i)}
                  />
                ))}
              </div>
            )}
            <div className="flex gap-3 items-end">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={FILE_ACCEPT_ATTR}
                onChange={handleFilePick}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={
                  sending ||
                  !!readOnlyOwner ||
                  pendingFiles.length >= 5 ||
                  turnCount >= turnLimit
                }
                className="flex-shrink-0 p-3 bg-gray-50 border border-gray-200 rounded-2xl hover:bg-gray-100 transition-all text-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                title="แนบไฟล์ (รูป, PDF, Word, Excel, PowerPoint, Text/Code — สูงสุด 5 ไฟล์)"
              >
                <Paperclip size={20} />
              </button>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleInputKeyDown}
                rows={1}
                placeholder={
                  readOnlyOwner
                    ? "ไม่สามารถส่งข้อความในโหมดอ่านได้"
                    : turnCount >= turnLimit
                    ? `แชทนี้ครบ ${turnLimit} คำถามแล้ว — เปิดแชทใหม่เพื่อถามต่อ`
                    : pendingFiles.length > 0
                    ? "เขียนคำถามเกี่ยวกับไฟล์"
                    : "พิมพ์คำถาม…"
                }
                disabled={sending || !!readOnlyOwner || turnCount >= turnLimit}
                className="flex-1 px-6 py-4 bg-gray-50 border border-gray-200 rounded-2xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all disabled:opacity-60 disabled:cursor-not-allowed resize-none overflow-y-auto leading-6"
              />
              {sending ? (
                <button
                  type="button"
                  onClick={cancelSend}
                  className="flex-shrink-0 px-8 py-4 bg-red-500 hover:bg-red-600 text-white rounded-2xl transition-all shadow-md hover:shadow-lg flex items-center gap-2"
                  title="หยุด AI"
                >
                  <Square size={18} fill="currentColor" />
                  <span>หยุด</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={
                    !!readOnlyOwner ||
                    (!input.trim() && pendingFiles.length === 0) ||
                    turnCount >= turnLimit
                  }
                  className="flex-shrink-0 px-8 py-4 bg-gradient-to-r from-purple-400 to-purple-500 text-white rounded-2xl hover:from-purple-500 hover:to-purple-600 transition-all shadow-md hover:shadow-lg transform hover:-translate-y-0.5 flex items-center gap-2 disabled:opacity-60 disabled:transform-none disabled:cursor-not-allowed"
                >
                  <Send size={20} />
                  <span>ส่ง</span>
                </button>
              )}
            </div>
            <p className="mt-2 text-xs text-gray-400 text-center">
              กด{" "}
              <kbd className="px-1.5 py-0.5 bg-gray-100 rounded text-gray-600 font-mono text-[10px]">
                Enter
              </kbd>{" "}
              ส่ง • กด{" "}
              <kbd className="px-1.5 py-0.5 bg-gray-100 rounded text-gray-600 font-mono text-[10px]">
                Shift+Enter
              </kbd>{" "}
              ขึ้นบรรทัดใหม่
            </p>
          </form>
        </div>
      </main>

      <PromptModal
        open={!!renameTarget}
        onClose={() => setRenameTarget(null)}
        onConfirm={(newTitle) => {
          if (renameTarget) performRename(renameTarget.id, newTitle);
        }}
        title="เปลี่ยนชื่อบทสนทนา"
        description="ตั้งชื่อใหม่เพื่อหาง่ายขึ้นในประวัติ"
        initialValue={renameTarget?.title ?? ""}
        placeholder="ชื่อบทสนทนา"
        confirmLabel="บันทึก"
      />

      <ConfirmModal
        open={deleteTargetId !== null}
        onClose={() => setDeleteTargetId(null)}
        onConfirm={() => {
          if (deleteTargetId !== null) performDelete(deleteTargetId);
        }}
        title="ลบบทสนทนานี้?"
        description="ข้อความทั้งหมดในบทสนทนานี้จะหายไปและไม่สามารถกู้คืนได้"
        confirmLabel="ลบ"
        danger
      />

      <ConfirmModal
        open={adminDeleteTarget !== null}
        onClose={() => setAdminDeleteTarget(null)}
        onConfirm={() => {
          if (adminDeleteTarget) performAdminDelete(adminDeleteTarget.sid);
        }}
        title="ลบบทสนทนาของทีม?"
        description={
          adminDeleteTarget
            ? `Admin action: คุณกำลังจะลบบทสนทนา "${adminDeleteTarget.title}" ของ ${adminDeleteTarget.owner} — การลบนี้ถาวร ไม่สามารถกู้คืนได้ และจะถูกบันทึกใน audit log`
            : ""
        }
        confirmLabel="ลบ"
        danger
      />

      <AlertModal
        open={!!alertMsg}
        onClose={() => setAlertMsg(null)}
        title="เกิดข้อผิดพลาด"
        description={alertMsg ?? ""}
        variant="error"
      />

      <ShareModal
        open={shareOpen}
        sharedToken={sharedToken}
        onClose={() => setShareOpen(false)}
        onToggleShare={handleShareToggle}
      />
    </div>
  );
}

function ShareModal({
  open,
  sharedToken,
  onClose,
  onToggleShare,
}: {
  open: boolean;
  sharedToken: string | null;
  onClose: () => void;
  onToggleShare: () => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  // Build the full URL on the client so it carries the current origin and
  // works correctly across :3032 / behind a reverse proxy / etc.
  const url =
    typeof window !== "undefined" && sharedToken
      ? `${window.location.origin}/chat/shared/${sharedToken}`
      : "";

  async function handleToggle() {
    setBusy(true);
    try {
      await onToggleShare();
    } finally {
      setBusy(false);
    }
  }

  async function copyUrl() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // clipboard API may be blocked on plain http — fall back to select+copy
      const ta = document.createElement("textarea");
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      } catch {
        // ignore
      }
      ta.remove();
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md bg-white rounded-2xl shadow-2xl border border-gray-100 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <Share2 size={18} className="text-purple-600" />
            แชร์แชทนี้ให้เพื่อน
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="close"
          >
            <X size={20} />
          </button>
        </div>

        {sharedToken ? (
          <>
            <p className="text-sm text-gray-600 mb-3">
              ส่งลิงค์นี้ให้เพื่อน — เพื่อนจะดูแชทแบบ <strong>read-only</strong> ได้
              และกด "รับเป็นแชทของฉัน" เพื่อถามต่อในบัญชีตัวเอง
              <br />
              <span className="text-xs text-gray-500">
                การถามของเพื่อนจะ <strong>ไม่กระทบ</strong> แชทของคุณ
              </span>
            </p>

            <div className="flex items-center gap-2 mb-4">
              <input
                type="text"
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                className="flex-1 px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg font-mono text-gray-700 focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
              <button
                onClick={copyUrl}
                className="flex-shrink-0 flex items-center gap-1 px-3 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-all text-sm"
              >
                {copied ? (
                  <>
                    <Check size={14} />
                    คัดลอกแล้ว
                  </>
                ) : (
                  <>
                    <Copy size={14} />
                    คัดลอก
                  </>
                )}
              </button>
            </div>

            <div className="flex items-center justify-between pt-3 border-t border-gray-100">
              <span className="text-xs text-gray-500 flex items-center gap-1.5">
                <Link2 size={12} className="text-green-600" />
                แชทนี้กำลังถูกแชร์อยู่
              </span>
              <button
                onClick={handleToggle}
                disabled={busy}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-red-200 bg-red-50 text-red-700 hover:bg-red-100 transition-all disabled:opacity-60"
              >
                {busy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Link2Off size={14} />
                )}
                ยกเลิกการแชร์
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-gray-600 mb-4">
              สร้างลิงค์เพื่อแชร์แชทนี้ให้เพื่อนในทีม — เพื่อนจะดูแชทแบบ read-only
              และนำไปถามต่อเป็นของตัวเองได้ <strong>โดยไม่กระทบของคุณ</strong>
            </p>
            <button
              onClick={handleToggle}
              disabled={busy}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-purple-500 to-purple-600 text-white rounded-xl hover:from-purple-600 hover:to-purple-700 transition-all shadow-md disabled:opacity-60"
            >
              {busy ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  กำลังสร้างลิงค์…
                </>
              ) : (
                <>
                  <Link2 size={16} />
                  สร้างลิงค์แชร์
                </>
              )}
            </button>
            <p className="mt-3 text-xs text-gray-500">
              💡 เฉพาะคนที่ login เข้าระบบนี้แล้วเท่านั้นที่จะเปิดลิงค์ได้
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function TurnCounter({
  count,
  limit,
  onNewChat,
}: {
  count: number;
  limit: number;
  onNewChat: () => void;
}) {
  const remaining = Math.max(0, limit - count);
  const atLimit = count >= limit;
  const nearLimit = !atLimit && remaining <= 5;

  let style = "bg-gray-100 text-gray-600 border-gray-200";
  if (atLimit) style = "bg-red-100 text-red-700 border-red-300";
  else if (nearLimit) style = "bg-amber-100 text-amber-800 border-amber-300";

  return (
    <div
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${style}`}
      title={
        atLimit
          ? "แชทนี้ครบจำนวนคำถามแล้ว — เปิดแชทใหม่เพื่อถามต่อ"
          : `ใช้ไป ${count} / ${limit} คำถามต่อแชท`
      }
    >
      <span className="font-medium">{count}/{limit}</span>
      {nearLimit && <span className="hidden sm:inline">เหลือ {remaining}</span>}
      {atLimit && (
        <button
          type="button"
          onClick={onNewChat}
          className="ml-1 px-2 py-0.5 rounded bg-white border border-red-300 text-red-700 hover:bg-red-50 transition-all font-medium"
        >
          + แชทใหม่
        </button>
      )}
    </div>
  );
}

function findPriorBotMessage(messages: Msg[], offerIdx: number): Msg | null {
  // Walk backwards from the export-offer message to find the most recent
  // bot answer — that's the content the user wants saved.
  for (let i = offerIdx - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "bot" && m.source !== "export_offer" && m.source !== "blocked") {
      return m;
    }
  }
  return null;
}

function findPriorUserQuestion(messages: Msg[], offerIdx: number): string | undefined {
  // The user question that produced the prior bot answer — used as PDF title.
  // Walk back: offer (bot) → request (user) → answer (bot) → real question (user).
  const priorBot = findPriorBotMessage(messages, offerIdx);
  if (!priorBot) return undefined;
  const botIdx = messages.indexOf(priorBot);
  for (let i = botIdx - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].text;
  }
  return undefined;
}

function ExportOfferActions({
  targetMessage,
  targetQuestion,
  onAlert,
}: {
  targetMessage: Msg | null;
  targetQuestion?: string;
  onAlert: (msg: string) => void;
}) {
  const [exporting, setExporting] = useState(false);
  const [downloading, setDownloading] = useState(false);

  if (!targetMessage) {
    return (
      <p className="mt-2 text-xs text-gray-500 italic">
        ยังไม่มีคำตอบล่าสุดให้บันทึก — ลองถามคำถามก่อน แล้วพิมพ์ "ขอ PDF" อีกครั้งค่ะ
      </p>
    );
  }

  const hasSource =
    !!targetMessage.source_knowledge_id && !!targetMessage.source_file;

  async function handleExportPdf() {
    if (exporting || !targetMessage) return;
    setExporting(true);
    try {
      const titleBase = targetQuestion?.trim() || "Sirivatana chat";
      const safeTitle =
        titleBase.length > 60 ? titleBase.slice(0, 57) + "..." : titleBase;
      const filename = `Sirivatana_${sanitizeFilename(safeTitle)}.pdf`;
      await exportAnswerPdf({
        content: targetMessage.text,
        title: safeTitle,
        userQuestion: targetQuestion,
        filename,
      });
    } catch (e: unknown) {
      onAlert("Export PDF ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    } finally {
      setExporting(false);
    }
  }

  async function handleDownloadSource() {
    if (!targetMessage?.source_knowledge_id || downloading) return;
    setDownloading(true);
    try {
      await downloadSourceFile(targetMessage.source_knowledge_id);
    } catch (e: unknown) {
      onAlert(
        "ดาวน์โหลดเอกสารต้นฉบับไม่สำเร็จ: " +
          (e instanceof Error ? e.message : ""),
      );
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={handleExportPdf}
        disabled={exporting}
        className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-xl border border-purple-300 bg-purple-50 text-purple-700 hover:bg-purple-100 hover:border-purple-400 transition-all disabled:opacity-60 disabled:cursor-wait shadow-sm"
        title="บันทึกคำตอบล่าสุดเป็น PDF"
      >
        {exporting ? (
          <>
            <Loader2 size={15} className="animate-spin" />
            กำลังสร้าง PDF…
          </>
        ) : (
          <>
            <FileDown size={15} />
            ดาวน์โหลด PDF
          </>
        )}
      </button>

      {hasSource && (
        <button
          type="button"
          onClick={handleDownloadSource}
          disabled={downloading}
          className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-xl border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 hover:border-gray-400 transition-all disabled:opacity-60 disabled:cursor-wait shadow-sm max-w-[360px]"
          title={`ดาวน์โหลด: ${targetMessage.source_file}`}
        >
          {downloading ? (
            <>
              <Loader2 size={15} className="animate-spin" />
              กำลังดาวน์โหลด…
            </>
          ) : (
            <>
              <FileText size={15} className="flex-shrink-0 text-purple-500" />
              <span className="truncate">📎 {targetMessage.source_file}</span>
            </>
          )}
        </button>
      )}
    </div>
  );
}

function sanitizeFilename(name: string): string {
  // Strip characters Windows / OSX can't have in a filename, plus any whitespace
  // that survives. Keep Thai chars intact so the file name stays meaningful.
  return name.replace(/[\\/:*?"<>|]+/g, "").replace(/\s+/g, "_").slice(0, 80) || "chat";
}

function PendingFileChip({ file, onRemove }: { file: File; onRemove: () => void }) {
  const isImage = file.type.startsWith("image/");
  const [thumb, setThumb] = useState<string | null>(null);

  useEffect(() => {
    if (!isImage) return;
    const url = URL.createObjectURL(file);
    setThumb(url);
    return () => URL.revokeObjectURL(url);
  }, [file, isImage]);

  return (
    <div className="flex items-center gap-2 bg-purple-50 border border-purple-200 rounded-xl px-3 py-2 text-sm">
      {isImage && thumb ? (
        <img
          src={thumb}
          alt={file.name}
          className="w-10 h-10 rounded-md object-cover flex-shrink-0"
        />
      ) : (
        <div className="w-10 h-10 rounded-md bg-purple-100 flex items-center justify-center text-purple-600 flex-shrink-0">
          <FileText size={20} />
        </div>
      )}
      <div className="min-w-0">
        <p className="text-gray-800 truncate max-w-[140px]">{file.name}</p>
        <p className="text-xs text-gray-500">{(file.size / 1024).toFixed(0)} KB</p>
      </div>
      <button
        type="button"
        onClick={onRemove}
        className="text-gray-400 hover:text-red-500 ml-1"
      >
        <X size={16} />
      </button>
    </div>
  );
}

function AttachmentList({
  attachments,
  onUserBubble,
}: {
  attachments: Attachment[];
  onUserBubble: boolean;
}) {
  return (
    <div className={`flex flex-wrap gap-2 ${onUserBubble ? "mb-2" : ""}`}>
      {attachments.map((a) =>
        a.content_type.startsWith("image/") ? (
          <AttachmentImage key={a.id} attachment={a} onUserBubble={onUserBubble} />
        ) : (
          <AttachmentFile key={a.id} attachment={a} onUserBubble={onUserBubble} />
        ),
      )}
    </div>
  );
}

function AttachmentImage({
  attachment,
  onUserBubble,
}: {
  attachment: Attachment;
  onUserBubble: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (attachment.id < 0) return; // optimistic, no URL yet
    let cancelled = false;
    fetchAttachmentBlobUrl(attachment.id).then((u) => {
      if (!cancelled) setUrl(u);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachment.id]);

  if (!url) {
    return (
      <div
        className={`w-32 h-32 rounded-lg flex items-center justify-center ${
          onUserBubble ? "bg-white/10" : "bg-gray-100"
        }`}
      >
        <ImageIcon size={24} className={onUserBubble ? "text-white/60" : "text-gray-400"} />
      </div>
    );
  }

  return (
    <a href={url} target="_blank" rel="noreferrer">
      <img
        src={url}
        alt={attachment.filename}
        className="w-32 h-32 rounded-lg object-cover border border-white/20 hover:opacity-90 transition-opacity"
      />
    </a>
  );
}

function AttachmentFile({
  attachment,
  onUserBubble,
}: {
  attachment: Attachment;
  onUserBubble: boolean;
}) {
  async function open() {
    if (attachment.id < 0) return;
    const u = await fetchAttachmentBlobUrl(attachment.id);
    window.open(u, "_blank");
  }
  return (
    <button
      type="button"
      onClick={open}
      className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition-all max-w-[260px] ${
        onUserBubble
          ? "bg-white/15 hover:bg-white/25 text-white"
          : "bg-gray-50 hover:bg-gray-100 text-gray-700 border border-gray-200"
      }`}
    >
      <div
        className={`w-9 h-9 rounded-md flex items-center justify-center flex-shrink-0 ${
          onUserBubble ? "bg-white/20" : "bg-purple-100 text-purple-600"
        }`}
      >
        <FileText size={18} />
      </div>
      <div className="min-w-0 text-left">
        <p className="truncate">{attachment.filename}</p>
        <p className={`text-xs ${onUserBubble ? "text-white/70" : "text-gray-500"}`}>
          {(attachment.size_bytes / 1024).toFixed(0)} KB
        </p>
      </div>
    </button>
  );
}
