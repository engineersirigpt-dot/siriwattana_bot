"use client";

// V4.2 "ความจำของฉัน" — the user's own long-term memory: see, add, delete,
// pause. Memories are saved only when the user asks ("จำไว้ว่า…" in the chat
// or the box here); the backend refuses passwords, ID / account numbers, salary.

import { useCallback, useEffect, useState } from "react";
import { Brain, Loader2, Trash2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import {
  addMemory,
  clearMemory,
  deleteMemory,
  getMemory,
  setMemoryEnabled,
  type MemoryState,
} from "@/lib/api";

export default function MemoryModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [state, setState] = useState<MemoryState | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(async () => {
    try {
      setState(await getMemory());
    } catch {
      setNote({ text: "โหลดความจำไม่สำเร็จ ลองใหม่อีกครั้ง", ok: false });
    }
  }, []);

  useEffect(() => {
    if (open) {
      setNote(null);
      setConfirmClear(false);
      load();
    }
  }, [open, load]);

  async function run(fn: () => Promise<unknown>, okText?: string) {
    setBusy(true);
    try {
      await fn();
      if (okText) setNote({ text: okText, ok: true });
      await load();
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : "ทำรายการไม่สำเร็จ", ok: false });
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const text = draft.trim();
    if (!text) return;
    await run(async () => {
      const msg = await addMemory(text);
      setDraft("");
      setNote({ text: msg, ok: true });
    });
  }

  const items = state?.items ?? [];

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6 space-y-4">
        <div className="flex items-center gap-2 pr-6">
          <Brain size={20} className="text-purple-500" />
          <h2 className="text-lg font-semibold text-content">ความจำของฉัน</h2>
        </div>
        <p className="text-xs text-muted leading-relaxed">
          ข้อมูลที่คุณสั่งให้จำไว้ AI จะใช้ประกอบคำตอบในทุกแชท เห็นได้เฉพาะคุณ — พิมพ์ในแชทว่า “จำไว้ว่า …”
          หรือ “ลืมข้อมูลนี้” ก็ได้ ระบบไม่เก็บรหัสผ่าน เลขบัตร/บัญชี/เบอร์โทร และเงินเดือน
        </p>

        {state && (
          <label className="flex items-center justify-between gap-3 text-sm text-content2">
            <span>ใช้ความจำในการตอบ</span>
            <input
              type="checkbox"
              className="w-4 h-4 accent-purple-500"
              checked={state.enabled}
              disabled={busy}
              onChange={(e) =>
                run(() => setMemoryEnabled(e.target.checked), e.target.checked ? "เปิดใช้ความจำแล้ว" : "ปิดการใช้ความจำแล้ว (ข้อมูลยังเก็บไว้)")
              }
            />
          </label>
        )}

        <div className="max-h-64 overflow-y-auto space-y-2">
          {!state ? (
            <div className="flex justify-center py-6 text-muted">
              <Loader2 size={18} className="animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted py-2">ยังไม่มีข้อมูลที่จำไว้</p>
          ) : (
            items.map((m) => (
              <div
                key={m.id}
                className="flex items-start gap-2 rounded-xl border border-line bg-surface2 px-3 py-2 text-sm text-content2"
              >
                <span className="flex-1 break-words">{m.content}</span>
                <button
                  onClick={() => run(() => deleteMemory(m.id), "ลบแล้ว")}
                  disabled={busy}
                  className="text-faint hover:text-red-500 p-1 rounded-lg"
                  aria-label="ลบ"
                  title="ลบรายการนี้"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))
          )}
        </div>

        <div className="flex gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            maxLength={500}
            placeholder="เพิ่มข้อมูลที่ให้จำ เช่น ฉันอยู่ฝ่ายผลิต"
            className="flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm text-content outline-none focus:border-purple-400"
          />
          <button
            onClick={add}
            disabled={busy || !draft.trim()}
            className="rounded-xl bg-purple-500 hover:bg-purple-600 disabled:opacity-50 text-white text-sm px-4"
          >
            จำ
          </button>
        </div>

        {note && <p className={`text-xs ${note.ok ? "text-green-600" : "text-red-600"}`}>{note.text}</p>}

        {items.length > 0 && (
          <div className="flex justify-end">
            <button
              onClick={() =>
                confirmClear
                  ? run(async () => {
                      await clearMemory();
                      setConfirmClear(false);
                    }, "ลบความจำทั้งหมดแล้ว")
                  : setConfirmClear(true)
              }
              disabled={busy}
              className="text-xs text-red-600 hover:underline"
            >
              {confirmClear ? "กดอีกครั้งเพื่อยืนยันการลบทั้งหมด" : "ลบความจำทั้งหมด"}
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}
