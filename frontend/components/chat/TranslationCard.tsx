"use client";

// A background translation started from the chat ("แปลไฟล์นี้" with a long
// PDF — backend _translation_job_reply). The answer text carries a
// `<!-- translate-job:<id> -->` marker per job; this card shows that job's real
// progress (polling /translate/status), then the Word/PDF downloads and the
// pages the review flagged. The same jobs are on the แปลเอกสาร page.

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Languages, Loader2 } from "lucide-react";
import {
  downloadTranslation,
  getTranslationReview,
  getTranslationStatus,
  type TranslateJob,
  type TranslateReview,
} from "@/lib/api";

const MARKER_RE = /<!--\s*translate-job:([0-9a-f]{6,32})\s*-->/g;
const POLL_MS = 3000;

// job ids in an answer, and the answer without the markers
export function translateJobs(text: string): { ids: string[]; text: string } {
  const ids = [...text.matchAll(MARKER_RE)].map((m) => m[1]);
  return { ids, text: ids.length ? text.replace(MARKER_RE, "").replace(/\n{3,}/g, "\n\n").trim() : text };
}

export function TranslationCard({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<TranslateJob | null>(null);
  const [gone, setGone] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [review, setReview] = useState<TranslateReview | null>(null);
  const [showReview, setShowReview] = useState(false);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const j = await getTranslationStatus(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "queued" || j.status === "running") timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (stop) return;
        if (e instanceof Error && /404|ไม่พบงานแปล/.test(e.message)) setGone(true);
        else timer = setTimeout(tick, POLL_MS * 2); // transient — keep trying
      }
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  async function download(fmt: "docx" | "pdf") {
    if (!job) return;
    setErr(null);
    try {
      await downloadTranslation(job.id, fmt, `${job.filename.replace(/\.pdf$/i, "")}_แปลไทย.${fmt}`);
    } catch {
      setErr("ดาวน์โหลดไม่สำเร็จ ลองใหม่อีกครั้ง");
    }
  }

  async function toggleReview() {
    if (!job) return;
    if (!review) {
      try {
        setReview(await getTranslationReview(job.id));
      } catch {
        setErr("เปิดรายการหน้าที่ควรตรวจไม่สำเร็จ");
        return;
      }
    }
    setShowReview((v) => !v);
  }

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <div className="mt-3 max-w-md rounded-2xl border border-line bg-surface p-3.5 shadow-sm" data-testid="translate-card">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300">
          <Languages size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-content" title={job?.filename}>
            {job?.filename ?? "งานแปลเอกสาร"}
          </p>
          <p className="text-xs text-muted">
            แปลอังกฤษ → ไทย ทีละหน้า{job?.total ? ` · ${job.total} หน้า` : ""}
          </p>
        </div>
      </div>

      {gone ? (
        <p className="mt-3 text-sm text-muted" role="status">งานแปลนี้ถูกลบแล้ว</p>
      ) : !job ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> กำลังโหลดสถานะ…</p>
      ) : job.status === "queued" || job.status === "running" ? (
        <div className="mt-3" role="status" aria-live="polite">
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface3">
            <div className="h-full bg-violet-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-1.5 flex items-center gap-2 text-sm text-muted" data-testid="translate-progress">
            <Loader2 size={14} className="animate-spin" />
            {job.status === "queued" ? "อยู่ในคิว — รองานแปลก่อนหน้าให้เสร็จ…" : `กำลังแปล ${job.done}/${job.total} หน้า (${pct}%)`}
          </p>
          {job.exceeds_cap && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
              ไฟล์เกิน {job.max_pages} หน้า/งาน — แปล {job.translated_pages} หน้าแรก
            </p>
          )}
        </div>
      ) : job.status === "done" ? (
        <div className="mt-3">
          <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-700 dark:text-emerald-300" data-testid="translate-done">
            <CheckCircle2 size={16} /> แปลเสร็จแล้ว — {job.done} หน้า
          </p>
          {job.review_flagged > 0 && (
            <button type="button" onClick={toggleReview} className="mt-1 text-left text-xs font-medium text-amber-700 hover:underline dark:text-amber-300">
              <AlertTriangle size={12} className="mr-1 inline" />
              มี {job.review_flagged} หน้าที่ควรตรวจทานซ้ำ (เลข/ตาราง) — {showReview ? "ซ่อน" : "ดูรายละเอียด"}
            </button>
          )}
          {showReview && review && (
            <ul className="mt-2 max-h-48 space-y-1.5 overflow-y-auto rounded-lg border border-line p-2 text-xs" data-testid="translate-review">
              {review.pages.length === 0 ? (
                <li className="text-muted">ไม่มีรายละเอียด</li>
              ) : (
                review.pages.map((pg) => (
                  <li key={pg.page}>
                    <span className="font-semibold text-content2">หน้า {pg.page}</span>
                    <ul className="ml-3 list-disc text-muted">
                      {pg.issues.map((it, i) => <li key={i}>{it}</li>)}
                    </ul>
                  </li>
                ))
              )}
            </ul>
          )}
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button type="button" onClick={() => download("docx")} className="ui-btn ui-btn-primary">
              <Download size={14} /> ดาวน์โหลด Word
            </button>
            {job.pdf && (
              <button type="button" onClick={() => download("pdf")} className="ui-btn ui-btn-ghost">
                <Download size={14} /> PDF
              </button>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-3 text-sm text-red-600 dark:text-red-300" role="alert">
          แปลไม่สำเร็จ: {job.error || "เกิดข้อผิดพลาด"} — แนบไฟล์แล้วสั่งแปลใหม่ได้
        </p>
      )}
      {err && <p className="mt-2 text-xs text-red-600 dark:text-red-300" role="alert">{err}</p>}
    </div>
  );
}
