"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { API_BASE, api, getToken } from "@/lib/api";

// ─────────────────────────────────────────────────────────────────────────────
// Types — mirror backend usage_log.build_report's return shape.
// ─────────────────────────────────────────────────────────────────────────────

type Sums = {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_thb: number;
};

type UsageReport = {
  period: { since: string; until: string; months: string[] };
  seat_usd: number;
  seat_thb: number;
  usd_to_thb: number;
  totals: Sums & {
    active_users: number;
    chatgpt_usd: number;
    chatgpt_thb: number;
    difference_thb: number;
    avg_cost_per_request_thb: number;
    break_even_requests_per_month: number | null;
  };
  monthly: (Sums & {
    month: string;
    active_users: number;
    chatgpt_thb: number;
    difference_thb: number;
  })[];
  users: (Sums & {
    username: string;
    months_active: number;
    avg_month_thb: number;
    seat_thb: number;
    siri_cheaper: boolean;
  })[];
  features: (Sums & { feature: string; label: string })[];
  models: (Sums & { model: string })[];
};

type Preset = "month" | "3m" | "6m" | "year";

const PRESET_LABEL: Record<Preset, string> = {
  month: "เดือนนี้",
  "3m": "3 เดือน",
  "6m": "6 เดือน",
  year: "ปีนี้",
};

function bangkokToday(): Date {
  // Calendar day in Asia/Bangkok, as a local Date at midnight.
  const s = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Bangkok" });
  return new Date(`${s}T00:00:00`);
}

