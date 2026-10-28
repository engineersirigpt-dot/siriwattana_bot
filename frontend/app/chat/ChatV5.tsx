"use client";

// V5 chat page (UI_V5_MODE). Same backend calls and rules as the classic page
// (ClassicChatPage.tsx, kept as the rollback); what changed is the UX:
//   - responsive: the sidebar is a drawer on phones/tablets, header and
//     composer shrink, nothing overflows sideways
//   - streaming: tokens are applied once per animation frame, only the
//     streaming message re-renders, the view follows the answer only while
//     the reader is at the bottom ("ไปข้อความล่าสุด" otherwise), หยุด keeps
//     what has arrived and says so
//   - status: the backend's real status events as a step line (no invented
//     progress); a file upload says it's processing files
//   - files: cards with type, preview (image / PDF / CSV / text / the
//     Document Engine's preview picture) and download; gone or forbidden files
//     say so
//   - history: type to filter chat titles instantly, Enter to search inside
//     messages; keyboard: Ctrl+K search, Esc closes the drawer; actions on a
//     chat row show on keyboard focus and on touch screens
//   - loading / error / empty states for the chat list and an opened chat

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDown,
  Bookmark,
  BookmarkCheck,
  Brain,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Download,
  Eye,
  FileText,
  Link2Off,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  Share2,
  Square,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  API_BASE,
  api,
  clearAuth,
  exportAnswerDocx,
  exportAnswerPdf,
  exportAnswerXlsx,
  forkSharedSession,
  getMemory,
  getRole,
  getSessionRecipients,
  getToken,
  getUsername,
  listShareableUsers,
  revokeSessionShare,
  sendChat,
  sendChatStream,
  sendFeedback,
  setUiPreference,
  shareSession,
  type ShareUser,
} from "@/lib/api";
import { AlertModal, ConfirmModal, PromptModal } from "@/components/Modal";
import ThemeToggle from "@/components/ThemeToggle";
import MemoryModal from "@/components/MemoryModal";
import MessageRow, { type MsgV5, type RowActions } from "@/components/chat/MessageRow";
import { PendingFile } from "@/components/chat/FileCard";
import { knownStep, stepsFromSource } from "@/components/chat/StatusLine";
import { useStickyScroll } from "@/components/chat/useStickyScroll";
import {
  DATA_FILE_RE,
  FILE_ACCEPT_ATTR,
  PROOFREAD_RE,
  ShareModal,
  TurnCounter,
  findPriorBotMessage,
  findPriorUserQuestion,
  isAcceptedFile,
  splitAttachments,
  type Attachment,
  type Msg,
  type SearchHit,
  type Session,
  type SharedTeamSession,
  type TeamSession,
} from "./ClassicChatPage";

type LoadedMessage = {
  id?: number;
  question: string;
  answer: string;
  source: string;
  attachments?: Attachment[];
  source_knowledge_id?: number | null;
  source_file?: string | null;
  my_vote?: "up" | "down" | null;
};

// Ends a stopped answer the backend saved (main.STOPPED_NOTE) — shown as the
// "หยุดแล้ว" note instead of text.
const STOPPED_RE = /\s*_\(หยุดก่อนตอบจบ — คำตอบอาจไม่ครบ\)_\s*$/;

function hydrate(loaded: LoadedMessage[]): MsgV5[] {
  const out: MsgV5[] = [];
  for (const m of loaded) {
    const atts = splitAttachments(m.source, m.attachments);
    const stopped = STOPPED_RE.test(m.answer);
    const text = stopped ? m.answer.replace(STOPPED_RE, "") : m.answer;
    out.push({ role: "user", text: m.question, attachments: atts.user });
    out.push({
      role: "bot",
      text,
      source: m.source,
      source_knowledge_id: m.source_knowledge_id ?? null,
      source_file: m.source_file ?? null,
      question: m.question,
      id: m.id,
      myVote: m.my_vote ?? null,
      attachments: atts.bot.length > 0 ? atts.bot : undefined,
      steps: stepsFromSource(m.source, text),
      stopped,
    });
  }
  return out;
}

function byUser<T extends { username: string }>(list: T[]) {
  const g: Record<string, T[]> = {};
  for (const s of list) (g[s.username] ??= []).push(s);
  return g;
}

