// File kinds for the V5 chat UI: label, colour and what we can preview in the
// browser. Decided by extension first (browsers often send an empty or odd
// MIME for Office files), then by MIME.

export type FileKind = "pdf" | "word" | "excel" | "csv" | "powerpoint" | "image" | "text" | "audio" | "other";

export type KindInfo = {
  kind: FileKind;
  label: string;
  // tile colours (icon background + text)
  tile: string;
  // can the browser show it in our preview window?
  preview: "image" | "pdf" | "csv" | "text" | null;
};

const EXT: Record<string, FileKind> = {
  ".pdf": "pdf",
  ".doc": "word", ".docx": "word",
  ".xls": "excel", ".xlsx": "excel",
  ".csv": "csv", ".tsv": "csv",
  ".ppt": "powerpoint", ".pptx": "powerpoint",
  ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image", ".bmp": "image",
  ".txt": "text", ".md": "text", ".json": "text", ".log": "text", ".xml": "text", ".html": "text",
  ".mp3": "audio", ".m4a": "audio", ".wav": "audio", ".ogg": "audio", ".flac": "audio", ".webm": "audio",
};

const INFO: Record<FileKind, Omit<KindInfo, "kind">> = {
  pdf: { label: "PDF", tile: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300", preview: "pdf" },
  word: { label: "Word", tile: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300", preview: null },
  excel: { label: "Excel", tile: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300", preview: null },
  csv: { label: "CSV", tile: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300", preview: "csv" },
  powerpoint: { label: "PowerPoint", tile: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300", preview: null },
  image: { label: "รูปภาพ", tile: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300", preview: "image" },
  text: { label: "ข้อความ", tile: "bg-slate-100 text-slate-700 dark:bg-slate-700/60 dark:text-slate-200", preview: "text" },
  audio: { label: "เสียง", tile: "bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300", preview: null },
  other: { label: "ไฟล์", tile: "bg-slate-100 text-slate-700 dark:bg-slate-700/60 dark:text-slate-200", preview: null },
};

export function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

export function fileKind(name: string, contentType = ""): KindInfo {
  let kind: FileKind = EXT[extensionOf(name)] ?? "other";
  if (kind === "other") {
    const ct = contentType.toLowerCase();
    if (ct === "application/pdf") kind = "pdf";
    else if (ct.startsWith("image/")) kind = "image";
    else if (ct.startsWith("audio/")) kind = "audio";
    else if (ct.includes("spreadsheet") || ct.includes("excel")) kind = "excel";
    else if (ct.includes("wordprocessing") || ct.includes("msword")) kind = "word";
    else if (ct.includes("presentation") || ct.includes("powerpoint")) kind = "powerpoint";
    else if (ct === "text/csv") kind = "csv";
    else if (ct.startsWith("text/")) kind = "text";
  }
  return { kind, ...INFO[kind] };
}

export function formatSize(bytes: number): string {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The Document Engine (V3) returns "<name>_ตัวอย่าง.png" next to a .docx/.pptx/.pdf
// it made: that picture is the document's preview, not a separate image.
export const PREVIEW_SUFFIX = "_ตัวอย่าง.png";

export function previewFor<T extends { filename: string }>(file: T, all: T[]): T | undefined {
  const dot = file.filename.lastIndexOf(".");
  const stem = dot > 0 ? file.filename.slice(0, dot) : file.filename;
  return all.find((a) => a !== file && a.filename === stem + PREVIEW_SUFFIX);
}

// Split attachments into files to show as cards and preview pictures that
// belong to one of them (shown inside that card instead of on their own).
export function withPreviews<T extends { filename: string; content_type: string }>(
  atts: T[],
): { item: T; preview?: T }[] {
  const used = new Set<T>();
  const out: { item: T; preview?: T }[] = [];
  for (const a of atts) {
    if (fileKind(a.filename, a.content_type).kind === "image") continue;
    const p = previewFor(a, atts);
    if (p) used.add(p);
    out.push({ item: a, preview: p });
  }
  // images that aren't a document's preview stay as images
  return [...out, ...atts.filter((a) => fileKind(a.filename, a.content_type).kind === "image" && !used.has(a))
    .map((a) => ({ item: a }))];
}
