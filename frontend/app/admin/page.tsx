"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  BarChart3,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Download,
  HelpCircle,
  History,
  Lock,
  MessageSquare,
  Plus,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Unlock,
  Users,
  X,
} from "lucide-react";
import { API_BASE, api, getRole, getToken } from "@/lib/api";
import { ConfirmModal } from "@/components/Modal";

type Pending = {
  id: number;
  question: string;
  ask_count: number;
  first_asked_at: string;
  last_asked_at: string;
};

type Knowledge = {
  id: number;
  question: string;
  answer: string;
  hit_count: number;
  approved_at: string;
  source: "admin" | "llm";
};

type AdminSession = {
  id: number;
  title: string;
  user_id: number;
  username: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

type AdminMessage = {
  id: number;
  question: string;
  answer: string;
  source: string;
  asked_at: string;
};

type AdminSessionDetail = {
  id: number;
  title: string;
  username: string;
  created_at: string;
  messages: AdminMessage[];
};

type AdminUser = {
  id: number;
  username: string;
  role: "user" | "admin";
  is_disabled: boolean;
  created_at: string | null;
  chat_count: number;
  last_active: string | null;
};

type UserActionKind = "promote" | "demote" | "disable" | "enable" | "delete_chats";

type UserActionTarget = {
  kind: UserActionKind;
  user: AdminUser;
};

type Analytics = {
  totals: {
    messages: number;
    sessions: number;
    users: number;
    messages_7d: number;
    feedback_up: number;
    feedback_down: number;
  };
  source_breakdown: { source: string; count: number }[];
  daily_volume: { day: string; count: number }[];
  top_unanswered: { question: string; ask_count: number }[];
  recent_downvotes: {
    question: string;
    reason: string | null;
    username: string;
    created_at: string | null;
  }[];
  top_users: { username: string; count: number }[];
};

type Tab = "analytics" | "pending" | "knowledge" | "history" | "users";

export default function AdminPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("analytics");
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [knowledge, setKnowledge] = useState<Knowledge[]>([]);
  const [adminSessions, setAdminSessions] = useState<AdminSession[]>([]);
  const [selectedSession, setSelectedSession] = useState<AdminSessionDetail | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [newQ, setNewQ] = useState("");
  const [newA, setNewA] = useState("");
  const [deleteKnowledgeId, setDeleteKnowledgeId] = useState<number | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [userAction, setUserAction] = useState<UserActionTarget | null>(null);
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    if (getRole() !== "admin") return router.replace("/chat");
    api<{ id: number }>("/auth/me")
      .then((u) => setCurrentUserId(u.id))
      .catch(() => {});
    refresh();
  }, [router]);

  async function refresh() {
    const [p, k, h, u, a] = await Promise.all([
      api<Pending[]>("/admin/pending"),
      api<Knowledge[]>("/admin/knowledge"),
      api<AdminSession[]>("/admin/chat-history"),
      api<AdminUser[]>("/admin/users").catch(() => [] as AdminUser[]),
      api<Analytics>("/admin/analytics").catch(() => null),
    ]);
    setPending(p);
    setKnowledge(k);
    setAdminSessions(h);
    setUsers(u);
    setAnalytics(a);
  }

  async function performUserAction() {
    if (!userAction) return;
    const { kind, user: target } = userAction;
    try {
      if (kind === "promote") {
        await api(`/admin/users/${target.id}/role`, {
          method: "PATCH",
          body: JSON.stringify({ role: "admin" }),
        });
      } else if (kind === "demote") {
        await api(`/admin/users/${target.id}/role`, {
          method: "PATCH",
          body: JSON.stringify({ role: "user" }),
        });
      } else if (kind === "disable") {
        await api(`/admin/users/${target.id}/status`, {
          method: "PATCH",
          body: JSON.stringify({ is_disabled: true }),
        });
      } else if (kind === "enable") {
        await api(`/admin/users/${target.id}/status`, {
          method: "PATCH",
          body: JSON.stringify({ is_disabled: false }),
        });
      } else if (kind === "delete_chats") {
        await api(`/admin/users/${target.id}/chats`, { method: "DELETE" });
      }
      setUserAction(null);
      refresh();
    } catch (e: unknown) {
      alert(
        "ทำรายการไม่สำเร็จ: " +
          (e instanceof Error ? e.message : "เกิดข้อผิดพลาด"),
      );
    }
  }

  function userActionLabel(t: UserActionTarget): {
    title: string;
    description: string;
    confirmLabel: string;
    danger: boolean;
  } {
    const name = t.user.username;
    if (t.kind === "promote")
      return {
        title: `แต่งตั้ง ${name} เป็น Admin?`,
        description: `${name} จะมีสิทธิ์เข้าหน้านี้และจัดการระบบทั้งหมด`,
        confirmLabel: "แต่งตั้ง",
        danger: false,
      };
    if (t.kind === "demote")
      return {
        title: `ลดสิทธิ์ ${name} เป็น User?`,
        description: `${name} จะไม่สามารถเข้าหน้า Admin Dashboard ได้อีก`,
        confirmLabel: "ลดสิทธิ์",
        danger: true,
      };
    if (t.kind === "disable")
      return {
        title: `ระงับการใช้งานของ ${name}?`,
        description: `${name} จะไม่สามารถ login เข้าระบบได้ — แต่ประวัติแชทยังคงอยู่`,
        confirmLabel: "ระงับ",
        danger: true,
      };
    if (t.kind === "enable")
      return {
        title: `เปิดใช้งาน ${name} อีกครั้ง?`,
        description: `${name} จะสามารถ login เข้าระบบได้`,
        confirmLabel: "เปิดใช้งาน",
        danger: false,
      };
    return {
      title: `ลบ chat ทั้งหมดของ ${name}?`,
      description: `chat ทั้ง ${t.user.chat_count} บทสนทนาจะถูกลบถาวร — บัญชี user ยังคงอยู่`,
      confirmLabel: "ลบทั้งหมด",
      danger: true,
    };
  }

  async function answerPending(id: number) {
    const text = (answers[id] ?? "").trim();
    if (!text) return;
    await api(`/admin/pending/${id}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer: text }),
    });
    setAnswers((a) => ({ ...a, [id]: "" }));
    await refresh();
  }

  async function ignorePending(id: number) {
    await api(`/admin/pending/${id}/ignore`, { method: "POST" });
    await refresh();
  }

  async function addKnowledge(e: React.FormEvent) {
    e.preventDefault();
    if (!newQ.trim() || !newA.trim()) return;
    await api("/admin/knowledge", {
      method: "POST",
      body: JSON.stringify({ question: newQ, answer: newA }),
    });
    setNewQ("");
    setNewA("");
    await refresh();
  }

  async function performDeleteKnowledge(id: number) {
    await api(`/admin/knowledge/${id}`, { method: "DELETE" });
    await refresh();
  }

  async function verifyKnowledge(id: number) {
    await api(`/admin/knowledge/${id}/verify`, { method: "POST" });
    await refresh();
  }

  async function viewSession(sid: number) {
    const detail = await api<AdminSessionDetail>(`/admin/chat-history/${sid}`);
    setSelectedSession(detail);
  }

  function exportAllCsv() {
    const token = getToken();
    fetch(`${API_BASE}/admin/chat-history/export/all`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.blob())
      .then((b) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(b);
        a.download = "all-chat-history.csv";
        a.click();
      });
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-white via-purple-50 to-purple-100">
      {/* Background decoration */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-purple-300 rounded-full opacity-20 blur-3xl"></div>
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-pink-300 rounded-full opacity-15 blur-3xl"></div>
      </div>

      <div className="relative z-10 max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-medium bg-gradient-to-r from-purple-600 to-purple-800 bg-clip-text text-transparent">
              Admin Dashboard
            </h1>
            <p className="text-gray-600 mt-1 text-sm">จัดการคำถาม ฐานความรู้ และประวัติการแชท</p>
          </div>
          <button
            onClick={() => router.push("/chat")}
            className="flex items-center gap-2 px-4 py-2 bg-white/80 backdrop-blur-sm border border-gray-200 rounded-xl hover:bg-white hover:shadow-md transition-all text-sm text-gray-700"
          >
            <ArrowLeft size={18} />
            กลับไปแชท
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6 bg-white/60 backdrop-blur-sm p-1.5 rounded-2xl shadow-sm border border-white/40 w-fit">
          <TabButton
            active={tab === "analytics"}
            onClick={() => setTab("analytics")}
            icon={<BarChart3 size={18} />}
            label="ภาพรวม"
          />
          <TabButton
            active={tab === "pending"}
            onClick={() => setTab("pending")}
            icon={<HelpCircle size={18} />}
            label="คำถามรอตอบ"
            count={pending.length}
          />
          <TabButton
            active={tab === "knowledge"}
            onClick={() => setTab("knowledge")}
            icon={<Sparkles size={18} />}
            label="ฐานความรู้"
            count={knowledge.length}
          />
          <TabButton
            active={tab === "history"}
            onClick={() => setTab("history")}
            icon={<History size={18} />}
            label="ประวัติแชท"
            count={adminSessions.length}
          />
          <TabButton
            active={tab === "users"}
            onClick={() => setTab("users")}
            icon={<Users size={18} />}
            label="จัดการ User"
            count={users.length}
          />
        </div>

        {/* Analytics / overview tab */}
        {tab === "analytics" && <AnalyticsView data={analytics} />}

        {/* Pending tab */}
        {tab === "pending" && (
          <div className="space-y-4">
            {pending.length === 0 && (
              <EmptyState
                icon={<CheckCircle2 className="text-green-500" size={48} />}
                title="ยังไม่มีคำถามรอตอบ"
                description="ทุกคำถามจาก user ที่ไม่อยู่ใน RAG จะมาโผล่ที่นี่ — ตอนนี้ว่างเปล่า ดีมาก!"
              />
            )}
            {pending.map((p) => (
              <div
                key={p.id}
                className="bg-white rounded-2xl shadow-md border border-gray-100 p-6 hover:shadow-lg transition-all"
              >
                <div className="flex justify-between text-xs text-gray-500 mb-3">
                  <span className="inline-flex items-center gap-1 bg-purple-100 text-purple-700 px-2.5 py-1 rounded-full">
                    ถูกถาม {p.ask_count} ครั้ง
                  </span>
                  <span>ล่าสุด: {new Date(p.last_asked_at + "Z").toLocaleString("th-TH")}</span>
                </div>
                <p className="text-gray-900 font-medium text-lg mb-3">{p.question}</p>
                <textarea
                  className="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all text-sm"
                  rows={3}
                  placeholder="พิมพ์คำตอบที่ถูกต้องของบริษัท..."
                  value={answers[p.id] ?? ""}
                  onChange={(e) =>
                    setAnswers((a) => ({ ...a, [p.id]: e.target.value }))
                  }
                />
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={() => answerPending(p.id)}
                    className="px-5 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md text-sm flex items-center gap-1.5"
                  >
                    <CheckCircle2 size={16} />
                    อนุมัติเข้า RAG
                  </button>
                  <button
                    onClick={() => ignorePending(p.id)}
                    className="px-4 py-2 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-all text-sm text-gray-600"
                  >
                    ข้าม
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Knowledge tab */}
        {tab === "knowledge" && (
          <div className="space-y-4">
            <form
              onSubmit={addKnowledge}
              className="bg-white rounded-2xl shadow-md border border-gray-100 p-6"
            >
              <h2 className="font-medium text-gray-800 mb-4 flex items-center gap-2">
                <Plus size={20} className="text-purple-600" />
                เพิ่ม Q&A เอง
              </h2>
              <input
                className="w-full mb-3 px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all text-sm"
                placeholder="คำถาม"
                value={newQ}
                onChange={(e) => setNewQ(e.target.value)}
              />
              <textarea
                className="w-full mb-3 px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all text-sm"
                rows={3}
                placeholder="คำตอบ"
                value={newA}
                onChange={(e) => setNewA(e.target.value)}
              />
              <button className="px-5 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md text-sm">
                เพิ่ม
              </button>
            </form>

            {knowledge.length === 0 && (
              <EmptyState
                icon={<Sparkles className="text-purple-400" size={48} />}
                title="ฐานความรู้ว่างเปล่า"
                description="เพิ่ม Q&A ผ่านฟอร์มด้านบน หรือรอ user ถามคำถามแล้ว approve จาก tab รอตอบ"
              />
            )}

            {knowledge.map((k) => (
              <div
                key={k.id}
                className={`bg-white rounded-2xl shadow-md p-6 transition-all hover:shadow-lg ${
                  k.source === "llm"
                    ? "border-2 border-amber-200"
                    : "border border-gray-100"
                }`}
              >
                <div className="flex justify-between items-center mb-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    {k.source === "llm" ? (
                      <span className="inline-flex items-center gap-1 bg-amber-100 text-amber-800 px-2.5 py-1 rounded-full text-xs">
                        🤖 จาก AI (ยังไม่ verify)
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 bg-green-100 text-green-800 px-2.5 py-1 rounded-full text-xs">
                        <CheckCircle2 size={12} /> Admin verified
                      </span>
                    )}
                    <span className="text-xs text-gray-500">
                      ถูกใช้ตอบ {k.hit_count} ครั้ง
                    </span>
                  </div>
                  <div className="flex gap-2">
                    {k.source === "llm" && (
                      <button
                        onClick={() => verifyKnowledge(k.id)}
                        className="text-green-700 hover:text-green-800 text-xs flex items-center gap-1"
                      >
                        <CheckCircle2 size={14} />
                        ยืนยัน
                      </button>
                    )}
                    <button
                      onClick={() => setDeleteKnowledgeId(k.id)}
                      className="text-red-600 hover:text-red-700 text-xs flex items-center gap-1"
                    >
                      <Trash2 size={14} />
                      ลบ
                    </button>
                  </div>
                </div>
                <p className="font-medium text-gray-900 mb-2">{k.question}</p>
                <p className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                  {k.answer}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* History tab */}
        {tab === "history" && (
          <div>
            <div className="flex justify-between items-center mb-4">
              <p className="text-sm text-gray-500">
                แสดงล่าสุด {adminSessions.length} บทสนทนา
              </p>
              <button
                onClick={exportAllCsv}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-xl hover:from-purple-700 hover:to-purple-800 transition-all shadow-md text-sm"
              >
                <Download size={16} />
                ดาวน์โหลด CSV ทั้งหมด
              </button>
            </div>

            {adminSessions.length === 0 ? (
              <EmptyState
                icon={<History className="text-purple-400" size={48} />}
                title="ยังไม่มีประวัติแชท"
                description="ประวัติการสนทนาของทุก user จะมาโผล่ที่นี่"
              />
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-2">
                  {adminSessions.map((s) => (
                    <div
                      key={s.id}
                      onClick={() => viewSession(s.id)}
                      className={`bg-white rounded-xl border p-4 cursor-pointer transition-all hover:shadow-md ${
                        selectedSession?.id === s.id
                          ? "border-purple-400 ring-2 ring-purple-200 shadow-md"
                          : "border-gray-100"
                      }`}
                    >
                      <div className="flex justify-between text-xs text-gray-500 mb-1">
                        <span className="inline-flex items-center gap-1 bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">
                          {s.username}
                        </span>
                        <span>
                          {new Date(s.updated_at + "Z").toLocaleString("th-TH")}
                        </span>
                      </div>
                      <p className="font-medium text-gray-900 truncate">{s.title}</p>
                      <p className="text-xs text-gray-500 mt-1">
                        {s.message_count} ข้อความ
                      </p>
                    </div>
                  ))}
                </div>

                <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-md max-h-[70vh] overflow-y-auto">
                  {selectedSession ? (
                    <>
                      <div className="border-b border-gray-100 pb-3 mb-4">
                        <div className="flex justify-between items-start">
                          <div>
                            <p className="font-medium text-gray-900">
                              {selectedSession.title}
                            </p>
                            <p className="text-xs text-gray-500 mt-1">
                              โดย {selectedSession.username} —{" "}
                              {new Date(selectedSession.created_at + "Z").toLocaleString(
                                "th-TH",
                              )}
                            </p>
                          </div>
                          <button
                            onClick={() => setSelectedSession(null)}
                            className="text-gray-400 hover:text-gray-600"
                          >
                            <X size={18} />
                          </button>
                        </div>
                      </div>
                      <div className="space-y-3">
                        {selectedSession.messages.map((m) => (
                          <div key={m.id} className="space-y-1.5">
                            <div className="bg-gradient-to-r from-purple-50 to-purple-100 rounded-xl px-4 py-2.5 text-sm text-gray-800">
                              <span className="text-xs text-purple-600 font-medium block mb-0.5">
                                user
                              </span>
                              {m.question}
                            </div>
                            <div className="bg-gray-50 rounded-xl px-4 py-2.5 text-sm text-gray-800 whitespace-pre-wrap">
                              <span className="text-xs text-gray-500 font-medium block mb-0.5">
                                bot ({m.source})
                              </span>
                              {m.answer}
                            </div>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="text-gray-400 text-center mt-20">
                      คลิกบทสนทนาด้านซ้ายเพื่อดูเนื้อหา
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Users tab */}
        {tab === "users" && (
          <UsersPanel
            users={users}
            currentUserId={currentUserId}
            onAction={(target) => setUserAction(target)}
          />
        )}
      </div>

      <ConfirmModal
        open={deleteKnowledgeId !== null}
        onClose={() => setDeleteKnowledgeId(null)}
        onConfirm={() => {
          if (deleteKnowledgeId !== null) performDeleteKnowledge(deleteKnowledgeId);
        }}
        title="ลบรายการนี้?"
        description="รายการในฐานความรู้จะถูกลบถาวร — บอทจะไม่ใช้ตอบครั้งต่อไป"
        confirmLabel="ลบ"
        danger
      />

      {userAction && (
        <ConfirmModal
          open={userAction !== null}
          onClose={() => setUserAction(null)}
          onConfirm={performUserAction}
          {...userActionLabel(userAction)}
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all ${
        active
          ? "bg-gradient-to-r from-purple-600 to-purple-700 text-white shadow-md"
          : "text-gray-600 hover:bg-white hover:text-gray-800"
      }`}
    >
      {icon}
      <span>{label}</span>
      {count !== undefined && (
        <span
          className={`px-2 py-0.5 rounded-full text-xs ${
            active ? "bg-white/20 text-white" : "bg-gray-100 text-gray-600"
          }`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

function EmptyState({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-white/60 backdrop-blur-sm rounded-2xl border border-white/40 p-12 text-center">
      <div className="flex justify-center mb-4">{icon}</div>
      <h3 className="text-gray-800 font-medium mb-1">{title}</h3>
      <p className="text-sm text-gray-500">{description}</p>
    </div>
  );
}

function formatLastActive(iso: string | null): string {
  if (!iso) return "ยังไม่เคยใช้";
  // PG `.isoformat()` already includes a tz offset like "+00:00"; only
  // append "Z" if the string truly has no tz marker (legacy SQLite path).
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  if (isNaN(d.getTime())) return "—";
  const diff = Date.now() - d.getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "เพิ่งใช้";
  if (min < 60) return `${min} นาทีก่อน`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ชม.ก่อน`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} วันก่อน`;
  return d.toLocaleDateString("th-TH");
}

function UsersPanel({
  users,
  currentUserId,
  onAction,
}: {
  users: AdminUser[];
  currentUserId: number | null;
  onAction: (target: UserActionTarget) => void;
}) {
  const total = users.length;
  const adminCount = users.filter((u) => u.role === "admin" && !u.is_disabled).length;
  const activeCount = users.filter((u) => !u.is_disabled).length;
  const disabledCount = total - activeCount;

  if (total === 0) {
    return (
      <EmptyState
        icon={<Users className="text-purple-400" size={48} />}
        title="ยังไม่มี user"
        description="user คนแรกจะมาโผล่ที่นี่หลัง login ครั้งแรก"
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="รวม user" value={total} color="purple" />
        <StatCard label="Admin" value={adminCount} color="indigo" />
        <StatCard label="ใช้งานได้" value={activeCount} color="green" />
        <StatCard label="ถูกระงับ" value={disabledCount} color="red" />
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-purple-50 text-purple-900">
              <tr>
                <th className="px-4 py-3 text-left font-semibold">Username</th>
                <th className="px-4 py-3 text-left font-semibold">Role</th>
                <th className="px-4 py-3 text-left font-semibold">สถานะ</th>
                <th className="px-4 py-3 text-left font-semibold">ใช้งานล่าสุด</th>
                <th className="px-4 py-3 text-right font-semibold">Chats</th>
                <th className="px-4 py-3 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.map((u) => {
                const isSelf = u.id === currentUserId;
                return (
                  <tr key={u.id} className="hover:bg-gray-50/60">
                    <td className="px-4 py-3 font-medium text-gray-800">
                      {u.username}
                      {isSelf && (
                        <span className="ml-2 text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                          คุณ
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {u.role === "admin" ? (
                        <span className="inline-flex items-center gap-1 bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded text-xs font-medium">
                          <ShieldCheck size={12} />
                          admin
                        </span>
                      ) : (
                        <span className="text-gray-600 text-xs">user</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {u.is_disabled ? (
                        <span className="inline-flex items-center gap-1 bg-red-100 text-red-700 px-2 py-0.5 rounded text-xs">
                          <Lock size={12} />
                          ระงับ
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 bg-green-100 text-green-700 px-2 py-0.5 rounded text-xs">
                          <CheckCircle2 size={12} />
                          ใช้งานได้
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {formatLastActive(u.last_active)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {u.chat_count}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {u.role === "user" ? (
                          <IconAction
                            icon={<ChevronUp size={14} />}
                            color="indigo"
                            title="แต่งตั้งเป็น admin"
                            disabled={u.is_disabled}
                            onClick={() => onAction({ kind: "promote", user: u })}
                          />
                        ) : (
                          <IconAction
                            icon={<ChevronDown size={14} />}
                            color="gray"
                            title="ลดสิทธิ์เป็น user"
                            disabled={isSelf}
                            onClick={() => onAction({ kind: "demote", user: u })}
                          />
                        )}
                        {u.is_disabled ? (
                          <IconAction
                            icon={<Unlock size={14} />}
                            color="green"
                            title="เปิดใช้งานอีกครั้ง"
                            onClick={() => onAction({ kind: "enable", user: u })}
                          />
                        ) : (
                          <IconAction
                            icon={<Lock size={14} />}
                            color="red"
                            title="ระงับการใช้งาน"
                            disabled={isSelf}
                            onClick={() => onAction({ kind: "disable", user: u })}
                          />
                        )}
                        <IconAction
                          icon={<Trash2 size={14} />}
                          color="red"
                          title={`ลบ chat ทั้งหมด (${u.chat_count})`}
                          disabled={u.chat_count === 0}
                          onClick={() => onAction({ kind: "delete_chats", user: u })}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-gray-400 px-2">
        💡 MI users ไม่สามารถ reset password จากที่นี่ได้ — ระงับการใช้งานแทนหากต้องการบล็อก
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "purple" | "indigo" | "green" | "red";
}) {
  const colors: Record<typeof color, string> = {
    purple: "from-purple-50 to-purple-100 text-purple-800 border-purple-200",
    indigo: "from-indigo-50 to-indigo-100 text-indigo-800 border-indigo-200",
    green: "from-green-50 to-green-100 text-green-800 border-green-200",
    red: "from-red-50 to-red-100 text-red-800 border-red-200",
  };
  return (
    <div
      className={`bg-gradient-to-br ${colors[color]} border rounded-xl p-3`}
    >
      <p className="text-xs opacity-80">{label}</p>
      <p className="text-2xl font-semibold mt-0.5">{value}</p>
    </div>
  );
}

function IconAction({
  icon,
  color,
  title,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  color: "indigo" | "gray" | "green" | "red";
  title: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  const colors: Record<typeof color, string> = {
    indigo: "text-indigo-600 hover:bg-indigo-50",
    gray: "text-gray-500 hover:bg-gray-100",
    green: "text-green-600 hover:bg-green-50",
    red: "text-red-600 hover:bg-red-50",
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`p-1.5 rounded-md transition-all ${
        disabled
          ? "text-gray-300 cursor-not-allowed"
          : colors[color]
      }`}
    >
      {icon}
    </button>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  rag: "ฐานความรู้ (KB)",
  "rag-calc": "ฐานความรู้ (คำนวณ)",
  llm: "AI ทั่วไป",
  blocked: "ถูกบล็อก",
  export_offer: "ขอ export",
};

function MetricCard({
  label,
  value,
  accent,
  icon,
}: {
  label: string;
  value: number | string;
  accent: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-4 flex items-center gap-3">
      <div className={`p-2.5 rounded-xl ${accent}`}>{icon}</div>
      <div className="min-w-0">
        <div className="text-2xl font-semibold text-gray-800 leading-tight">
          {value}
        </div>
        <div className="text-xs text-gray-500 truncate">{label}</div>
      </div>
    </div>
  );
}

function BarRow({
  label,
  count,
  max,
  color = "bg-purple-500",
  sub,
}: {
  label: string;
  count: number;
  max: number;
  color?: string;
  sub?: string;
}) {
  const pct = max > 0 ? Math.max(4, Math.round((count / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="w-32 shrink-0 truncate text-gray-600" title={label}>
        {label}
      </div>
      <div className="flex-1 bg-gray-100 rounded-full h-5 overflow-hidden">
        <div
          className={`h-full ${color} rounded-full transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="w-16 shrink-0 text-right text-gray-700 tabular-nums">
        {count}
        {sub && <span className="text-gray-400 text-xs ml-1">{sub}</span>}
      </div>
    </div>
  );
}

function AnalyticsView({ data }: { data: Analytics | null }) {
  if (!data) {
    return (
      <EmptyState
        icon={<BarChart3 className="text-purple-400" size={48} />}
        title="ยังไม่มีข้อมูลภาพรวม"
        description="เมื่อมีการใช้งานแชทและการให้คะแนนคำตอบ สถิติจะแสดงที่นี่"
      />
    );
  }

  const t = data.totals;
  const totalFeedback = t.feedback_up + t.feedback_down;
  const downRate =
    totalFeedback > 0 ? Math.round((t.feedback_down / totalFeedback) * 100) : 0;
  const maxDaily = Math.max(1, ...data.daily_volume.map((d) => d.count));
  const maxSource = Math.max(1, ...data.source_breakdown.map((s) => s.count));
  const maxUser = Math.max(1, ...data.top_users.map((u) => u.count));

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <MetricCard
          label="ข้อความทั้งหมด"
          value={t.messages.toLocaleString()}
          accent="bg-purple-100 text-purple-600"
          icon={<MessageSquare size={18} />}
        />
        <MetricCard
          label="บทสนทนา"
          value={t.sessions.toLocaleString()}
          accent="bg-blue-100 text-blue-600"
          icon={<History size={18} />}
        />
        <MetricCard
          label="ผู้ใช้"
          value={t.users.toLocaleString()}
          accent="bg-amber-100 text-amber-600"
          icon={<Users size={18} />}
        />
        <MetricCard
          label="ข้อความ 7 วัน"
          value={t.messages_7d.toLocaleString()}
          accent="bg-green-100 text-green-600"
          icon={<BarChart3 size={18} />}
        />
        <MetricCard
          label="ถูกใจ (👍)"
          value={t.feedback_up.toLocaleString()}
          accent="bg-green-100 text-green-600"
          icon={<ThumbsUp size={18} />}
        />
        <MetricCard
          label={`ไม่ถูกใจ — ${downRate}% ของโหวต`}
          value={t.feedback_down.toLocaleString()}
          accent="bg-red-100 text-red-600"
          icon={<ThumbsDown size={18} />}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Daily volume */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
          <h3 className="font-medium text-gray-800 mb-4">
            ปริมาณข้อความ (14 วันล่าสุด)
          </h3>
          {data.daily_volume.length === 0 ? (
            <p className="text-sm text-gray-400">ยังไม่มีข้อมูล</p>
          ) : (
            <div className="flex items-end gap-1 h-40">
              {data.daily_volume.map((d) => (
                <div
                  key={d.day}
                  className="flex-1 flex flex-col items-center justify-end gap-1 group"
                  title={`${d.day}: ${d.count}`}
                >
                  <span className="text-[10px] text-gray-400 opacity-0 group-hover:opacity-100">
                    {d.count}
                  </span>
                  <div
                    className="w-full bg-purple-400 group-hover:bg-purple-600 rounded-t transition-all"
                    style={{
                      height: `${Math.max(4, (d.count / maxDaily) * 100)}%`,
                    }}
                  />
                  <span className="text-[9px] text-gray-400">
                    {d.day.slice(5)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Source breakdown */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
          <h3 className="font-medium text-gray-800 mb-4">ที่มาของคำตอบ</h3>
          {data.source_breakdown.length === 0 ? (
            <p className="text-sm text-gray-400">ยังไม่มีข้อมูล</p>
          ) : (
            <div className="space-y-2.5">
              {data.source_breakdown.map((s) => (
                <BarRow
                  key={s.source}
                  label={SOURCE_LABELS[s.source] ?? s.source}
                  count={s.count}
                  max={maxSource}
                  color={
                    s.source === "blocked"
                      ? "bg-red-400"
                      : s.source.startsWith("rag")
                      ? "bg-green-500"
                      : "bg-purple-500"
                  }
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Top unanswered = KB gaps */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
        <h3 className="font-medium text-gray-800 mb-1">
          คำถามที่ตอบไม่ได้บ่อย (ช่องว่างความรู้)
        </h3>
        <p className="text-xs text-gray-500 mb-4">
          ควรเพิ่มคำตอบเหล่านี้เข้าฐานความรู้ — ดูที่แท็บ “คำถามรอตอบ”
        </p>
        {data.top_unanswered.length === 0 ? (
          <p className="text-sm text-gray-400">ไม่มี — เยี่ยมมาก! 🎉</p>
        ) : (
          <div className="space-y-2">
            {data.top_unanswered.map((q, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 text-sm border-b border-gray-50 pb-2 last:border-0"
              >
                <span className="text-gray-700 truncate">{q.question}</span>
                <span className="shrink-0 bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full text-xs">
                  ถูกถาม {q.ask_count} ครั้ง
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent downvotes */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
        <h3 className="font-medium text-gray-800 mb-4 flex items-center gap-2">
          <ThumbsDown size={16} className="text-red-500" />
          คำตอบที่โดน 👎 ล่าสุด
        </h3>
        {data.recent_downvotes.length === 0 ? (
          <p className="text-sm text-gray-400">ยังไม่มี</p>
        ) : (
          <div className="space-y-3">
            {data.recent_downvotes.map((d, i) => (
              <div key={i} className="border-b border-gray-50 pb-3 last:border-0">
                <p className="text-sm text-gray-800 font-medium">{d.question}</p>
                {d.reason && (
                  <p className="text-sm text-red-600 mt-0.5">เหตุผล: {d.reason}</p>
                )}
                <p className="text-xs text-gray-400 mt-0.5">
                  โดย {d.username}
                  {d.created_at &&
                    " · " +
                      new Date(
                        d.created_at.endsWith("Z") || d.created_at.includes("+")
                          ? d.created_at
                          : d.created_at + "Z",
                      ).toLocaleString("th-TH")}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Top users */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
        <h3 className="font-medium text-gray-800 mb-4">ผู้ใช้ที่ถามมากที่สุด</h3>
        {data.top_users.length === 0 ? (
          <p className="text-sm text-gray-400">ยังไม่มีข้อมูล</p>
        ) : (
          <div className="space-y-2.5">
            {data.top_users.map((u) => (
              <BarRow
                key={u.username}
                label={u.username}
                count={u.count}
                max={maxUser}
                color="bg-blue-500"
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
