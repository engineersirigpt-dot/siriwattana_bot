"use client";

// One chat message in the V5 page. Memoised: while an answer streams only its
// own row re-renders (the old page re-parsed every answer's Markdown on every
// token, which made long chats stutter).

import { memo } from "react";
import { Check, Copy, FileDown, FileText, Sheet, Square, ThumbsDown, ThumbsUp } from "lucide-react";
import Markdown from "@/components/chat/Markdown";
import { FileCard } from "@/components/chat/FileCard";
import { StatusLine, StepChips } from "@/components/chat/StatusLine";
import { fileKind, withPreviews } from "@/lib/fileKinds";
import {
  AttachmentImage,
  ExportOfferActions,
  hasMarkdownTable,
  isImageSource,
  requestedExportFormat,
  type Attachment,
  type Msg,
} from "@/app/chat/ClassicChatPage";

// V5 additions on a message (all optional, so a classic Msg still fits)
export type MsgV5 = Msg & {
  steps?: string[];        // backend status events received while streaming (or from the saved source)
  startedAt?: number;      // when the request went out (real elapsed time)
  waitLabel?: string;      // label before any status (e.g. "กำลังประมวลผลไฟล์…")
  waitHint?: string;       // e.g. proofreading takes 1–2 min
  stopped?: boolean;       // the user pressed หยุด mid-answer
};

export type RowActions = {
  copy: (index: number, text: string) => void;
  thumb: (index: number, messageId: number, vote: "up" | "down") => void;
  exportPdf: (m: Msg) => void;
  exportDocx: (text: string) => void;
  exportXlsx: (text: string) => void;
  alert: (msg: string) => void;
};

function Attachments({ items, onBubble, large }: { items: Attachment[]; onBubble: boolean; large?: boolean }) {
  return (
    <div className={`flex flex-col gap-2 ${onBubble ? "items-end" : "items-start"}`}>
      {withPreviews(items).map(({ item, preview }) =>
        fileKind(item.filename, item.content_type).kind === "image" ? (
          <AttachmentImage key={item.id} attachment={item} onUserBubble={onBubble} large={large} />
        ) : (
          <FileCard key={item.id} file={item} preview={preview} onBubble={onBubble} />
        ),
      )}
    </div>
  );
}

