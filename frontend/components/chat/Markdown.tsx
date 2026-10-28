"use client";

// V5 answer renderer. Same Markdown stack as the classic MarkdownMessage
// (react-markdown + GFM + highlight.js github theme), plus: long words and
// links wrap instead of widening the page, tables scroll inside their own box
// with a sticky header, code blocks get a header with the language and an
// always-visible copy button (hover-only buttons don't work on phones), links
// open in a new tab. Memoised: a finished answer isn't re-parsed while another
// one streams.

import { memo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";
import "highlight.js/styles/github.css";

// live: still streaming — draws the typing caret at the end of the last
// paragraph/list item (globals.css .md-live) instead of on a line of its own.
function Markdown({ text, live = false }: { text: string; live?: boolean }) {
  return (
    <div
      data-testid="md"
      style={{ fontSize: "15px", overflowWrap: "anywhere" }}
      className={`${live ? "md-live " : ""}
        prose prose-sm max-w-none min-w-0
        text-content leading-relaxed
        prose-headings:font-semibold prose-headings:text-content
        prose-h1:text-xl prose-h1:mt-4 prose-h1:mb-2
        prose-h2:text-lg prose-h2:mt-4 prose-h2:mb-2
        prose-h3:text-base prose-h3:mt-3 prose-h3:mb-1.5
        prose-h4:text-base prose-h4:mt-3 prose-h4:mb-1.5
        prose-p:my-2 prose-p:leading-relaxed
        prose-strong:text-content prose-strong:font-semibold
        prose-em:text-content
        prose-a:text-purple-600 prose-a:underline-offset-2 hover:prose-a:underline
        prose-ul:my-2 prose-ol:my-2
        prose-li:my-0.5 prose-li:marker:text-purple-500
        prose-blockquote:border-l-4 prose-blockquote:border-purple-300
        prose-blockquote:bg-purple-50/50 dark:prose-blockquote:bg-purple-900/20 prose-blockquote:py-1 prose-blockquote:px-3
        prose-blockquote:rounded-r prose-blockquote:not-italic
        prose-blockquote:text-content2
        prose-hr:my-4 prose-hr:border-line
      `}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ src, alt }) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={typeof src === "string" ? src : undefined} alt={alt ?? ""} loading="lazy"
                 className="my-2 max-w-full h-auto rounded-lg border border-line" />
          ),
          table: ({ children }) => (
            <div className="my-3 max-h-[28rem] overflow-auto rounded-xl border border-line shadow-sm" role="region"
                 aria-label="ตาราง" tabIndex={0}>
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="sticky top-0 z-[1] bg-purple-50 dark:bg-purple-900/40">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border-b border-line px-3 py-2 text-left font-semibold text-purple-900 dark:text-purple-100 whitespace-nowrap">
              {children}
            </th>
          ),
          tr: ({ children }) => (
            <tr className="border-b border-line last:border-0 hover:bg-surface2/60">{children}</tr>
          ),
          td: ({ children }) => <td className="px-3 py-2 align-top text-content2">{children}</td>,
          code: ({ className, children, ...rest }) =>
            !className ? (
              <code className="bg-surface3 text-purple-700 dark:text-purple-300 px-1.5 py-0.5 rounded text-[0.875em] font-mono" {...rest}>
                {children}
              </code>
            ) : (
              <code className={className} {...rest}>
                {children}
              </code>
            ),
          pre: ({ children }) => {
            const arr = Array.isArray(children) ? children : [children];
            return <CodeBlock raw={textOf(arr)} lang={langOf(arr)}>{children}</CodeBlock>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default memo(Markdown);

function textOf(nodes: unknown): string {
  if (typeof nodes === "string") return nodes;
  if (Array.isArray(nodes)) return nodes.map(textOf).join("");
  if (nodes && typeof nodes === "object" && "props" in (nodes as object)) {
    return textOf((nodes as { props?: { children?: unknown } }).props?.children);
  }
  return "";
}

function langOf(nodes: unknown[]): string {
  for (const n of nodes) {
    const cls = (n as { props?: { className?: string } })?.props?.className ?? "";
    const m = /language-([\w+#-]+)/.exec(cls);
    if (m) return m[1];
  }
  return "";
}

function CodeBlock({ children, raw, lang }: { children: ReactNode; raw: string; lang: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(raw);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = raw;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* nothing more we can do on plain http */
      }
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="not-prose my-3 overflow-hidden rounded-xl border border-line" data-testid="code-block">
      <div className="flex items-center justify-between bg-surface3 px-3 py-1.5 text-xs text-muted">
        <span className="font-mono">{lang || "code"}</span>
        <button type="button" onClick={copy} className="ui-icon-btn gap-1 px-2 py-0.5 text-xs" aria-label="คัดลอกโค้ด">
          {copied ? <Check size={12} className="text-green-600" /> : <Copy size={12} />}
          {copied ? "คัดลอกแล้ว" : "คัดลอก"}
        </button>
      </div>
      <pre className="m-0 overflow-x-auto bg-white text-sm [&>code]:!bg-transparent [&>code]:block [&>code]:p-4">
        {children}
      </pre>
    </div>
  );
}
