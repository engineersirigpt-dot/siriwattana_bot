"use client";

// What the AI is doing right now — only states the backend actually sent on
// /chat/stream ("status" events), never invented. Before the first one it
// says "กำลังคิด…" (the request is out, nothing back yet). Earlier steps stay
// as small ticked chips; a real elapsed-time counter shows on slow steps.

import { useEffect, useState } from "react";
import { Check, FileText, Globe, Image as ImageIcon, Link2, PenLine, Sigma } from "lucide-react";

type Step = { now: string; done: string; icon: typeof Globe; slow?: boolean };

export const STEPS: Record<string, Step> = {
  generating_image: { now: "กำลังสร้างรูป…", done: "สร้างรูป", icon: ImageIcon, slow: true },
  agent_searching: { now: "กำลังค้นข้อมูลจากเว็บ…", done: "ค้นเว็บแล้ว", icon: Globe },
  agent_reading: { now: "กำลังอ่านลิงก์…", done: "อ่านลิงก์แล้ว", icon: Link2 },
  agent_coding: { now: "กำลังวิเคราะห์ข้อมูลด้วย Python…", done: "วิเคราะห์ข้อมูลแล้ว", icon: Sigma, slow: true },
  agent_document: { now: "กำลังจัดทำเอกสารและตรวจไฟล์…", done: "ทำเอกสารแล้ว", icon: FileText, slow: true },
  agent_writing: { now: "กำลังเรียบเรียงคำตอบ…", done: "เรียบเรียงแล้ว", icon: PenLine },
};

export function knownStep(state: string): boolean {
  return state in STEPS;
}

// Finished steps as small ticked chips ("ค้นเว็บแล้ว"). Also shown above the
// answer once text arrives, so the user still sees the web was searched.
export function StepChips({ steps }: { steps: string[] }) {
  const done = steps.map((s) => STEPS[s]).filter(Boolean);
  if (!done.length) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1.5" data-testid="step-chips">
      {done.map((s, i) => (
        <span key={i} className="ui-chip">
          <Check size={11} className="text-green-600" /> {s.done}
        </span>
      ))}
    </div>
  );
}

export function StatusLine({
  steps,
  startedAt,
  label,
  hint,
}: {
  steps: string[];
  startedAt?: number;
  // overrides "กำลังคิด…" before any status (e.g. a file upload being processed)
  label?: string;
  hint?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const current = steps.length ? STEPS[steps[steps.length - 1]] : undefined;
  const secs = startedAt ? Math.floor((now - startedAt) / 1000) : 0;
  const Icon = current?.icon;
  return (
    <div className="py-1" data-testid="status-line">
      <StepChips steps={steps.slice(0, -1)} />
      <div className="flex items-center gap-2 text-sm text-muted" role="status" aria-live="polite">
        {Icon ? (
          <Icon size={15} className="text-purple-500 animate-pulse" aria-hidden />
        ) : (
          <span className="flex gap-1" aria-hidden>
            {[0, 150, 300].map((d) => (
              <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-purple-400" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        )}
        <span data-testid="status-label">{current?.now ?? label ?? "กำลังคิด…"}</span>
        {secs >= 3 && <span className="text-xs text-faint">· {secs} วิ</span>}
      </div>
      {(hint || current?.slow) && secs >= 3 && (
        <p className="mt-1 text-xs text-faint">{hint ?? "ขั้นตอนนี้อาจใช้เวลา 1–2 นาที"}</p>
      )}
    </div>
  );
}