export default function ChatV5() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsState, setSessionsState] = useState<"loading" | "ok" | "error">("loading");
  const [teamSessions, setTeamSessions] = useState<TeamSession[]>([]);
  const [sharedTeamSessions, setSharedTeamSessions] = useState<SharedTeamSession[]>([]);
  const [currentSid, setCurrentSid] = useState<number | null>(null);
  const [messages, setMessages] = useState<MsgV5[]>([]);
  const [chatState, setChatState] = useState<"idle" | "loading" | "error">("idle");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [role, setRole] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [memoryAvailable, setMemoryAvailable] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Session | null>(null);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);
  const [adminDeleteTarget, setAdminDeleteTarget] = useState<{ sid: number; owner: string; title: string } | null>(null);
  const [alertMsg, setAlertMsg] = useState<string | null>(null);
  const [teamExpanded, setTeamExpanded] = useState(false);
  const [sharedTeamExpanded, setSharedTeamExpanded] = useState(false);
  const [readOnlyOwner, setReadOnlyOwner] = useState<string | null>(null);
  const [readOnlyToken, setReadOnlyToken] = useState<string | null>(null);
  const [forkingInline, setForkingInline] = useState(false);
  const [folderOpen, setFolderOpen] = useState<Record<string, boolean>>({ บันทึกไว้: true, ล่าสุด: true });
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [turnCount, setTurnCount] = useState(0);
  const [turnLimit, setTurnLimit] = useState(20);
  const [sharedToken, setSharedToken] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareUsers, setShareUsers] = useState<ShareUser[]>([]);
  const [shareSel, setShareSel] = useState<Set<number>>(new Set());
  const [copiedMsgIdx, setCopiedMsgIdx] = useState<number | null>(null);
  const [reasonTarget, setReasonTarget] = useState<{ index: number; messageId: number } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const loadSeq = useRef(0);
  const { atBottom, scrollToBottom } = useStickyScroll(scrollRef, messages);

  // ── start ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setRole(getRole());
    setUsername(getUsername());
    getMemory()
      .then((m) => setMemoryAvailable(m.available))
      .catch(() => setMemoryAvailable(false));
    refreshSessions();
    refreshSharedTeamSessions();
    const sid = parseInt(new URLSearchParams(window.location.search).get("sid") ?? "", 10);
    if (!Number.isNaN(sid)) {
      loadSession(sid);
      window.history.replaceState({}, "", "/chat");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  useEffect(() => {
    if (role === "admin") refreshTeamSessions();
  }, [role]);

  // keyboard: Ctrl/Cmd+K → search, Esc → close the drawer
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setNavOpen(true);
        setTimeout(() => searchRef.current?.focus(), 50);
      } else if (e.key === "Escape" && navOpen) {
        setNavOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // auto-grow the composer (up to ~8 lines)
  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [input]);

  // ── data ─────────────────────────────────────────────────────────────────
  async function refreshSessions() {
    try {
      setSessions(await api<Session[]>("/chat/sessions"));
      setSessionsState("ok");
    } catch {
      setSessionsState((s) => (s === "loading" ? "error" : s));
    }
  }
  async function refreshTeamSessions() {
    try {
      setTeamSessions(await api<TeamSession[]>("/admin/chat-history"));
    } catch {
      /* admin-only; nothing to show */
    }
  }
  async function refreshSharedTeamSessions() {
    try {
      setSharedTeamSessions(await api<SharedTeamSession[]>("/chat/shared-sessions"));
    } catch {
      /* postgres-only endpoint */
    }
  }

  function openChat(sid: number, owner: string | null, token: string | null) {
    setCurrentSid(sid);
    setSearchResults(null);
    setReadOnlyOwner(owner);
    setReadOnlyToken(token);
    setNavOpen(false);
    setChatState("loading");
    setMessages([]);
  }

  async function loadSession(sid: number) {
    const seq = ++loadSeq.current;
    openChat(sid, null, null);
    try {
      const data = await api<{ messages: LoadedMessage[]; turn_count?: number; shared_token?: string | null }>(
        `/chat/sessions/${sid}`,
      );
      if (seq !== loadSeq.current) return;
      setMessages(hydrate(data.messages));
      setTurnCount(data.turn_count ?? 0);
      setSharedToken(data.shared_token ?? null);
      setChatState("idle");
      requestAnimationFrame(() => scrollToBottom(false));
    } catch {
      if (seq === loadSeq.current) setChatState("error");
    }
  }

  async function loadTeamSession(sid: number, owner: string) {
    const seq = ++loadSeq.current;
    openChat(sid, owner, null);
    try {
      const data = await api<{ messages: LoadedMessage[] }>(`/admin/chat-history/${sid}`);
      if (seq !== loadSeq.current) return;
      setMessages(hydrate(data.messages));
      setChatState("idle");
      requestAnimationFrame(() => scrollToBottom(false));
    } catch {
      if (seq === loadSeq.current) setChatState("error");
    }
  }

  async function loadSharedTeamSession(sid: number, owner: string, token: string) {
    const seq = ++loadSeq.current;
    openChat(sid, owner, token);
    try {
      const data = await api<{ messages: LoadedMessage[] }>(`/chat/shared/${token}`);
      if (seq !== loadSeq.current) return;
      setMessages(hydrate(data.messages));
      setChatState("idle");
      requestAnimationFrame(() => scrollToBottom(false));
    } catch {
      if (seq !== loadSeq.current) return;
      setReadOnlyOwner(null);
      setReadOnlyToken(null);
      setCurrentSid(null);
      setChatState("idle");
      setAlertMsg(`${owner} ยกเลิกการแชร์แชทนี้แล้ว — เปิดดูไม่ได้อีก`);
      refreshSharedTeamSessions();
    }
  }

  function retryOpen() {
    if (currentSid === null) return;
    if (readOnlyToken && readOnlyOwner) loadSharedTeamSession(currentSid, readOnlyOwner, readOnlyToken);
    else if (readOnlyOwner) loadTeamSession(currentSid, readOnlyOwner);
    else loadSession(currentSid);
  }

  async function handleForkInline() {
    if (!readOnlyToken || forkingInline) return;
    setForkingInline(true);
    try {
      const { session_id } = await forkSharedSession(readOnlyToken);
      await refreshSessions();
      await loadSession(session_id);
    } catch (e) {
      setAlertMsg("รับเป็นแชทของฉันไม่สำเร็จ: " + (e instanceof Error ? e.message : "เกิดข้อผิดพลาด"));
    } finally {
      setForkingInline(false);
    }
  }

  async function unshareFromPanel(sid: number) {
    try {
      await revokeSessionShare(sid);
      if (currentSid === sid && readOnlyToken) exitReadOnly();
      if (sid === currentSid) setSharedToken(null);
      refreshSharedTeamSessions();
    } catch (e) {
      setAlertMsg("ยกเลิกการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  // ── message actions (stable object, so memoised rows don't re-render) ────
  const actionsRef = useRef<RowActions>(null as unknown as RowActions);
  actionsRef.current = {
    copy: async (index, text) => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
        } catch {
          /* ignore */
        }
        ta.remove();
      }
      setCopiedMsgIdx(index);
      setTimeout(() => setCopiedMsgIdx((c) => (c === index ? null : c)), 1500);
    },
    thumb: (index, messageId, vote) => {
      castVote(index, messageId, vote);
      if (vote === "down") setReasonTarget({ index, messageId });
    },
    exportPdf: (m) =>
      exportAnswerPdf({ content: m.text, userQuestion: m.question }).catch((e) =>
        setAlertMsg("Export PDF ไม่สำเร็จ: " + (e instanceof Error ? e.message : "")),
      ),
    exportDocx: (text) =>
      exportAnswerDocx({ content: text }).catch((e) => setAlertMsg("Export Word ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""))),
    exportXlsx: (text) =>
      exportAnswerXlsx({ content: text }).catch((e) => setAlertMsg("Export Excel ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""))),
    alert: (msg) => setAlertMsg(msg),
  };
  const actions = useMemo<RowActions>(
    () => ({
      copy: (...a) => actionsRef.current.copy(...a),
      thumb: (...a) => actionsRef.current.thumb(...a),
      exportPdf: (...a) => actionsRef.current.exportPdf(...a),
      exportDocx: (...a) => actionsRef.current.exportDocx(...a),
      exportXlsx: (...a) => actionsRef.current.exportXlsx(...a),
      alert: (...a) => actionsRef.current.alert(...a),
    }),
    [],
  );

  async function castVote(index: number, messageId: number, vote: "up" | "down", reason?: string) {
    setMessages((m) => {
      const copy = [...m];
      if (copy[index]) copy[index] = { ...copy[index], myVote: vote };
      return copy;
    });
    try {
      await sendFeedback(messageId, vote, reason);
    } catch (e) {
      setAlertMsg("ส่งความคิดเห็นไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }

  // ── chat list actions ─────────────────────────────────────────────────────
  function newChat() {
    abortRef.current?.abort();
    loadSeq.current++;
    setCurrentSid(null);
    setMessages([]);
    setChatState("idle");
    setSearchResults(null);
    setReadOnlyOwner(null);
    setReadOnlyToken(null);
    setInput("");
    setPendingFiles([]);
    setTurnCount(0);
    setSharedToken(null);
    setNavOpen(false);
    setTimeout(() => inputRef.current?.focus(), 50);
  }

  function exitReadOnly() {
    setReadOnlyOwner(null);
    setReadOnlyToken(null);
    setCurrentSid(null);
    setMessages([]);
  }

  async function performRename(sid: number, title: string) {
    try {
      await api(`/chat/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) });
      refreshSessions();
    } catch (e) {
      setAlertMsg("เปลี่ยนชื่อไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }
  async function performDelete(sid: number) {
    try {
      await api(`/chat/sessions/${sid}`, { method: "DELETE" });
      if (sid === currentSid) newChat();
      refreshSessions();
    } catch (e) {
      setAlertMsg("ลบไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }
  async function performAdminDelete(sid: number) {
    try {
      await api(`/admin/chat-history/${sid}`, { method: "DELETE" });
      if (sid === currentSid && readOnlyOwner) exitReadOnly();
      refreshTeamSessions();
      refreshSharedTeamSessions();
    } catch (e) {
      setAlertMsg("ลบไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }
  async function performToggleSave(sid: number, saved: boolean) {
    try {
      await api(`/chat/sessions/${sid}/save`, { method: "PATCH", body: JSON.stringify({ is_saved: !saved }) });
      refreshSessions();
    } catch (e) {
      setAlertMsg("บันทึกไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    }
  }
  function exportSession(sid: number) {
    fetch(`${API_BASE}/chat/sessions/${sid}/export`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      })
      .then((b) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = `session-${sid}.csv`;
        a.click();
      })
      .catch(() => setAlertMsg("ดาวน์โหลดแชทไม่สำเร็จ"));
  }

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = search.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    try {
      setSearchResults(await api<SearchHit[]>(`/chat/search?q=${encodeURIComponent(q)}`));
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }

  // ── share (same flow as classic) ─────────────────────────────────────────
  async function handleShareToggle() {
    if (!currentSid) return;
    setShareBusy(true);
    setShareOpen(true);
    try {
      if (role === "admin") {
        const [users, current] = await Promise.all([listShareableUsers(), getSessionRecipients(currentSid)]);
        setShareUsers(users);
        setShareSel(new Set(current));
      } else if (!sharedToken) {
        const { token } = await shareSession(currentSid, []);
        setSharedToken(token);
        refreshSharedTeamSessions();
      }
    } catch (e) {
      setAlertMsg("เปิดการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
      setShareOpen(false);
    } finally {
      setShareBusy(false);
    }
  }
  async function unshareCurrent() {
    if (!currentSid) return;
    setShareBusy(true);
    try {
      await revokeSessionShare(currentSid);
      setSharedToken(null);
      refreshSharedTeamSessions();
      setShareOpen(false);
    } catch (e) {
      setAlertMsg("ยกเลิกการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    } finally {
      setShareBusy(false);
    }
  }
  async function saveShare() {
    if (!currentSid) return;
    setShareBusy(true);
    try {
      const { token } = await shareSession(currentSid, [...shareSel]);
      setSharedToken(token);
      refreshSharedTeamSessions();
      if (!token) setShareOpen(false);
    } catch (e) {
      setAlertMsg("ทำรายการแชร์ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    } finally {
      setShareBusy(false);
    }
  }
  useEffect(() => {
    if (!shareOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setShareOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shareOpen]);

  // ── files ─────────────────────────────────────────────────────────────────
  function addFiles(files: File[]) {
    if (readOnlyOwner) return;
    const valid: File[] = [];
    for (const f of files) {
      if (f.size > 100 * 1024 * 1024) setAlertMsg(`ไฟล์ "${f.name}" ใหญ่เกิน 100MB`);
      else if (!isAcceptedFile(f)) setAlertMsg(`ไฟล์ "${f.name}" ไม่รองรับ`);
      else valid.push(f);
    }
    if (!valid.length) return;
    setPendingFiles((prev) => {
      if (prev.length + valid.length > 5) setAlertMsg("แนบไฟล์ได้สูงสุด 5 ไฟล์ต่อข้อความ");
      return [...prev, ...valid].slice(0, 5);
    });
  }
  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    if (!readOnlyOwner && !sending) addFiles(Array.from(e.dataTransfer.files ?? []));
  }
  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    if (readOnlyOwner || sending) return;
    const images: File[] = [];
    for (const item of Array.from(e.clipboardData?.items ?? [])) {
      if (item.kind === "file" && item.type.startsWith("image/")) {
        const blob = item.getAsFile();
        if (!blob) continue;
        const ext = (blob.type.split("/")[1] || "png").split("+")[0];
        images.push(new File([blob], `pasted-${Date.now()}-${images.length + 1}.${ext}`, { type: blob.type }));
      }
    }
    if (images.length) {
      e.preventDefault();
      addFiles(images);
    }
  }

  // ── sending ───────────────────────────────────────────────────────────────
  const patchLastBot = useCallback(
    (patch: Partial<MsgV5> | ((m: MsgV5) => Partial<MsgV5>)) =>
      setMessages((m) => {
        const copy = [...m];
        const last = copy.length - 1;
        if (copy[last]?.role === "bot") {
          copy[last] = { ...copy[last], ...(typeof patch === "function" ? patch(copy[last]) : patch) };
        }
        return copy;
      }),
    [],
  );

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if ((!text && pendingFiles.length === 0) || sending || readOnlyOwner) return;
    const files = pendingFiles;
    const optimistic: Attachment[] = files.map((f, i) => ({
      id: -(i + 1),
      filename: f.name,
      content_type: f.type,
      size_bytes: f.size,
      previewUrl: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined,
    }));
    setMessages((m) => [...m, { role: "user", text: text || "[ไฟล์แนบ]", attachments: optimistic }]);
    setInput("");
    setPendingFiles([]);
    setSending(true);
    inputRef.current?.focus();
    requestAnimationFrame(() => scrollToBottom(true));
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (files.length) await runNonStream(text, files, controller);
      else await runStreaming(text, controller);
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  // Files go through /chat (no streaming). A placeholder answer shows the wait.
  async function runNonStream(text: string, files: File[], controller: AbortController) {
    const proof = files.length > 0 && PROOFREAD_RE.test(text);
    const heavy = files.some((f) => DATA_FILE_RE.test(f.name));
    setMessages((m) => [
      ...m,
      {
        role: "bot",
        text: "",
        question: text,
        streaming: true,
        startedAt: Date.now(),
        waitLabel: files.length ? "กำลังประมวลผลไฟล์…" : undefined,
        waitHint: proof ? "งานตรวจคำผิดอย่างละเอียดอาจใช้เวลา 1–2 นาที" : heavy ? "ไฟล์ข้อมูลหรือเอกสารอาจใช้เวลา 1–2 นาที" : undefined,
      },
    ]);
    try {
      const res = await sendChat({ message: text, sessionId: currentSid, files, signal: controller.signal });
      const atts = splitAttachments(res.source, res.attachments);
      const isImageResult = res.source === "image_edit" || res.source === "image_gen";
      setMessages((m) => {
        const copy = [...m];
        const botIdx = copy.length - 1;
        const userIdx = botIdx - 1;
        if (files.length && copy[userIdx]?.role === "user" && !isImageResult) {
          copy[userIdx] = { ...copy[userIdx], attachments: atts.user };
        }
        copy[botIdx] = {
          role: "bot",
          text: res.answer,
          source: res.source,
          source_knowledge_id: res.source_knowledge_id ?? null,
          source_file: res.source_file ?? null,
          question: text,
          id: res.message_id ?? undefined,
          myVote: null,
          attachments: atts.bot.length ? atts.bot : undefined,
        };
        return copy;
      });
      setCurrentSid(res.session_id);
      if (typeof res.turn_count === "number") setTurnCount(res.turn_count);
      if (typeof res.turn_limit === "number") setTurnLimit(res.turn_limit);
      refreshSessions();
      if (role === "admin") refreshTeamSessions();
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      patchLastBot({
        streaming: false,
        text: aborted ? "ยกเลิกการถามแล้ว" : `⚠️ เกิดข้อผิดพลาด: ${e instanceof Error ? e.message : ""}`,
      });
    }
  }

  async function runStreaming(text: string, controller: AbortController) {
    setMessages((m) => [
      ...m,
      { role: "bot", text: "", source: "", question: text, myVote: null, streaming: true, steps: [], startedAt: Date.now() },
    ]);
    // Coalesce tokens: one state update per animation frame, not per token.
    let pending = "";
    let frame: number | null = null;
    const flush = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      if (!pending) return;
      const t = pending;
      pending = "";
      patchLastBot((last) => ({ text: last.text + t }));
    };
    let receivedAny = false;
    let sentText = false;
    try {
      await sendChatStream(
        { message: text, sessionId: currentSid, signal: controller.signal },
        {
          onDelta: (t) => {
            receivedAny = sentText = true;
            pending += t;
            if (frame === null) frame = requestAnimationFrame(flush);
          },
          onSession: (s) => {
            receivedAny = true;
            if (currentSid === null) {
              // a new chat: it exists now, even if this answer gets stopped
              setCurrentSid(s.session_id);
              refreshSessions();
            }
          },
          onStatus: (state) => {
            if (state === "generating_image") patchLastBot({ generatingImage: true });
            else if (knownStep(state)) patchLastBot((last) => ({ steps: [...(last.steps ?? []), state] }));
          },
          onDone: (meta) => {
            flush();
            patchLastBot((last) => ({
              source: meta.source,
              source_knowledge_id: meta.source_knowledge_id ?? null,
              source_file: meta.source_file ?? null,
              id: meta.message_id ?? undefined,
              streaming: false,
              generatingImage: false,
              // steps that sent no status (legacy web/link answers) from the source
              steps: last.steps?.length ? last.steps : stepsFromSource(meta.source, last.text),
              ...(meta.attachments?.length ? { attachments: meta.attachments } : {}),
            }));
            setCurrentSid(meta.session_id);
            if (typeof meta.turn_count === "number") setTurnCount(meta.turn_count);
            if (typeof meta.turn_limit === "number") setTurnLimit(meta.turn_limit);
            refreshSessions();
            if (role === "admin") refreshTeamSessions();
          },
          onError: (detail) => {
            receivedAny = true;
            flush();
            patchLastBot((last) => ({ text: last.text + (last.text ? "\n\n" : "") + `⚠️ ${detail}`, streaming: false }));
          },
        },
      );
    } catch (e) {
      flush();
      const aborted = e instanceof DOMException && e.name === "AbortError";
      if (aborted) {
        patchLastBot((last) => (last.text ? { stopped: true } : { text: "ยกเลิกการถามแล้ว" }));
        if (sentText) {
          // the backend saves the text it had sent (marked as stopped) — one more turn
          setTurnCount((c) => c + 1);
          refreshSessions();
        }
      } else if (!receivedAny) {
        // never started (e.g. sqlite → 501): drop the bubble, use /chat instead
        setMessages((m) => (m[m.length - 1]?.role === "bot" && !m[m.length - 1].text ? m.slice(0, -1) : m));
        await runNonStream(text, [], controller);
        return;
      } else {
        patchLastBot((last) => ({ text: last.text + "\n\n⚠️ การเชื่อมต่อขัดข้อง" }));
      }
    } finally {
      flush();
      patchLastBot((last) => (last.streaming ? { streaming: false, generatingImage: false } : {}));
    }
  }

  function cancelSend() {
    abortRef.current?.abort();
  }

  function onComposerKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  function logout() {
    clearAuth();
    router.replace("/login");
  }

  function backToClassic() {
    setUiPreference("classic");
    window.location.reload();
  }

  // ── derived ───────────────────────────────────────────────────────────────
  const q = search.trim().toLowerCase();
  const visibleSessions = q ? sessions.filter((s) => s.title.toLowerCase().includes(q)) : sessions;
  const groups: [string, Session[]][] = [
    ["บันทึกไว้", visibleSessions.filter((s) => s.is_saved)],
    ["ล่าสุด", visibleSessions.filter((s) => !s.is_saved)],
  ];
  const teamByUser = byUser(teamSessions);
  const sharedByUser = byUser(sharedTeamSessions);
  const currentTitle = currentSid
    ? readOnlyOwner
      ? [...teamSessions, ...sharedTeamSessions].find((s) => s.id === currentSid)?.title ?? "บทสนทนา"
      : sessions.find((s) => s.id === currentSid)?.title ?? "แชท"
    : "แชทใหม่";
  const atLimit = turnCount >= turnLimit;
  const exportTargets = useMemo(() => {
    const out: Record<number, { message: Msg | null; question?: string }> = {};
    messages.forEach((m, i) => {
      if (m.role === "bot" && m.source === "export_offer") {
        out[i] = { message: findPriorBotMessage(messages, i), question: findPriorUserQuestion(messages, i) };
      }
    });
    return out;
  }, [messages]);

  // ── render ────────────────────────────────────────────────────────────────
  const sideSection = "rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white hover:bg-white/10";
  return (
    <div
      className="relative flex h-[100dvh] w-full overflow-hidden bg-surface2"
      data-ui="v5"
      onDragOver={(e) => {
        e.preventDefault();
        if (!readOnlyOwner && !sending) setIsDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setIsDragging(false);
      }}
      onDrop={handleDrop}
    >
      {isDragging && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-purple-900/20 backdrop-blur-sm">
          <div className="rounded-3xl border-2 border-dashed border-purple-400 bg-surface px-8 py-6 text-center shadow-2xl">
            <Paperclip size={32} className="mx-auto mb-2 text-purple-500" />
            <p className="font-semibold text-purple-700">วางไฟล์ที่นี่เพื่อแนบในแชท</p>
            <p className="mt-1 text-sm text-muted">รูปภาพ, PDF, Word, Excel, PowerPoint, เสียง และไฟล์ข้อความ — สูงสุด 5 ไฟล์</p>
          </div>
        </div>
      )}

      {/* drawer backdrop (phones / tablets) */}
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} aria-hidden data-testid="nav-backdrop" />
      )}

      {/* ── sidebar ── */}
      <aside
        data-testid="sidebar"
        aria-label="ประวัติแชท"
        className={`fixed inset-y-0 left-0 z-40 flex w-[85vw] max-w-[20rem] flex-col bg-gradient-to-b from-purple-500 via-purple-600 to-purple-700 transition-transform duration-200
          md:static md:z-auto md:w-72 md:max-w-none md:translate-x-0 md:shadow-2xl lg:w-80 ${navOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full"}`}
      >
        <div className="flex items-center gap-2 p-3 md:p-4">
          <button onClick={newChat} className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-2.5 text-white shadow-lg transition-all hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70">
            <Plus size={18} /> <span>แชทใหม่</span>
          </button>
          <button onClick={() => setNavOpen(false)} className="ui-icon-btn-on-color md:hidden" aria-label="ปิดเมนู">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={runSearch} className="px-3 pb-2 md:px-4" role="search">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-white/80" size={16} aria-hidden />
            <input
              ref={searchRef}
              type="search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                if (!e.target.value.trim()) setSearchResults(null);
              }}
              placeholder="ค้นหาแชท…  (Ctrl+K)"
              aria-label="ค้นหาแชท"
              className="w-full rounded-lg border border-white/20 bg-white/10 py-2 pl-9 pr-8 text-sm text-white placeholder-white/70 focus:outline-none focus:ring-2 focus:ring-white/40"
            />
            {searching && <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-white/80" />}
          </div>
          {q && !searchResults && (
            <button type="submit" className="mt-1.5 w-full rounded-md px-2 py-1 text-left text-xs text-white/85 hover:bg-white/10">
              กด Enter เพื่อค้นหาในข้อความทั้งหมด
            </button>
          )}
        </form>

        {role === "admin" && (
          <div className="px-3 pb-2 md:px-4">
            <button onClick={() => setTeamExpanded(!teamExpanded)} className={`flex w-full items-center justify-between ${sideSection}`} aria-expanded={teamExpanded}>
              <span className="flex items-center gap-2"><Users size={15} /> แชทของทีม <span className="rounded-full bg-white/20 px-1.5 text-xs">{teamSessions.length}</span></span>
              {teamExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            </button>
            {teamExpanded && (
              <div className="mt-1.5 max-h-56 space-y-2 overflow-y-auto rounded-lg bg-black/20 p-2">
                {Object.keys(teamByUser).length === 0 && <p className="py-2 text-center text-xs text-white/85">ยังไม่มีบทสนทนา</p>}
                {Object.entries(teamByUser).map(([uname, list]) => (
                  <div key={uname}>
                    <div className="px-2 py-1 text-xs font-medium text-white/85">{uname}</div>
                    {list.map((s) => (
                      <SideRow
                        key={s.id}
                        small
                        active={s.id === currentSid && readOnlyOwner === uname && !readOnlyToken}
                        icon={<Eye size={12} className="opacity-70" />}
                        title={s.title}
                        onOpen={() => loadTeamSession(s.id, uname)}
                        actions={[{ icon: <Trash2 size={12} />, label: `ลบบทสนทนานี้ของ ${uname}`, danger: true, run: () => setAdminDeleteTarget({ sid: s.id, owner: uname, title: s.title }) }]}
                      />
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="px-3 pb-2 md:px-4">
          <button onClick={() => setSharedTeamExpanded(!sharedTeamExpanded)} className={`flex w-full items-center justify-between ${sideSection}`} aria-expanded={sharedTeamExpanded}>
            <span className="flex items-center gap-2"><Share2 size={15} /> แชร์ร่วมกันในทีม <span className="rounded-full bg-white/20 px-1.5 text-xs">{sharedTeamSessions.length}</span></span>
            {sharedTeamExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
          {sharedTeamExpanded && (
            <div className="mt-1.5 max-h-56 space-y-2 overflow-y-auto rounded-lg bg-black/20 p-2">
              {Object.keys(sharedByUser).length === 0 && <p className="py-2 text-center text-xs text-white/85">ยังไม่มีแชทที่ถูกแชร์</p>}
              {Object.entries(sharedByUser).map(([uname, list]) => {
                const mine = uname === username;
                return (
                  <div key={uname}>
                    <div className="px-2 py-1 text-xs font-medium text-white/85">{uname}{mine && <span className="ml-1 text-purple-200">(คุณ)</span>}</div>
                    {list.map((s) => (
                      <SideRow
                        key={s.id}
                        small
                        active={s.id === currentSid && readOnlyToken === s.shared_token}
                        icon={<Share2 size={12} className="opacity-70" />}
                        title={s.title}
                        onOpen={() => loadSharedTeamSession(s.id, uname, s.shared_token)}
                        actions={
                          mine
                            ? [{ icon: <Link2Off size={12} />, label: "ยกเลิกการแชร์ (แชทยังอยู่ของคุณ)", run: () => unshareFromPanel(s.id) }]
                            : role === "admin"
                              ? [{ icon: <Trash2 size={12} />, label: `ลบบทสนทนานี้ของ ${uname}`, danger: true, run: () => setAdminDeleteTarget({ sid: s.id, owner: uname, title: s.title }) }]
                              : []
                        }
                      />
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-3 overflow-y-auto px-3 pb-3 md:px-4" aria-label="แชทของฉัน" data-testid="session-list">
          {searchResults ? (
            <div>
              <div className="mb-2 flex items-center justify-between text-xs text-white/85">
                <span>พบในข้อความ {searchResults.length} รายการ</span>
                <button onClick={() => setSearchResults(null)} className="rounded px-1.5 py-0.5 hover:bg-white/10">ปิด</button>
              </div>
              <div className="space-y-1">
                {searchResults.map((h) => (
                  <button
                    key={h.id}
                    data-testid="search-hit"
                    onClick={() => loadSession(h.session_id)}
                    className="w-full rounded-lg bg-white/5 p-2.5 text-left transition-all hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
                  >
                    <div className="truncate text-xs text-white/80">{h.session_title}</div>
                    <div className="mt-0.5 line-clamp-2 text-sm text-white">{h.question}</div>
                  </button>
                ))}
                {searchResults.length === 0 && <div className="px-1 text-sm text-white/85">ไม่พบข้อความที่ตรงกัน</div>}
              </div>
            </div>
          ) : sessionsState === "loading" ? (
            <div className="space-y-2" aria-busy="true" data-testid="sessions-loading">
              {[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-9 animate-pulse rounded-lg bg-white/10" />)}
            </div>
          ) : sessionsState === "error" ? (
            <div className="rounded-lg bg-black/20 p-3 text-sm text-white" role="alert">
              โหลดรายการแชทไม่สำเร็จ
              <button onClick={() => { setSessionsState("loading"); refreshSessions(); }} className="mt-2 flex items-center gap-1 rounded-md bg-white/15 px-2 py-1 text-xs hover:bg-white/25">
                <RefreshCw size={12} /> ลองใหม่
              </button>
            </div>
          ) : visibleSessions.length === 0 ? (
            <p className="px-1 py-2 text-sm text-white/85">{q ? "ไม่พบชื่อแชทที่ตรงกัน — กด Enter เพื่อค้นในข้อความ" : "ยังไม่มีแชท เริ่มถามได้เลย"}</p>
          ) : (
            groups.map(([label, list]) =>
              list.length === 0 ? null : (
                <div key={label}>
                  <button
                    onClick={() => setFolderOpen((p) => ({ ...p, [label]: !(p[label] ?? true) }))}
                    className="mb-1 flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-semibold uppercase tracking-wide text-white/85 hover:bg-white/10"
                    aria-expanded={folderOpen[label] ?? true}
                  >
                    {folderOpen[label] ?? true ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    {label === "บันทึกไว้" ? <BookmarkCheck size={13} className="text-yellow-300" /> : null}
                    <span className="flex-1 text-left">{label}</span>
                    <span className="rounded-full bg-white/10 px-1.5 font-normal">{list.length}</span>
                  </button>
                  {(folderOpen[label] ?? true) && (
                    <div className="space-y-0.5">
                      {list.map((s) => (
                        <SideRow
                          key={s.id}
                          active={s.id === currentSid && !readOnlyOwner}
                          icon={<MessageSquare size={15} />}
                          title={s.title}
                          onOpen={() => loadSession(s.id)}
                          actions={[
                            { icon: s.is_saved ? <BookmarkCheck size={14} className="text-yellow-300" /> : <Bookmark size={14} />, label: s.is_saved ? "เลิกบันทึก" : "บันทึกแชทนี้ไว้", run: () => performToggleSave(s.id, !!s.is_saved) },
                            { icon: <Pencil size={14} />, label: "เปลี่ยนชื่อ", run: () => setRenameTarget(s) },
                            { icon: <Download size={14} />, label: "ดาวน์โหลด CSV", run: () => exportSession(s.id) },
                            { icon: <Trash2 size={14} />, label: "ลบ", danger: true, run: () => setDeleteTargetId(s.id) },
                          ]}
                        />
                      ))}
                    </div>
                  )}
                </div>
              ),
            )
          )}
        </nav>

        <div className="space-y-1 border-t border-white/20 p-3 md:p-4">
          <div className="mb-1 flex items-center gap-3 rounded-xl border border-white/10 bg-white/5 px-2 py-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/Logo_siri.jpg" alt="" className="h-9 w-9 flex-shrink-0 rounded-lg bg-surface object-cover p-0.5" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-white">{username ?? "—"}</p>
              <p className="text-xs text-white/85">{role === "admin" ? "⭐ Administrator" : "User"}</p>
            </div>
          </div>
          {role === "admin" && (
            <button onClick={() => router.push("/admin")} className="ui-side-btn"><Settings size={16} /> Admin Dashboard</button>
          )}
          <button onClick={() => router.push("/translate")} className="ui-side-btn"><FileText size={16} /> แปลเอกสาร</button>
          {memoryAvailable && (
            <button onClick={() => setShowMemory(true)} className="ui-side-btn" data-testid="memory-menu"><Brain size={16} /> ความจำของฉัน</button>
          )}
          <ThemeToggle showLabel className="ui-side-btn" />
          <button onClick={backToClassic} className="ui-side-btn" data-testid="back-to-classic"><RotateCcw size={16} /> กลับไปหน้าแชทแบบเดิม</button>
          <button onClick={logout} className="ui-side-btn"><LogOut size={16} /> ออกจากระบบ</button>
        </div>
      </aside>

      {/* ── main ── */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-line bg-surface px-3 py-2.5 shadow-sm sm:px-6 sm:py-3">
          <button onClick={() => setNavOpen(true)} className="ui-icon-btn md:hidden" aria-label="เปิดเมนู" data-testid="open-nav">
            <Menu size={20} />
          </button>
          <h1 className="min-w-0 flex-1 truncate font-medium text-content" title={currentTitle}>{currentTitle}</h1>
          <div className="flex flex-shrink-0 items-center gap-2">
            {!readOnlyOwner && currentSid !== null && <TurnCounter count={turnCount} limit={turnLimit} onNewChat={newChat} />}
            {!readOnlyOwner && currentSid !== null && messages.length > 0 && (
              <button
                onClick={handleShareToggle}
                className={`ui-btn ${sharedToken ? "border border-green-300 bg-green-50 text-green-700 hover:bg-green-100 dark:bg-green-900/30" : "ui-btn-ghost"}`}
                aria-label={sharedToken ? "แชทนี้ถูกแชร์อยู่ — จัดการการแชร์" : "แชร์แชทนี้"}
              >
                <Share2 size={14} /> <span className="hidden sm:inline">{sharedToken ? "แชร์อยู่" : "แชร์"}</span>
              </button>
            )}
            {readOnlyOwner && (
              <button onClick={exitReadOnly} className="ui-btn border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100">
                <X size={14} /> <span className="hidden sm:inline">ออกจากโหมดอ่าน</span>
              </button>
            )}
            <div className="hidden items-center gap-2 rounded-xl bg-purple-500 py-1 pl-1 pr-3 lg:flex">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/Logo_siri.jpg" alt="Sirivatana" className="h-7 w-7 rounded-md bg-surface object-cover p-0.5" />
              <span className="whitespace-nowrap text-sm font-semibold text-white">ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน)</span>
            </div>
          </div>
        </header>

        {readOnlyOwner && (
          <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 sm:px-6">
            <Eye size={15} />
            <span className="min-w-0 flex-1">คุณกำลังดูบทสนทนาของ <strong>{readOnlyOwner}</strong> — โหมดอ่านอย่างเดียว</span>
            {readOnlyToken && readOnlyOwner !== username && (
              <button onClick={handleForkInline} disabled={forkingInline} className="ui-btn ui-btn-primary px-3 py-1 text-xs">
                {forkingInline ? <Loader2 size={13} className="animate-spin" /> : <ClipboardList size={13} />} รับเป็นแชทของฉัน
              </button>
            )}
          </div>
        )}

        <div className="relative min-h-0 flex-1">
          <div ref={scrollRef} className="h-full overflow-y-auto overflow-x-hidden bg-gradient-to-b from-app/30 to-surface" data-testid="scroller">
            <div className="mx-auto max-w-4xl space-y-6 px-3 py-6 sm:px-6 sm:py-8">
              {chatState === "loading" && (
                <div className="space-y-6" aria-busy="true" data-testid="chat-loading">
                  <div className="ml-auto h-12 w-2/3 max-w-md animate-pulse rounded-2xl bg-purple-200/60 dark:bg-purple-900/40" />
                  <div className="h-24 w-full animate-pulse rounded-2xl bg-surface3" />
                  <div className="ml-auto h-10 w-1/2 max-w-sm animate-pulse rounded-2xl bg-purple-200/60 dark:bg-purple-900/40" />
                </div>
              )}
              {chatState === "error" && (
                <div className="mx-auto mt-10 max-w-sm rounded-2xl border border-line bg-surface p-5 text-center shadow-sm" role="alert">
                  <p className="font-medium text-content">เปิดแชทนี้ไม่สำเร็จ</p>
                  <p className="mt-1 text-sm text-muted">แชทอาจถูกลบ หรือการเชื่อมต่อขัดข้อง</p>
                  <div className="mt-3 flex justify-center gap-2">
                    <button onClick={retryOpen} className="ui-btn ui-btn-primary"><RefreshCw size={14} /> ลองใหม่</button>
                    <button onClick={newChat} className="ui-btn ui-btn-ghost">แชทใหม่</button>
                  </div>
                </div>
              )}
              {chatState === "idle" && messages.length === 0 && (
                <div className="mt-16 text-center text-faint sm:mt-20" data-testid="empty-state">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src="/Logo_siri.jpg" alt="Sirivatana" className="mx-auto mb-4 h-16 w-16 rounded-2xl object-cover shadow-md sm:h-20 sm:w-20" />
                  <p className="text-content2">พิมพ์คำถามเกี่ยวกับบริษัทหรือคำถามทั่วไปได้เลย</p>
                  <p className="mt-1 text-sm">แนบไฟล์ได้ด้วยปุ่ม 📎 หรือลากไฟล์มาวาง</p>
                </div>
              )}
              {chatState === "idle" &&
                messages.map((m, i) => (
                  <MessageRow
                    key={i}
                    m={m}
                    index={i}
                    copied={copiedMsgIdx === i}
                    readOnly={!!readOnlyOwner}
                    exportTarget={exportTargets[i]}
                    actions={actions}
                  />
                ))}
            </div>
          </div>
          {!atBottom && messages.length > 0 && (
            <button
              onClick={() => scrollToBottom(true)}
              className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-content2 shadow-lg hover:bg-surface2"
              data-testid="jump-latest"
            >
              <span className="flex items-center gap-1"><ArrowDown size={13} /> ไปข้อความล่าสุด</span>
            </button>
          )}
        </div>

        <div className="border-t border-line bg-surface px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 shadow-lg sm:px-6 sm:py-4">
          <form onSubmit={send} className="mx-auto max-w-4xl" data-testid="composer">
            {pendingFiles.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {pendingFiles.map((f, i) => (
                  <PendingFile key={`${f.name}-${i}`} file={f} onRemove={() => setPendingFiles((p) => p.filter((_, j) => j !== i))} />
                ))}
              </div>
            )}
            <div className="flex items-end gap-2">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={FILE_ACCEPT_ATTR}
                onChange={(e) => {
                  addFiles(Array.from(e.target.files ?? []));
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                className="hidden"
                data-testid="file-input"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || !!readOnlyOwner || pendingFiles.length >= 5 || atLimit}
                className="flex-shrink-0 rounded-2xl border border-line bg-surface2 p-3 text-muted transition-all hover:bg-surface3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="แนบไฟล์"
                title="แนบไฟล์ (รูป, PDF, Word, Excel, PowerPoint, เสียง, Text — สูงสุด 5 ไฟล์)"
              >
                <Paperclip size={20} />
              </button>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onComposerKey}
                onPaste={handlePaste}
                rows={1}
                aria-label="ข้อความ"
                placeholder={
                  readOnlyOwner
                    ? "โหมดอ่านอย่างเดียว — ส่งข้อความไม่ได้"
                    : atLimit
                      ? `แชทนี้ครบ ${turnLimit} คำถามแล้ว — เปิดแชทใหม่เพื่อถามต่อ`
                      : pendingFiles.length
                        ? "เขียนคำถามเกี่ยวกับไฟล์"
                        : "พิมพ์คำถาม…"
                }
                disabled={!!readOnlyOwner || atLimit}
                className="min-w-0 flex-1 resize-none overflow-y-auto rounded-2xl border border-line bg-surface2 px-4 py-3 leading-6 transition-all focus:border-transparent focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:cursor-not-allowed disabled:opacity-60 sm:px-5"
              />
              {sending ? (
                <button type="button" onClick={cancelSend} className="ui-btn ui-btn-danger flex-shrink-0 rounded-2xl px-4 py-3 sm:px-6" aria-label="หยุด AI" data-testid="stop">
                  <Square size={16} fill="currentColor" /> <span className="hidden sm:inline">หยุด</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!!readOnlyOwner || (!input.trim() && pendingFiles.length === 0) || atLimit}
                  className="ui-btn flex-shrink-0 rounded-2xl bg-gradient-to-r from-purple-500 to-purple-600 px-4 py-3 text-white shadow-md hover:from-purple-600 hover:to-purple-700 sm:px-6"
                  aria-label="ส่ง"
                  data-testid="send"
                >
                  <Send size={18} /> <span className="hidden sm:inline">ส่ง</span>
                </button>
              )}
            </div>
            <p className="mt-1.5 hidden text-center text-xs text-faint sm:block">
              <kbd className="rounded bg-surface3 px-1.5 py-0.5 font-mono text-[10px] text-muted">Enter</kbd> ส่ง •{" "}
              <kbd className="rounded bg-surface3 px-1.5 py-0.5 font-mono text-[10px] text-muted">Shift+Enter</kbd> ขึ้นบรรทัดใหม่ •{" "}
              <kbd className="rounded bg-surface3 px-1.5 py-0.5 font-mono text-[10px] text-muted">Ctrl+K</kbd> ค้นหาแชท
            </p>
          </form>
        </div>
      </main>

      <PromptModal
        open={!!renameTarget}
        onClose={() => setRenameTarget(null)}
        onConfirm={(t) => renameTarget && performRename(renameTarget.id, t)}
        title="เปลี่ยนชื่อบทสนทนา"
        description="ตั้งชื่อใหม่เพื่อหาง่ายขึ้นในประวัติ"
        initialValue={renameTarget?.title ?? ""}
        placeholder="ชื่อบทสนทนา"
        confirmLabel="บันทึก"
      />
      <PromptModal
        open={reasonTarget !== null}
        onClose={() => setReasonTarget(null)}
        onConfirm={(reason) => {
          if (reasonTarget && reason.trim()) castVote(reasonTarget.index, reasonTarget.messageId, "down", reason.trim());
          setReasonTarget(null);
        }}
        title="คำตอบนี้ยังไม่ดีตรงไหน?"
        description="บอกสั้นๆ ได้ (ไม่บังคับ) — ช่วยให้แอดมินปรับปรุงคำตอบได้ตรงจุด"
        initialValue=""
        placeholder="เช่น ข้อมูลไม่ตรง / ตอบไม่ครบ / ผิดประเด็น"
        confirmLabel="ส่ง"
      />
      <ConfirmModal
        open={deleteTargetId !== null}
        onClose={() => setDeleteTargetId(null)}
        onConfirm={() => deleteTargetId !== null && performDelete(deleteTargetId)}
        title="ลบบทสนทนานี้?"
        description="ข้อความทั้งหมดในบทสนทนานี้จะหายไปและไม่สามารถกู้คืนได้"
        confirmLabel="ลบ"
        danger
      />
      <ConfirmModal
        open={adminDeleteTarget !== null}
        onClose={() => setAdminDeleteTarget(null)}
        onConfirm={() => adminDeleteTarget && performAdminDelete(adminDeleteTarget.sid)}
        title="ลบบทสนทนาของทีม?"
        description={
          adminDeleteTarget
            ? `Admin action: คุณกำลังจะลบบทสนทนา "${adminDeleteTarget.title}" ของ ${adminDeleteTarget.owner} — การลบนี้ถาวร ไม่สามารถกู้คืนได้ และจะถูกบันทึกใน audit log`
            : ""
        }
        confirmLabel="ลบ"
        danger
      />
      <AlertModal open={!!alertMsg} onClose={() => setAlertMsg(null)} title="เกิดข้อผิดพลาด" description={alertMsg ?? ""} variant="error" />
      <MemoryModal open={showMemory} onClose={() => setShowMemory(false)} />
      <ShareModal
        open={shareOpen}
        busy={shareBusy}
        isAdmin={role === "admin"}
        users={shareUsers}
        selected={shareSel}
        sharedToken={sharedToken}
        onToggleUser={(id) =>
          setShareSel((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          })
        }
        onSave={saveShare}
        onUnshare={unshareCurrent}
        onClose={() => setShareOpen(false)}
      />
    </div>
  );
}

// A chat row in the sidebar: opens on click / Enter / Space. With a mouse its
// actions show as icons on hover or keyboard focus; on touch screens (no
// hover) one "⋯" button opens them as labelled buttons under the row, so the
// titles keep their width.
function SideRow({
  active,
  icon,
  title,
  onOpen,
  actions,
  small = false,
}: {
  active: boolean;
  icon: React.ReactNode;
  title: string;
  onOpen: () => void;
  actions: { icon: React.ReactNode; label: string; run: () => void; danger?: boolean }[];
  small?: boolean;
}) {
  const [menu, setMenu] = useState(false);
  const run = (e: React.MouseEvent, a: (typeof actions)[number]) => {
    e.stopPropagation();
    setMenu(false);
    a.run();
  };
  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            onOpen();
          }
        }}
        aria-current={active ? "true" : undefined}
        data-testid="session-row"
        title={title}
        className={`group flex cursor-pointer items-center gap-2 rounded-lg ${small ? "px-2 py-1.5 text-xs" : "px-2.5 py-2 text-sm"} text-white transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 ${
          active ? "bg-purple-400 shadow-lg" : "hover:bg-white/10"
        }`}
      >
        <span className="flex-shrink-0">{icon}</span>
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {actions.length > 0 && (
          <>
            <span className="hidden flex-shrink-0 gap-0.5 [@media(hover:hover)]:group-hover:flex [@media(hover:hover)]:group-focus-within:flex">
              {actions.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  onClick={(e) => run(e, a)}
                  className={`rounded p-1 text-white/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 ${a.danger ? "hover:text-red-300" : "hover:text-white"}`}
                  aria-label={a.label}
                  title={a.label}
                >
                  {a.icon}
                </button>
              ))}
            </span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setMenu((v) => !v);
              }}
              className="-my-1 flex-shrink-0 rounded p-1.5 text-white/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 [@media(hover:hover)]:hidden"
              aria-label={`ตัวเลือกของแชท ${title}`}
              aria-expanded={menu}
              data-testid="row-menu"
            >
              <MoreHorizontal size={16} />
            </button>
          </>
        )}
      </div>
      {menu && (
        <div className="flex flex-wrap gap-1.5 px-2 pb-1.5 pt-1 [@media(hover:hover)]:hidden" data-testid="row-actions">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={(e) => run(e, a)}
              className={`flex items-center gap-1.5 rounded-lg border border-white/20 bg-white/10 px-2.5 py-1.5 text-xs text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 ${a.danger ? "text-red-200" : ""}`}
            >
              {a.icon}
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
