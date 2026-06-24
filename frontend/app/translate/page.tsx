"use client";

import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  Upload,
} from "lucide-react";
import {
  downloadTranslation,
  getToken,
  getTranslationReview,
  getTranslationStatus,
  listTranslationJobs,
  startTranslation,
  type TranslateJob,
  type TranslateReview,
} from "../../lib/api";

const STATUS_LABEL: Record<string, string> = {
  queued: "อยู่ในคิว",
  running: "กำลังแปล",
  done: "เสร็จ",
  error: "ผิดพลาด",
};

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "เกิดข้อผิดพลาด";
}

export default function TranslatePage() {
  const router = useRouter();
  const [job, setJob] = useState<TranslateJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [review, setReview] = useState<TranslateReview | null>(null);
  const [showReview, setShowReview] = useState(false);
  const [history, setHistory] = useState<TranslateJob[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadHistory() {
    try {
      setHistory(await listTranslationJobs());
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    loadHistory();
  }, [router]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function startPolling(id: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await getTranslationStatus(id);
        setJob(j);
        if (j.status === "done" || j.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          loadHistory();
        }
      } catch {
        /* transient — keep polling */
      }
    }, 2000);
  }

  async function onPick(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setErr("รองรับเฉพาะไฟล์ PDF");
      return;
    }
    setErr(null);
    setBusy(true);
    setJob(null);
    setReview(null);
    setShowReview(false);
    try {
      const j = await startTranslation(f);
      setJob(j);
      startPolling(j.id);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleReview() {
    if (!job) return;
    if (showReview) {
      setShowReview(false);
      return;
    }
    if (!review) {
      try {
        setReview(await getTranslationReview(job.id));
      } catch (e) {
        setErr(errMsg(e));
        return;
      }
    }
    setShowReview(true);
  }

  async function onDownload(fmt: "docx" | "pdf") {
    if (!job) return;
    const base = job.filename.replace(/\.pdf$/i, "");
    try {
      await downloadTranslation(job.id, fmt, `${base}_แปลไทย.${fmt}`);
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  function downloadHist(h: TranslateJob, fmt: "docx" | "pdf") {
    const base = h.filename.replace(/\.pdf$/i, "");
    downloadTranslation(h.id, fmt, `${base}_แปลไทย.${fmt}`).catch((e) => setErr(errMsg(e)));
  }

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-slate-100 p-6">
      <div className="mx-auto max-w-2xl">
        <button
          onClick={() => router.push("/chat")}
          className="mb-4 flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft size={16} /> กลับไปแชท
        </button>

        <div className="rounded-2xl bg-white p-8 shadow-sm">
          <div className="mb-2 flex items-center gap-2">
            <FileText className="text-violet-600" />
            <h1 className="text-xl font-bold text-slate-800">แปลเอกสาร (คู่มือ / สเปก)</h1>
          </div>
          <p className="mb-6 text-sm text-slate-500">
            อัปโหลดไฟล์ PDF ภาษาอังกฤษ → ระบบแปลเป็นไทยทั้งเล่ม คงรูปภาพ/ตารางจากต้นฉบับ
            พร้อมรอบตรวจทาน → ดาวน์โหลดเป็น Word
          </p>

          {!job && (
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              className="flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed border-slate-300 p-10 text-slate-500 hover:border-violet-400 hover:text-violet-600 disabled:opacity-50"
            >
              {busy ? <Loader2 className="animate-spin" /> : <Upload />}
              <span className="text-sm">{busy ? "กำลังอัปโหลด..." : "คลิกเพื่อเลือกไฟล์ PDF"}</span>
            </button>
          )}
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={onPick}
          />

          {err && (
            <div className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-600">{err}</div>
          )}

          {job && (
            <div className="mt-2">
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="truncate font-medium text-slate-700">{job.filename}</span>
                <span className="shrink-0 text-slate-400">{job.total} หน้า</span>
              </div>

              {job.exceeds_cap && (
                <div className="mb-3 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-xs text-amber-700">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                  <span>
                    ไฟล์มี {job.total} หน้า เกินลิมิต {job.max_pages} หน้า/งาน — ระบบจะแปล{" "}
                    {job.translated_pages} หน้าแรก (แบ่งไฟล์เป็นช่วงถ้าต้องการครบทั้งเล่ม)
                  </span>
                </div>
              )}

              {(job.status === "queued" || job.status === "running") && (
                <div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full bg-violet-500 transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
                    <Loader2 size={14} className="animate-spin" />
                    {job.status === "queued"
                      ? "อยู่ในคิว..."
                      : `กำลังแปล ${job.done}/${job.total} หน้า (${pct}%)`}
                  </div>
                </div>
              )}

              {job.status === "done" && (
                <div className="rounded-xl bg-emerald-50 p-4">
                  <div className="mb-3 flex items-center gap-2 text-emerald-700">
                    <CheckCircle2 size={18} />
                    <span className="font-medium">แปลเสร็จแล้ว — {job.done} หน้า</span>
                  </div>
                  {job.review_flagged > 0 && (
                    <button
                      onClick={toggleReview}
                      className="mb-3 text-xs font-medium text-amber-700 hover:underline"
                    >
                      ⚠️ มี {job.review_flagged} หน้าที่ควรตรวจทานซ้ำ (เลข/ตาราง) —{" "}
                      {showReview ? "ซ่อน" : "ดูรายละเอียด"}
                    </button>
                  )}
                  {showReview && review && (
                    <div className="mb-3 max-h-64 overflow-y-auto rounded-lg border border-amber-200 bg-white p-3 text-xs">
                      {review.pages.length === 0 ? (
                        <p className="text-slate-500">ไม่มีรายละเอียด</p>
                      ) : (
                        <ul className="space-y-2">
                          {review.pages.map((pg) => (
                            <li key={pg.page}>
                              <span className="font-semibold text-slate-700">หน้า {pg.page}</span>
                              <ul className="ml-3 mt-1 list-disc space-y-0.5 text-slate-500">
                                {pg.issues.map((it, i) => (
                                  <li key={i}>{it}</li>
                                ))}
                              </ul>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <button
                      onClick={() => onDownload("docx")}
                      className="flex items-center gap-1 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700"
                    >
                      <Download size={16} /> ดาวน์โหลด Word
                    </button>
                    {job.pdf && (
                      <button
                        onClick={() => onDownload("pdf")}
                        className="flex items-center gap-1 rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
                      >
                        <Download size={16} /> PDF
                      </button>
                    )}
                  </div>
                </div>
              )}

              {job.status === "error" && (
                <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-600">
                  แปลไม่สำเร็จ: {job.error || "เกิดข้อผิดพลาด"}
                </div>
              )}

              {(job.status === "done" || job.status === "error") && (
                <button
                  onClick={() => {
                    setJob(null);
                    setErr(null);
                  }}
                  className="mt-4 text-sm text-violet-600 hover:underline"
                >
                  + แปลไฟล์ใหม่
                </button>
              )}
            </div>
          )}
        </div>

        {history.filter((h) => h.id !== job?.id).length > 0 && (
          <div className="mt-6 rounded-2xl bg-white p-6 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-slate-700">
              งานแปลที่ผ่านมา
            </h2>
            <ul className="divide-y divide-slate-100">
              {history
                .filter((h) => h.id !== job?.id)
                .map((h) => (
                  <li
                    key={h.id}
                    className="flex items-center justify-between gap-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm text-slate-700">{h.filename}</p>
                      <p className="text-xs text-slate-400">
                        {STATUS_LABEL[h.status] ?? h.status}
                        {h.status === "done" ? ` · ${h.done} หน้า` : ""}
                        {h.status === "error" && h.error ? ` · ${h.error}` : ""}
                      </p>
                    </div>
                    {h.status === "done" ? (
                      <div className="flex shrink-0 gap-1">
                        <button
                          onClick={() => downloadHist(h, "docx")}
                          className="rounded-md border border-slate-200 px-2 py-1 text-xs text-violet-600 hover:bg-violet-50"
                        >
                          Word
                        </button>
                        {h.pdf && (
                          <button
                            onClick={() => downloadHist(h, "pdf")}
                            className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50"
                          >
                            PDF
                          </button>
                        )}
                      </div>
                    ) : h.status === "running" ? (
                      <span className="shrink-0 text-xs text-violet-500">
                        {h.done}/{h.total}
                      </span>
                    ) : null}
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
