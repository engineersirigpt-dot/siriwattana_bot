"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  Download,
  HelpCircle,
  History,
  Plus,
  Sparkles,
  Trash2,
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

type Tab = "pending" | "knowledge" | "history";

export default function AdminPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("pending");
  const [pending, setPending] = useState<Pending[]>([]);
  const [knowledge, setKnowledge] = useState<Knowledge[]>([]);
  const [adminSessions, setAdminSessions] = useState<AdminSession[]>([]);
  const [selectedSession, setSelectedSession] = useState<AdminSessionDetail | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [newQ, setNewQ] = useState("");
  const [newA, setNewA] = useState("");
  const [deleteKnowledgeId, setDeleteKnowledgeId] = useState<number | null>(null);

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    if (getRole() !== "admin") return router.replace("/chat");
    refresh();
  }, [router]);

  async function refresh() {
    const [p, k, h] = await Promise.all([
      api<Pending[]>("/admin/pending"),
      api<Knowledge[]>("/admin/knowledge"),
      api<AdminSession[]>("/admin/chat-history"),
    ]);
    setPending(p);
    setKnowledge(k);
    setAdminSessions(h);
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
        </div>

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
  count: number;
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
      <span
        className={`px-2 py-0.5 rounded-full text-xs ${
          active ? "bg-white/20 text-white" : "bg-gray-100 text-gray-600"
        }`}
      >
        {count}
      </span>
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
