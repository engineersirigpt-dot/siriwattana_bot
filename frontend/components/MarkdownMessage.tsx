"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";
import "highlight.js/styles/github.css";

type Props = { text: string };

export function MarkdownMessage({ text }: Props) {
  return (
    <div
      className="
        prose prose-sm max-w-none
        text-gray-800 leading-relaxed
        prose-headings:font-semibold prose-headings:text-gray-900
        prose-h1:text-xl prose-h1:mt-4 prose-h1:mb-2
        prose-h2:text-lg prose-h2:mt-4 prose-h2:mb-2
        prose-h3:text-base prose-h3:mt-3 prose-h3:mb-1.5
        prose-h4:text-base prose-h4:mt-3 prose-h4:mb-1.5
        prose-p:my-2 prose-p:leading-relaxed
        prose-strong:text-gray-900 prose-strong:font-semibold
        prose-em:text-gray-800
        prose-a:text-purple-600 prose-a:no-underline hover:prose-a:underline
        prose-ul:my-2 prose-ol:my-2
        prose-li:my-0.5 prose-li:marker:text-purple-500
        prose-blockquote:border-l-4 prose-blockquote:border-purple-300
        prose-blockquote:bg-purple-50/50 prose-blockquote:py-1 prose-blockquote:px-3
        prose-blockquote:rounded-r prose-blockquote:not-italic
        prose-blockquote:text-gray-700
        prose-hr:my-4 prose-hr:border-gray-200
        prose-img:rounded-lg prose-img:my-2
      "
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-purple-50">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border-b border-gray-200 px-4 py-2.5 text-left font-semibold text-purple-900">
              {children}
            </th>
          ),
          tr: ({ children }) => (
            <tr className="border-b border-gray-100 last:border-0 hover:bg-gray-50/60">
              {children}
            </tr>
          ),
          td: ({ children }) => (
            <td className="px-4 py-2.5 align-top text-gray-700">{children}</td>
          ),
          code: ({ className, children, ...rest }) => {
            const isInline = !className;
            if (isInline) {
              return (
                <code
                  className="bg-gray-100 text-purple-700 px-1.5 py-0.5 rounded text-[0.875em] font-mono"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={className} {...rest}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => {
            const childArray = Array.isArray(children) ? children : [children];
            const codeText = extractCodeText(childArray);
            return <CodeBlock raw={codeText}>{children}</CodeBlock>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function extractCodeText(nodes: unknown): string {
  if (typeof nodes === "string") return nodes;
  if (Array.isArray(nodes)) return nodes.map(extractCodeText).join("");
  if (nodes && typeof nodes === "object" && "props" in (nodes as object)) {
    const props = (nodes as { props?: { children?: unknown } }).props;
    return extractCodeText(props?.children);
  }
  return "";
}

function CodeBlock({
  children,
  raw,
}: {
  children: React.ReactNode;
  raw: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore — clipboard may be unavailable on http
    }
  }

  return (
    <div className="relative my-3 group">
      <button
        type="button"
        onClick={copy}
        className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-gray-700/80 hover:bg-gray-600 text-white opacity-0 group-hover:opacity-100 transition-opacity"
        title="คัดลอกโค้ด"
      >
        {copied ? (
          <>
            <Check size={12} /> คัดลอกแล้ว
          </>
        ) : (
          <>
            <Copy size={12} /> คัดลอก
          </>
        )}
      </button>
      <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 overflow-x-auto text-sm">
        {children}
      </pre>
    </div>
  );
}
