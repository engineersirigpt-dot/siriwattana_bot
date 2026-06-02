"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, ClipboardList, Eye, Loader2 } from "lucide-react";
import { api, forkSharedSession, getToken } from "@/lib/api";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { AlertModal } from "@/components/Modal";

type SharedMessage = {
  id: number;
  question: string;
  answer: string;
  source: string;
  asked_at: string | null;
  source_knowledge_id?: number | null;
  source_file?: string | null;
};

type SharedSession = {
  id: number;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  mode: "normal" | "company";
  owner_user_id: number;
  owner_username: string;
  is_owner: boolean;
  messages: SharedMessage[];
};

export default function SharedChatPage() {
  const router = useRouter();
  const params = useParams<{ token: string }>();
  const token = params?.token;

  const [session, setSession] = useState<SharedSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [forking, setForking] = useState(false);
  const [alertMsg, setAlertMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      // Bounce to login but remember where to come back to.
      const next = encodeURIComponent(`/chat/shared/${token}`);
      router.replace(`/login?next=${next}`);
      return;
    }
    if (!token) return;

    api<SharedSession>(`/chat/shared/${token}`)
      .then((data) => setSession(data))
      .catch((e: unknown) => {
        const text = e instanceof Error ? e.message : "เปิดลิงค์ไม่ได้";
        setErrorMsg(text);
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  async function handleFork() {
    if (!token || forking) return;
    setForking(true);
    try {
      const { session_id } = await forkSharedSession(token);
      router.push(`/chat?sid=${session_id}`);
    } catch (e: unknown) {
      setAlertMsg(
        "รับเป็นแชทของฉันไม่สำเร็จ: " +
          (e instanceof Error ? e.message : "เกิดข้อผิดพลาด"),
      );
      setForking(false);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-purple-50">
        <Loader2 size={32} className="text-purple-500 animate-spin" />
      </div>
    );
  }

  if (errorMsg || !session) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-white via-purple-50 to-purple-100 p-6">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-lg border border-gray-100 p-8 text-center">
          <h1 className="text-xl font-semibold text-gray-800 mb-2">
            ลิงค์ใช้งานไม่ได้
          </h1>
          <p className="text-sm text-gray-600 mb-6">
            {errorMsg || "ลิงค์นี้อาจถูกยกเลิกการแชร์โดยเจ้าของ หรือถูกลบไปแล้ว"}
          </p>
          <button
            onClick={() => router.push("/chat")}
            className="inline-flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-xl hover:bg-purple-700 transition-all"
          >
            <ArrowLeft size={16} />
            กลับไปแชท
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-white via-purple-50 to-purple-100">
      {/* Top banner */}
      <div className="sticky top-0 z-10 bg-white/90 backdrop-blur-sm border-b border-gray-200 shadow-sm">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <button
              onClick={() => router.push("/chat")}
              className="flex-shrink-0 p-2 rounded-lg text-gray-500 hover:bg-gray-100"
              title="กลับไปแชทของฉัน"
            >
              <ArrowLeft size={18} />
            </button>
            <div className="min-w-0">
              <p className="text-xs text-gray-500 flex items-center gap-1">
                <Eye size={11} />
                บทสนทนาที่แชร์โดย{" "}
                <span className="font-medium text-purple-700">
                  {session.owner_username}
                </span>
                {session.mode === "company" && (
                  <span className="ml-1 bg-amber-100 text-amber-800 text-[10px] px-1.5 py-0.5 rounded">
                    📘 คู่มือบริษัท
                  </span>
                )}
              </p>
              <h1 className="text-base sm:text-lg font-medium text-gray-800 truncate">
                {session.title}
              </h1>
            </div>
          </div>

          {session.is_owner ? (
            <button
              onClick={() => router.push("/chat")}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-purple-50 text-purple-700 border border-purple-200 rounded-xl hover:bg-purple-100 transition-all"
              title="นี่คือแชทของคุณเอง"
            >
              เปิดแชทของฉัน
            </button>
          ) : (
            <button
              onClick={handleFork}
              disabled={forking}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-gradient-to-r from-purple-500 to-purple-600 text-white rounded-xl hover:from-purple-600 hover:to-purple-700 transition-all shadow-md disabled:opacity-60"
            >
              {forking ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  กำลังสร้าง…
                </>
              ) : (
                <>
                  <ClipboardList size={14} />
                  รับเป็นแชทของฉัน
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Read-only chat thread */}
      <div className="max-w-4xl mx-auto px-6 py-8 space-y-6">
        {session.messages.length === 0 && (
          <p className="text-center text-gray-400 mt-20">
            (บทสนทนานี้ยังว่างเปล่า)
          </p>
        )}

        {session.messages.map((m) => (
          <div key={m.id} className="space-y-3">
            <div className="flex justify-end">
              <div className="max-w-2xl px-6 py-4 rounded-2xl shadow-md whitespace-pre-wrap bg-gradient-to-r from-purple-400 to-purple-500 text-white">
                {m.question}
              </div>
            </div>

            <div className="flex justify-start gap-3">
              <img
                src="/Logo_siri.jpg"
                alt="Sirivatana"
                className="w-9 h-9 rounded-full object-cover flex-shrink-0 shadow-sm border border-gray-200"
              />
              <div className="flex-1 min-w-0 pt-1">
                <MarkdownMessage text={m.answer} />
                {m.source_file && (
                  <p className="mt-2 text-xs text-gray-500 italic">
                    📎 {m.source_file}
                  </p>
                )}
              </div>
            </div>
          </div>
        ))}

        {/* Footer call-to-action — repeat fork button at the bottom for long
            threads where the user scrolled past the header. */}
        {!session.is_owner && session.messages.length > 0 && (
          <div className="mt-12 mb-6 flex justify-center">
            <button
              onClick={handleFork}
              disabled={forking}
              className="inline-flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-purple-500 to-purple-600 text-white rounded-xl hover:from-purple-600 hover:to-purple-700 transition-all shadow-md disabled:opacity-60"
            >
              {forking ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  กำลังสร้างแชท…
                </>
              ) : (
                <>
                  <ClipboardList size={16} />
                  รับเป็นแชทของฉัน
                </>
              )}
            </button>
          </div>
        )}
      </div>

      <AlertModal
        open={!!alertMsg}
        onClose={() => setAlertMsg(null)}
        title="เกิดข้อผิดพลาด"
        description={alertMsg ?? ""}
        variant="error"
      />
    </div>
  );
}