function iso(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function presetRange(p: Preset): { since: string; until: string } {
  const today = bangkokToday();
  const start = new Date(today.getFullYear(), today.getMonth(), 1);
  if (p === "3m") start.setMonth(start.getMonth() - 2);
  if (p === "6m") start.setMonth(start.getMonth() - 5);
  if (p === "year") start.setMonth(0);
  return { since: iso(start), until: iso(today) };
}

const fmtInt = (n: number) => Math.round(n).toLocaleString("th-TH");
const fmtThb = (n: number) =>
  n.toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtTokens = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}K` : `${n}`;

// ─────────────────────────────────────────────────────────────────────────────

export function UsagePanel() {
  const [range, setRange] = useState(presetRange("3m"));
  const [seatUsd, setSeatUsd] = useState<string>("");
  const [data, setData] = useState<UsageReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const query = useCallback(() => {
    const q = new URLSearchParams({ since: range.since, until: range.until });
    if (seatUsd.trim()) q.set("seat_usd", seatUsd.trim());
    return q.toString();
  }, [range, seatUsd]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<UsageReport>(`/admin/usage/report?${query()}`);
      setData(res);
      setSeatUsd((s) => s || String(res.seat_usd));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "โหลดข้อมูลไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    load();
    // reload on range change only; the seat price applies on "คำนวณใหม่"
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  async function exportXlsx() {
    if (exporting) return;
    setExporting(true);
    try {
      const token = getToken();
      const res = await fetch(`${API_BASE}/admin/usage/export.xlsx?${query()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error("ดาวน์โหลดไม่สำเร็จ");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `SiriChat_AI_usage_${range.since}_${range.until}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e: unknown) {
      alert("Export Excel ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Toolbar */}
      <div className="bg-surface rounded-2xl border border-line p-4 shadow-sm flex flex-wrap items-end gap-3">
        <div className="flex gap-1 bg-surface2 p-1 rounded-xl">
          {(Object.keys(PRESET_LABEL) as Preset[]).map((p) => (
            <button
              key={p}
              onClick={() => setRange(presetRange(p))}
              className="px-3 py-1.5 text-sm rounded-lg text-content2 hover:bg-surface3"
            >
              {PRESET_LABEL[p]}
            </button>
          ))}
        </div>
        <label className="text-xs text-muted">
          ตั้งแต่
          <input
            type="date"
            value={range.since}
            onChange={(e) => e.target.value && setRange({ ...range, since: e.target.value })}
            className="block mt-1 px-2 py-1.5 rounded-lg border border-line bg-surface text-sm text-content"
          />
        </label>
        <label className="text-xs text-muted">
          ถึง
          <input
            type="date"
            value={range.until}
            onChange={(e) => e.target.value && setRange({ ...range, until: e.target.value })}
            className="block mt-1 px-2 py-1.5 rounded-lg border border-line bg-surface text-sm text-content"
          />
        </label>
        <label className="text-xs text-muted">
          ChatGPT Business (USD/คน/เดือน)
          <input
            type="number"
            min={0}
            step="0.5"
            value={seatUsd}
            onChange={(e) => setSeatUsd(e.target.value)}
            className="block mt-1 w-28 px-2 py-1.5 rounded-lg border border-line bg-surface text-sm text-content"
          />
        </label>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-xl border border-line bg-surface hover:bg-surface2 text-content2"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          คำนวณใหม่
        </button>
        <button
          onClick={exportXlsx}
          disabled={exporting || !data}
          className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-xl bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-60"
        >
          {exporting ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
          ดาวน์โหลด Excel
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-4 text-sm text-red-700">{error}</div>
      )}
      {loading && !data && (
        <div className="flex justify-center py-16">
          <Loader2 size={32} className="text-purple-500 animate-spin" />
        </div>
      )}
      {data && <UsageReportView data={data} />}
    </div>
  );
}

function UsageReportView({ data }: { data: UsageReport }) {
  const t = data.totals;
  const siriCheaper = t.difference_thb >= 0;
  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="ค่าใช้จ่าย SiriChat" value={`฿${fmtThb(t.cost_thb)}`} sub={`$${t.cost_usd.toFixed(2)}`} />
        <Stat
          label="ถ้าใช้ ChatGPT Business แทน"
          value={`฿${fmtThb(t.chatgpt_thb)}`}
          sub={`${t.active_users} คน × $${data.seat_usd}/เดือน`}
        />
        <Stat
          label={siriCheaper ? "SiriChat ประหยัดกว่า" : "ChatGPT Business ถูกกว่า"}
          value={`฿${fmtThb(Math.abs(t.difference_thb))}`}
          tone={siriCheaper ? "good" : "bad"}
          sub="ในช่วงเวลาที่เลือก"
        />
        <Stat
          label="Token รวม"
          value={fmtTokens(t.total_tokens)}
          sub={`เข้า ${fmtTokens(t.prompt_tokens)} · ออก ${fmtTokens(t.completion_tokens)}`}
        />
      </div>

      <div className="bg-surface rounded-2xl border border-line p-5 shadow-sm text-sm text-content2 space-y-1">
        <p>
          คำขอทั้งหมด <b>{fmtInt(t.requests)}</b> ครั้ง · ผู้ใช้ที่ใช้งาน <b>{t.active_users}</b> คน ·
          เฉลี่ย <b>฿{t.avg_cost_per_request_thb.toFixed(2)}</b> ต่อคำขอ
        </p>
        {t.break_even_requests_per_month !== null && (
          <p>
            ผู้ใช้ 1 คนต้องใช้มากกว่า <b>{fmtInt(t.break_even_requests_per_month)}</b> คำขอต่อเดือน
            ChatGPT Business ถึงจะคุ้มกว่า (คิดจากค่าเฉลี่ยต่อคำขอในช่วงนี้)
          </p>
        )}
        <p className="text-xs text-muted">
          ChatGPT Business คิดเงินต่อที่นั่งต่อเดือนไม่ว่าจะใช้มากหรือน้อย · SiriChat คิดตาม token ที่ใช้จริงผ่าน
          OpenAI API · อัตรา ฿{data.usd_to_thb}/USD · ราคาที่นั่งแก้ได้ด้านบน (ตรวจราคาปัจจุบันก่อนรายงาน)
        </p>
      </div>

      <Table
        title="รายเดือน"
        head={["เดือน", "ผู้ใช้", "คำขอ", "Token เข้า", "Token ออก", "SiriChat (฿)", "ChatGPT Business (฿)", "ส่วนต่าง (฿)"]}
        rows={data.monthly.map((m) => [
          m.month,
          m.active_users,
          fmtInt(m.requests),
          fmtTokens(m.prompt_tokens),
          fmtTokens(m.completion_tokens),
          fmtThb(m.cost_thb),
          fmtThb(m.chatgpt_thb),
          <span key="d" className={m.difference_thb >= 0 ? "text-green-600" : "text-red-600"}>
            {m.difference_thb >= 0 ? "+" : ""}
            {fmtThb(m.difference_thb)}
          </span>,
        ])}
      />

      <Table
        title="รายผู้ใช้"
        head={["ผู้ใช้", "เดือนที่ใช้", "คำขอ", "Token รวม", "รวม (฿)", "เฉลี่ย/เดือน (฿)", "Business/เดือน (฿)", "ถูกกว่า"]}
        rows={data.users.slice(0, 100).map((u) => [
          u.username,
          u.months_active,
          fmtInt(u.requests),
          fmtTokens(u.total_tokens),
          fmtThb(u.cost_thb),
          fmtThb(u.avg_month_thb),
          fmtThb(u.seat_thb),
          <span key="c" className={u.siri_cheaper ? "text-green-600" : "text-red-600"}>
            {u.siri_cheaper ? "SiriChat" : "ChatGPT Business"}
          </span>,
        ])}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Table
          title="ตามฟีเจอร์"
          head={["ฟีเจอร์", "คำขอ", "Token รวม", "฿"]}
          rows={data.features.map((f) => [f.label, fmtInt(f.requests), fmtTokens(f.total_tokens), fmtThb(f.cost_thb)])}
        />
        <Table
          title="ตามโมเดล"
          head={["โมเดล", "ครั้ง", "Token รวม", "฿"]}
          rows={data.models.map((m) => [m.model, fmtInt(m.requests), fmtTokens(m.total_tokens), fmtThb(m.cost_thb)])}
        />
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "bad";
}) {
  const color = tone === "good" ? "text-green-600" : tone === "bad" ? "text-red-600" : "text-content";
  return (
    <div className="bg-surface rounded-2xl border border-line p-5 shadow-sm">
      <p className="text-xs text-muted">{label}</p>
      <p className={`text-2xl font-semibold mt-1 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  );
}

function Table({
  title,
  head,
  rows,
}: {
  title: string;
  head: string[];
  rows: ReactNode[][];
}) {
  return (
    <div className="bg-surface rounded-2xl border border-line p-5 shadow-sm overflow-x-auto">
      <h3 className="font-medium text-content mb-3">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">ยังไม่มีข้อมูลในช่วงนี้</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted border-b border-line">
              {head.map((h) => (
                <th key={h} className="py-2 pr-4 font-normal whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-line/60 last:border-0">
                {r.map((c, j) => (
                  <td key={j} className="py-2 pr-4 text-content2 whitespace-nowrap">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