function MessageRow({
  m,
  index,
  copied,
  readOnly,
  exportTarget,
  actions,
}: {
  m: MsgV5;
  index: number;
  copied: boolean;
  readOnly: boolean;
  exportTarget?: { message: Msg | null; question?: string };
  actions: RowActions;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end" data-testid="msg-user">
        <div className="flex max-w-[88%] flex-col items-end gap-2 sm:max-w-2xl">
          {m.attachments && m.attachments.length > 0 && (
            <Attachments items={m.attachments} onBubble={false} />
          )}
          {m.text && m.text !== "[ไฟล์แนบ]" && (
            <div className="whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-gradient-to-r from-purple-500 to-purple-600 px-4 py-3 text-[15px] leading-relaxed text-white shadow-sm sm:px-5">
              {m.text}
            </div>
          )}
        </div>
      </div>
    );
  }

  const streaming = !!m.streaming;
  const fmt = requestedExportFormat(m.question);
  const showActions = m.text && !streaming && m.source !== "export_offer" && m.source !== "blocked";
  return (
    <div className="flex justify-start gap-2.5 sm:gap-3" data-testid="msg-bot" data-streaming={streaming ? "1" : "0"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/Logo_siri.jpg" alt="" className="mt-0.5 h-8 w-8 flex-shrink-0 rounded-full border border-line object-cover shadow-sm sm:h-9 sm:w-9" />
      <div className="min-w-0 flex-1 pt-0.5">
        {m.attachments && m.attachments.length > 0 && isImageSource(m.source) && (
          <Attachments items={m.attachments} onBubble={false} large />
        )}
        {streaming && m.generatingImage ? (
          <div className="w-64 sm:w-80">
            <div className="relative aspect-square w-full overflow-hidden rounded-xl border border-line bg-gradient-to-br from-app to-surface3">
              <div className="absolute inset-0 -translate-x-full animate-[shimmer_1.6s_infinite] bg-gradient-to-r from-transparent via-white/60 to-transparent" />
              <div className="absolute inset-0 flex items-center justify-center p-3">
                <StatusLine steps={["generating_image"]} startedAt={m.startedAt} />
              </div>
            </div>
          </div>
        ) : streaming && !m.text ? (
          <StatusLine steps={m.steps ?? []} startedAt={m.startedAt} label={m.waitLabel} hint={m.waitHint} />
        ) : (
          (m.text || streaming) && (
            <>
              {m.steps && <StepChips steps={m.steps.filter((s) => s !== "agent_writing")} />}
              <Markdown text={m.text} live={streaming} />
            </>
          )
        )}
        {m.stopped && (
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-muted" data-testid="stopped-note">
            <Square size={10} fill="currentColor" /> หยุดแล้ว — คำตอบอาจไม่ครบ
          </p>
        )}
        {m.attachments && m.attachments.length > 0 && !isImageSource(m.source) && !streaming && (
          <div className="mt-3">
            <Attachments items={m.attachments} onBubble={false} large />
          </div>
        )}
        {m.text && !streaming && m.source === "export_offer" && exportTarget && (
          <ExportOfferActions
            targetMessage={exportTarget.message}
            targetQuestion={exportTarget.question}
            format={fmt ?? "pdf"}
            onAlert={actions.alert}
          />
        )}
        {showActions && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1" data-testid="msg-actions">
            <button type="button" onClick={() => actions.copy(index, m.text)} className="ui-icon-btn" aria-label="คัดลอกคำตอบ" title="คัดลอกคำตอบ">
              {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}
              {copied && <span className="ml-1 text-xs text-green-600">คัดลอกแล้ว</span>}
            </button>
            {fmt === "pdf" && (
              <button type="button" onClick={() => actions.exportPdf(m)} className="ui-btn px-2 py-1 text-xs text-red-600 bg-red-50 hover:bg-red-100 dark:bg-red-900/30">
                <FileDown size={13} /> PDF
              </button>
            )}
            {fmt === "docx" && (
              <button type="button" onClick={() => actions.exportDocx(m.text)} className="ui-btn px-2 py-1 text-xs text-blue-600 bg-blue-50 hover:bg-blue-100 dark:bg-blue-900/30">
                <FileText size={13} /> Word
              </button>
            )}
            {(fmt === "xlsx" || hasMarkdownTable(m.text)) && (
              <button type="button" onClick={() => actions.exportXlsx(m.text)} className="ui-btn px-2 py-1 text-xs text-green-700 bg-green-50 hover:bg-green-100 dark:bg-green-900/30 dark:text-green-300 dark:hover:bg-green-900/50">
                <Sheet size={13} /> Excel
              </button>
            )}
            {m.id && !readOnly && (
              <>
                <span className="mx-0.5 h-3.5 w-px bg-line" aria-hidden />
                <button
                  type="button"
                  onClick={() => actions.thumb(index, m.id!, "up")}
                  className={`ui-icon-btn ${m.myVote === "up" ? "bg-green-50 text-green-600 dark:bg-green-900/30" : ""}`}
                  aria-label="คำตอบนี้มีประโยชน์"
                  aria-pressed={m.myVote === "up"}
                  title="คำตอบนี้มีประโยชน์"
                >
                  <ThumbsUp size={14} />
                </button>
                <button
                  type="button"
                  onClick={() => actions.thumb(index, m.id!, "down")}
                  className={`ui-icon-btn ${m.myVote === "down" ? "bg-red-50 text-red-600 dark:bg-red-900/30" : ""}`}
                  aria-label="คำตอบนี้ยังไม่ดี"
                  aria-pressed={m.myVote === "down"}
                  title="คำตอบนี้ยังไม่ดี — ส่งให้แอดมินปรับปรุง"
                >
                  <ThumbsDown size={14} />
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(MessageRow);
