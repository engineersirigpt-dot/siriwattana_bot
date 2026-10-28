"use client";

// V5 file card: name, type, size, "ดูตัวอย่าง" when the browser can show it
// (image, PDF, CSV, text — or the Document Engine's preview picture of a
// Word/PowerPoint/Excel file) and "ดาวน์โหลด". Every fetch goes through the
// owner-checked /attachments/{id}; a missing or forbidden file says so on the
// card instead of failing silently.

import { useEffect, useState } from "react";
import { AlertTriangle, Download, Eye, FileText, Image as ImageIcon, Loader2, Sheet, Sparkles, X } from "lucide-react";
import { AttachmentError, fetchAttachment, saveBlob } from "@/lib/api";
import { fileKind, formatSize, type KindInfo } from "@/lib/fileKinds";
import { Modal } from "@/components/Modal";

export type CardFile = {
  id: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  generated?: boolean;
};

function KindIcon({ info, size = 18 }: { info: KindInfo; size?: number }) {
  if (info.kind === "excel" || info.kind === "csv") return <Sheet size={size} />;
  if (info.kind === "image") return <ImageIcon size={size} />;
  return <FileText size={size} />;
}

export function FileCard({
  file,
  preview,
  onBubble = false,
}: {
  file: CardFile;
  // the Document Engine's "<name>_ตัวอย่าง.png" for this file, if any
  preview?: CardFile;
  // on the purple user bubble (light-on-colour styling)
  onBubble?: boolean;
}) {
  const info = fileKind(file.filename, file.content_type);
  const [busy, setBusy] = useState<"open" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState(false);
  const pending = file.id < 0;
  const canPreview = !pending && (!!preview || info.preview !== null);

  async function download() {
    if (pending || busy) return;
    setBusy("save");
    setError(null);
    try {
      saveBlob(await fetchAttachment(file.id), file.filename);
    } catch (e) {
      setError(e instanceof AttachmentError ? e.message : "ดาวน์โหลดไม่สำเร็จ");
    } finally {
      setBusy(null);
    }
  }

  const tone = onBubble
    ? "bg-white/15 text-white border-white/20"
    : "bg-surface text-content2 border-line";
  return (
    <div
      data-testid="file-card"
      data-kind={info.kind}
      className={`flex w-full max-w-sm items-center gap-3 rounded-2xl border px-3 py-2.5 shadow-sm ${tone}`}
    >
      <div
        className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl ${onBubble ? "bg-white/20 text-white" : info.tile}`}
        aria-hidden
      >
        <KindIcon info={info} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={file.filename}>
          {file.filename}
        </p>
        {error ? (
          <p className={`flex items-center gap-1 text-xs ${onBubble ? "text-amber-200" : "text-red-600"}`} role="alert">
            <AlertTriangle size={12} className="flex-shrink-0" />
            {error}
          </p>
        ) : (
          <p className={`flex items-center gap-1 text-xs ${onBubble ? "text-white/75" : "text-muted"}`}>
            <span>{info.label}</span>
            {file.size_bytes > 0 && <span>· {formatSize(file.size_bytes)}</span>}
            {file.generated && (
              <span className="inline-flex items-center gap-0.5 text-purple-600 dark:text-purple-300">
                · <Sparkles size={11} /> สร้างโดย AI
              </span>
            )}
            {pending && <span>· กำลังส่ง…</span>}
          </p>
        )}
      </div>
      {canPreview && (
        <button
          type="button"
          onClick={() => setViewing(true)}
          className={onBubble ? "ui-icon-btn-on-color" : "ui-icon-btn"}
          aria-label={`ดูตัวอย่าง ${file.filename}`}
          title="ดูตัวอย่าง"
        >
          <Eye size={16} />
        </button>
      )}
      {!pending && (
        <button
          type="button"
          onClick={download}
          disabled={!!busy}
          className={onBubble ? "ui-icon-btn-on-color" : "ui-icon-btn"}
          aria-label={`ดาวน์โหลด ${file.filename}`}
          title="ดาวน์โหลด"
        >
          {busy === "save" ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
        </button>
      )}
      {viewing && (
        <FilePreview
          file={file}
          info={info}
          preview={preview}
          onClose={() => setViewing(false)}
          onDownload={download}
          onError={(msg) => {
            setViewing(false);
            setError(msg);
          }}
        />
      )}
    </div>
  );
}

function FilePreview({
  file,
  info,
  preview,
  onClose,
  onDownload,
  onError,
}: {
  file: CardFile;
  info: KindInfo;
  preview?: CardFile;
  onClose: () => void;
  onDownload: () => void;
  onError: (msg: string) => void;
}) {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "url"; url: string; as: "image" | "pdf" } | { kind: "text"; text: string; csv: boolean }
  >({ kind: "loading" });

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    const source = preview ?? file;
    const as = preview ? "image" : info.preview;
    fetchAttachment(source.id)
      .then(async (blob) => {
        if (cancelled) return;
        if (as === "csv" || as === "text") {
          setState({ kind: "text", text: (await blob.text()).slice(0, 200_000), csv: as === "csv" });
        } else {
          // a PDF blob must say application/pdf for the browser viewer
          url = URL.createObjectURL(as === "pdf" ? new Blob([blob], { type: "application/pdf" }) : blob);
          setState({ kind: "url", url, as: as === "pdf" ? "pdf" : "image" });
        }
      })
      .catch((e) => !cancelled && onError(e instanceof AttachmentError ? e.message : "เปิดตัวอย่างไม่สำเร็จ"));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, preview?.id]);

  return (
    <Modal open onClose={onClose} wide>
      <div className="flex max-h-[90dvh] flex-col">
        <div className="flex items-center gap-3 border-b border-line px-5 py-3 pr-12">
          <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${info.tile}`} aria-hidden>
            <KindIcon info={info} size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-content" title={file.filename}>{file.filename}</p>
            <p className="text-xs text-muted">
              {preview ? "ภาพตัวอย่างหน้าแรก ๆ ของเอกสาร — ไฟล์จริงแก้ไขได้" : info.label}
            </p>
          </div>
          <button type="button" onClick={onDownload} className="ui-btn ui-btn-primary">
            <Download size={15} /> ดาวน์โหลด
          </button>
        </div>
        <div className="min-h-[12rem] flex-1 overflow-auto bg-surface2 p-3">
          {state.kind === "loading" && (
            <div className="flex h-48 items-center justify-center text-muted">
              <Loader2 className="animate-spin" size={20} />
            </div>
          )}
          {state.kind === "url" && state.as === "image" && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={state.url} alt={file.filename} className="mx-auto max-h-[75dvh] max-w-full rounded-lg object-contain shadow" />
          )}
          {state.kind === "url" && state.as === "pdf" && (
            <iframe src={state.url} title={file.filename} className="h-[75dvh] w-full rounded-lg border border-line bg-white" />
          )}
          {state.kind === "text" && (state.csv ? <CsvTable text={state.text} /> : (
            <pre className="whitespace-pre-wrap break-words rounded-lg bg-surface p-4 text-sm text-content2">{state.text}</pre>
          ))}
        </div>
      </div>
    </Modal>
  );
}

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text).slice(0, 200);
  if (rows.length === 0) return <p className="text-sm text-muted">ไฟล์ว่าง</p>;
  return (
    <div className="overflow-auto rounded-lg border border-line bg-surface">
      <table className="min-w-full text-sm" data-testid="csv-preview">
        <thead className="sticky top-0 bg-purple-50 dark:bg-purple-900/40">
          <tr>{rows[0].map((h, i) => <th key={i} className="whitespace-nowrap px-3 py-2 text-left font-semibold">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(1).map((r, i) => (
            <tr key={i} className="border-t border-line">
              {r.map((c, j) => <td key={j} className="whitespace-nowrap px-3 py-1.5 text-content2">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Small CSV reader: quoted fields, doubled quotes, comma or tab.
function parseCsv(text: string): string[][] {
  const sep = text.split("\n", 1)[0].includes("\t") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === sep) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim()));
}

// The classic pending-file chip with a type label (V5 composer).
export function PendingFile({ file, onRemove }: { file: File; onRemove: () => void }) {
  const info = fileKind(file.name, file.type);
  const [thumb, setThumb] = useState<string | null>(null);
  useEffect(() => {
    if (info.kind !== "image") return;
    const url = URL.createObjectURL(file);
    setThumb(url);
    return () => URL.revokeObjectURL(url);
  }, [file, info.kind]);
  return (
    <div data-testid="pending-file" className="flex max-w-[15rem] items-center gap-2 rounded-xl border border-line bg-surface px-2.5 py-2 text-sm shadow-sm">
      {thumb ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={thumb} alt="" className="h-9 w-9 flex-shrink-0 rounded-md object-cover" />
      ) : (
        <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md ${info.tile}`}>
          <KindIcon info={info} size={16} />
        </div>
      )}
      <div className="min-w-0">
        <p className="truncate text-content" title={file.name}>{file.name}</p>
        <p className="text-xs text-muted">{info.label} · {formatSize(file.size)}</p>
      </div>
      <button type="button" onClick={onRemove} className="ui-icon-btn" aria-label={`เอา ${file.name} ออก`}>
        <X size={15} />
      </button>
    </div>
  );
}
