"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  Brain,
  CalendarDays,
  Database,
  DollarSign,
  Download,
  Loader2,
  MessageSquare,
  RefreshCw,
  Shield,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TrendingDown,
  TrendingUp,
  Users,
  Wand2,
} from "lucide-react";
import { API_BASE, api, getToken } from "@/lib/api";

// ─────────────────────────────────────────────────────────────────────────────
// Types — mirror admin_pg.admin_dashboard_overview_pg's return shape.
// ─────────────────────────────────────────────────────────────────────────────

type KpiMetric = {
  value: number;
  prev: number;
  delta_pct: number | null;
};

type Overview = {
  range: {
    label: string;
    human: string;
    since: string;
    until: string;
    prev_since: string;
    prev_until: string;
  };
  kpis: {
    active_users: KpiMetric;
    new_sessions: KpiMetric;
    messages: KpiMetric;
    cost_thb: KpiMetric;
  };
  source_distribution: {
    brain: number;
    rag: number;
    llm: number;
    files: number;
    blocked: number;
  };
  answered_total: number;
  usage_trend: { day: string; sessions: number; messages: number }[];
  top_questions: { question: string; count: number }[];
  top_users: { username: string; sessions: number; messages: number }[];
  pending_count: number;
  feedback: {
    up: number;
    down: number;
    total: number;
    satisfaction_pct: number | null;
  };
  recent_downvotes: {
    message_id: number;
    question: string;
    reason: string | null;
    username: string;
    created_at: string | null;
  }[];
  cost: {
    real_usd: number;
    estimated_usd: number;
    total_usd: number;
    total_thb: number;
    rows_with_real_cost: number;
    rows_estimated: number;
    by_model: {
      model: string;
      cost_usd: number;
      cost_thb: number;
      rows: number;
    }[];
  };
  translation?: {
    cost_thb: number;
    jobs: number;
    pages: number;
    cost_thb_all: number;
    jobs_all: number;
    pages_all: number;
  };
  safety: {
    blocked_total: number;
    blocked_by_category: Record<string, number>;
    failed_logins: number;
    login_blocked_disabled: number;
  };
};

type RangeKey = "today" | "7d" | "30d";

const RANGE_LABEL: Record<RangeKey, string> = {
  today: "วันนี้",
  "7d": "7 วัน",
  "30d": "30 วัน",
};

// Brand palette — keep these in sync with the rest of the admin page (purple).
const COLORS = {
  brain: "#7c3aed",   // purple
  rag: "#0ea5e9",     // sky
  llm: "#94a3b8",     // slate
  files: "#f59e0b",   // amber
  blocked: "#ef4444", // red
};

// ─────────────────────────────────────────────────────────────────────────────

export function DashboardPanel() {
  const [range, setRange] = useState<RangeKey>("7d");
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);

  const fetchOverview = useCallback(async (rangeKey: RangeKey) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<Overview>(
        `/admin/dashboard/overview?range=${rangeKey}`,
      );
      setData(res);
      setLastFetchedAt(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "โหลดข้อมูลไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview(range);
  }, [range, fetchOverview]);

  // Optional auto-refresh: keep the dashboard live without manual reload. Off
  // by default so we don't burn API budget on idle admin tabs.
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => fetchOverview(range), 60_000);
    return () => clearInterval(id);
  }, [autoRefresh, range, fetchOverview]);

  async function handleExportPdf() {
    if (!data || exporting) return;
    setExporting(true);
    try {
      const token = getToken();
      const res = await fetch(
        `${API_BASE}/admin/dashboard/export-pdf?range=${range}`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        },
      );
      if (!res.ok) throw new Error("Export ล้มเหลว");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Sirivatana_Dashboard_${range}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e: unknown) {
      alert("Export PDF ไม่สำเร็จ: " + (e instanceof Error ? e.message : ""));
    } finally {
      setExporting(false);
    }
  }

  if (loading && !data) {
    return (
      <div className="flex justify-center items-center py-20">
        <Loader2 size={32} className="text-purple-500 animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-2xl p-6 text-center">
        <p className="text-red-700 font-medium">โหลดข้อมูลไม่สำเร็จ</p>
        <p className="text-sm text-red-600 mt-1">{error}</p>
        <button
          onClick={() => fetchOverview(range)}
          className="mt-3 px-3 py-1.5 text-sm bg-red-100 hover:bg-red-200 rounded-lg text-red-700"
        >
          ลองอีกครั้ง
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-6">
      <DashboardToolbar
        range={range}
        onRangeChange={setRange}
        autoRefresh={autoRefresh}
        onAutoRefreshChange={setAutoRefresh}
        onRefresh={() => fetchOverview(range)}
        onExportPdf={handleExportPdf}
        exporting={exporting}
        lastFetchedAt={lastFetchedAt}
        loading={loading}
      />

      <ExecutiveSummary data={data} />

      <KpiCards kpis={data.kpis} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <UsageTrendChart trend={data.usage_trend} />
        <SourceDistributionChart distribution={data.source_distribution} />
      </div>

      <CostBreakdownCard cost={data.cost} />

      <TranslationCostCard t={data.translation} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TopQuestionsCard items={data.top_questions} />
        <TopUsersCard items={data.top_users} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <SatisfactionCard feedback={data.feedback} />
        <RecentDownvotesCard
          items={data.recent_downvotes}
          className="lg:col-span-2"
        />
      </div>

      <SafetyCard
        safety={data.safety}
        pendingCount={data.pending_count}
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function DashboardToolbar({
  range,
  onRangeChange,
  autoRefresh,
  onAutoRefreshChange,
  onRefresh,
  onExportPdf,
  exporting,
  lastFetchedAt,
  loading,
}: {
  range: RangeKey;
  onRangeChange: (r: RangeKey) => void;
  autoRefresh: boolean;
  onAutoRefreshChange: (b: boolean) => void;
  onRefresh: () => void;
  onExportPdf: () => void;
  exporting: boolean;
  lastFetchedAt: Date | null;
  loading: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 bg-white/80 backdrop-blur-sm rounded-2xl border border-gray-100 px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2">
        <CalendarDays size={16} className="text-purple-600" />
        <div className="flex bg-gray-100 rounded-lg p-0.5">
          {(["today", "7d", "30d"] as RangeKey[]).map((r) => (
            <button
              key={r}
              onClick={() => onRangeChange(r)}
              className={`px-3 py-1.5 text-sm rounded-md transition-all ${
                range === r
                  ? "bg-white text-purple-700 shadow-sm font-medium"
                  : "text-gray-600 hover:text-gray-800"
              }`}
            >
              {RANGE_LABEL[r]}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {lastFetchedAt && (
          <span className="text-xs text-gray-400 hidden sm:inline">
            อัพเดต {lastFetchedAt.toLocaleTimeString("th-TH")}
          </span>
        )}

        <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => onAutoRefreshChange(e.target.checked)}
            className="rounded"
          />
          Auto-refresh
        </label>

        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-2 text-gray-500 hover:text-purple-600 hover:bg-purple-50 rounded-lg transition-all disabled:opacity-50"
          title="โหลดใหม่"
        >
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
        </button>

        <button
          onClick={onExportPdf}
          disabled={exporting}
          className="inline-flex items-center gap-1.5 px-3 py-2 bg-gradient-to-r from-purple-600 to-purple-700 text-white rounded-lg hover:from-purple-700 hover:to-purple-800 transition-all text-sm shadow-sm disabled:opacity-60"
        >
          {exporting ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Download size={14} />
          )}
          Export PDF
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function ExecutiveSummary({ data }: { data: Overview }) {
  const { kpis, source_distribution, answered_total } = data;
  const brainPct = answered_total
    ? Math.round((source_distribution.brain / answered_total) * 100)
    : 0;
  const ragPct = answered_total
    ? Math.round((source_distribution.rag / answered_total) * 100)
    : 0;
  const llmPct = answered_total
    ? Math.round((source_distribution.llm / answered_total) * 100)
    : 0;

  const insights: string[] = [];
  if (kpis.active_users.delta_pct !== null) {
    if (kpis.active_users.delta_pct > 0) {
      insights.push(
        `ผู้ใช้ active เพิ่มขึ้น ${kpis.active_users.delta_pct}% เทียบช่วงก่อนหน้า`,
      );
    } else if (kpis.active_users.delta_pct < 0) {
      insights.push(
        `ผู้ใช้ active ลดลง ${Math.abs(kpis.active_users.delta_pct)}%`,
      );
    }
  }
  if (kpis.cost_thb.delta_pct !== null && kpis.cost_thb.delta_pct < 0) {
    insights.push(
      `ค่าใช้จ่ายลดลง ${Math.abs(kpis.cost_thb.delta_pct)}% — brain ช่วยประหยัดได้`,
    );
  }
  if (data.pending_count > 5) {
    insights.push(
      `มี ${data.pending_count} คำถามที่ตอบไม่ได้รอ admin curate`,
    );
  }
  if (data.safety.blocked_total > 0) {
    insights.push(
      `Safety filter บล็อกความพยายามเข้าถึงข้อมูลละเอียดอ่อน ${data.safety.blocked_total} ครั้ง`,
    );
  }

  return (
    <div className="bg-gradient-to-br from-purple-50 via-white to-indigo-50 border border-purple-100 rounded-2xl p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-purple-700 flex items-center justify-center shadow-md">
          <Sparkles size={18} className="text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-800">
            ภาพรวม{data.range.human}
          </h3>
          <p className="text-sm text-gray-600 mt-1">
            ระบบตอบคำถาม {kpis.messages.value.toLocaleString()} ครั้ง โดย{" "}
            <span className="font-medium text-purple-700">
              {brainPct}% จาก AI Brain
            </span>
            {", "}
            <span className="font-medium text-sky-700">{ragPct}% จาก KB</span>
            {", "}
            และ <span className="font-medium text-slate-600">{llmPct}% จาก LLM</span>
          </p>
          {insights.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-gray-700">
              {insights.map((line, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Wand2
                    size={13}
                    className="text-purple-500 flex-shrink-0 mt-0.5"
                  />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function KpiCards({ kpis }: { kpis: Overview["kpis"] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <KpiCard
        icon={<Users size={18} />}
        label="ผู้ใช้ active"
        value={kpis.active_users.value}
        delta={kpis.active_users.delta_pct}
        color="purple"
      />
      <KpiCard
        icon={<MessageSquare size={18} />}
        label="แชทใหม่"
        value={kpis.new_sessions.value}
        delta={kpis.new_sessions.delta_pct}
        color="indigo"
      />
      <KpiCard
        icon={<Database size={18} />}
        label="ข้อความ"
        value={kpis.messages.value}
        delta={kpis.messages.delta_pct}
        color="sky"
      />
      <KpiCard
        icon={<DollarSign size={18} />}
        label="ค่าใช้จ่าย (฿)"
        value={kpis.cost_thb.value}
        delta={kpis.cost_thb.delta_pct}
        // For cost, going DOWN is good (inverse trend coloring).
        invertedTrend
        color="emerald"
        formatValue={(v) => v.toLocaleString("th-TH", { maximumFractionDigits: 2 })}
      />
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  delta,
  color,
  invertedTrend,
  formatValue,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  delta: number | null;
  color: "purple" | "indigo" | "sky" | "emerald";
  invertedTrend?: boolean;
  formatValue?: (v: number) => string;
}) {
  const tones: Record<typeof color, string> = {
    purple: "from-purple-50 to-purple-100 border-purple-200 text-purple-700",
    indigo: "from-indigo-50 to-indigo-100 border-indigo-200 text-indigo-700",
    sky: "from-sky-50 to-sky-100 border-sky-200 text-sky-700",
    emerald: "from-emerald-50 to-emerald-100 border-emerald-200 text-emerald-700",
  };

  // Trend health: by default up = good (green), down = bad (red). For cost we
  // invert so that "↓3%" reads as green.
  let trendBadge: React.ReactNode = (
    <span className="text-gray-400 text-xs">—</span>
  );
  if (delta !== null) {
    const isUp = delta > 0;
    const treatAsPositive = invertedTrend ? !isUp : isUp;
    const cls = treatAsPositive
      ? "bg-green-100 text-green-700"
      : "bg-red-100 text-red-700";
    const Arrow = isUp ? TrendingUp : TrendingDown;
    const pct = Math.abs(delta).toFixed(1);
    trendBadge = (
      <span
        className={`inline-flex items-center gap-0.5 text-[11px] font-medium px-1.5 py-0.5 rounded ${cls}`}
      >
        <Arrow size={11} />
        {pct}%
      </span>
    );
  }

  return (
    <div
      className={`bg-gradient-to-br ${tones[color]} border rounded-2xl p-4 shadow-sm`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm opacity-90">
          {icon}
          <span className="font-medium">{label}</span>
        </div>
        {trendBadge}
      </div>
      <p className="text-3xl font-bold mt-2 text-gray-900">
        {formatValue ? formatValue(value) : value.toLocaleString("th-TH")}
      </p>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function UsageTrendChart({ trend }: { trend: Overview["usage_trend"] }) {
  // Format the X-axis to show just the short Thai date (e.g. "5 มิ.ย.").
  const data = useMemo(
    () =>
      trend.map((p) => {
        const d = new Date(p.day + "T00:00:00Z");
        return {
          day: d.toLocaleDateString("th-TH", {
            day: "numeric",
            month: "short",
          }),
          messages: p.messages,
          sessions: p.sessions,
        };
      }),
    [trend],
  );

  return (
    <div className="lg:col-span-2 bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
          <TrendingUp size={16} className="text-purple-600" />
          การใช้งาน 7 วันล่าสุด
        </h3>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id="usagePurple" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.5} />
                <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="usageIndigo" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#4f46e5" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#4f46e5" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="day" stroke="#6b7280" fontSize={12} />
            <YAxis stroke="#6b7280" fontSize={12} allowDecimals={false} />
            <Tooltip
              contentStyle={{
                background: "white",
                border: "1px solid #e5e7eb",
                borderRadius: "8px",
              }}
              formatter={(value) =>
                typeof value === "number"
                  ? value.toLocaleString("th-TH")
                  : String(value ?? "")
              }
            />
            <Legend wrapperStyle={{ fontSize: "12px" }} />
            <Area
              type="monotone"
              dataKey="messages"
              name="ข้อความ"
              stroke="#7c3aed"
              fill="url(#usagePurple)"
              strokeWidth={2}
            />
            <Area
              type="monotone"
              dataKey="sessions"
              name="แชท"
              stroke="#4f46e5"
              fill="url(#usageIndigo)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function SourceDistributionChart({
  distribution,
}: {
  distribution: Overview["source_distribution"];
}) {
  const data = useMemo(
    () =>
      [
        { name: "🧠 Brain", value: distribution.brain, color: COLORS.brain },
        { name: "📚 KB", value: distribution.rag, color: COLORS.rag },
        { name: "🤖 LLM", value: distribution.llm, color: COLORS.llm },
        { name: "📎 ไฟล์", value: distribution.files, color: COLORS.files },
      ].filter((d) => d.value > 0),
    [distribution],
  );

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
        <Brain size={16} className="text-purple-600" />
        คำตอบมาจากไหน
      </h3>
      {data.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-8">ยังไม่มีข้อมูล</p>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={3}
              >
                {data.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "white",
                  border: "1px solid #e5e7eb",
                  borderRadius: "8px",
                }}
              />
              <Legend
                verticalAlign="bottom"
                wrapperStyle={{ fontSize: "12px" }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function TopQuestionsCard({ items }: { items: Overview["top_questions"] }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
        <MessageSquare size={16} className="text-purple-600" />
        คำถามยอดนิยม
      </h3>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-6">ยังไม่มีข้อมูล</p>
      ) : (
        <ol className="space-y-2">
          {items.map((q, i) => (
            <li
              key={i}
              className="flex items-start gap-3 text-sm"
              title={q.question}
            >
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-purple-100 text-purple-700 text-xs font-medium flex items-center justify-center">
                {i + 1}
              </span>
              <span className="flex-1 text-gray-700 truncate">
                {q.question}
              </span>
              <span className="flex-shrink-0 text-xs text-gray-400 font-medium">
                {q.count} ครั้ง
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function TopUsersCard({ items }: { items: Overview["top_users"] }) {
  const maxMessages = items.reduce((m, u) => Math.max(m, u.messages), 0) || 1;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
        <Users size={16} className="text-purple-600" />
        ผู้ใช้ Active สูงสุด
      </h3>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-6">ยังไม่มีข้อมูล</p>
      ) : (
        <ul className="space-y-2.5">
          {items.map((u, i) => {
            const pct = (u.messages / maxMessages) * 100;
            return (
              <li key={i}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="font-medium text-gray-700">{u.username}</span>
                  <span className="text-xs text-gray-500">
                    {u.messages} ข้อความ / {u.sessions} แชท
                  </span>
                </div>
                <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-purple-500 to-purple-600 rounded-full"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

// Pastel palette cycled across models. Lines up with the existing donut so
// the dashboard doesn't fight itself visually.
const MODEL_COLORS = [
  "#7c3aed", // purple — primary answer model
  "#0ea5e9", // sky
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ec4899", // pink
  "#94a3b8", // slate — fallback / legacy
];

function TranslationCostCard({ t }: { t?: Overview["translation"] }) {
  if (!t) return null;
  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
        <DollarSign size={16} className="text-violet-600" />
        ค่าใช้จ่ายการแปลเอกสาร
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl bg-violet-50 p-4">
          <p className="text-xs text-gray-500">ในช่วงที่เลือก</p>
          <p className="text-2xl font-bold text-violet-700">฿{t.cost_thb.toFixed(2)}</p>
          <p className="text-xs text-gray-400">
            {t.jobs} งาน · {t.pages} หน้า
          </p>
        </div>
        <div className="rounded-xl bg-gray-50 p-4">
          <p className="text-xs text-gray-500">รวมทั้งหมด</p>
          <p className="text-2xl font-bold text-gray-700">฿{t.cost_thb_all.toFixed(2)}</p>
          <p className="text-xs text-gray-400">
            {t.jobs_all} งาน · {t.pages_all} หน้า
          </p>
        </div>
      </div>
    </div>
  );
}


function CostBreakdownCard({ cost }: { cost: Overview["cost"] }) {
  const hasRealData = cost.rows_with_real_cost > 0;
  const hasLegacy = cost.rows_estimated > 0;
  const total = cost.real_usd + cost.estimated_usd;
  const realPct = total ? Math.round((cost.real_usd / total) * 100) : 0;

  // Pie data — real per-model rows, plus a legacy "ค่าประมาณ" slice when there
  // are still NULL-cost rows so the user can see the share that's a guess.
  const pieData = useMemo(() => {
    const real = cost.by_model.map((m, i) => ({
      name: m.model,
      value: m.cost_thb,
      color: MODEL_COLORS[i % MODEL_COLORS.length],
    }));
    if (cost.estimated_usd > 0) {
      real.push({
        name: "ค่าประมาณ (rows เก่า)",
        value: cost.estimated_usd * 36,
        color: "#cbd5e1",
      });
    }
    return real.filter((s) => s.value > 0);
  }, [cost]);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
          <DollarSign size={16} className="text-emerald-600" />
          ค่าใช้จ่ายตามโมเดล
        </h3>
        {hasLegacy && (
          <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
            <AlertTriangle size={11} />
            {cost.rows_estimated} rows ใช้ค่าประมาณ
          </span>
        )}
      </div>

      {pieData.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-8 italic">
          ยังไม่มีคำถามในช่วงนี้
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <div className="md:col-span-2 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={40}
                  outerRadius={75}
                  paddingAngle={3}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "white",
                    border: "1px solid #e5e7eb",
                    borderRadius: "8px",
                  }}
                  formatter={(value) =>
                    typeof value === "number"
                      ? `฿${value.toFixed(4)}`
                      : String(value ?? "")
                  }
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="md:col-span-3 flex flex-col">
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3">
                <p className="text-[11px] text-emerald-700 font-medium uppercase tracking-wide">
                  รวมทั้งสิ้น
                </p>
                <p className="text-2xl font-bold text-emerald-900">
                  ฿{cost.total_thb.toFixed(2)}
                </p>
                <p className="text-[11px] text-emerald-600 mt-0.5">
                  ${cost.total_usd.toFixed(4)}
                </p>
              </div>
              <div className="bg-purple-50 border border-purple-200 rounded-xl p-3">
                <p className="text-[11px] text-purple-700 font-medium uppercase tracking-wide">
                  ค่าจริงจาก tokens
                </p>
                <p className="text-2xl font-bold text-purple-900">
                  {realPct}%
                </p>
                <p className="text-[11px] text-purple-600 mt-0.5">
                  {cost.rows_with_real_cost} rows
                </p>
              </div>
            </div>

            <div className="overflow-y-auto max-h-32 -mx-1 px-1">
              <ul className="space-y-1">
                {cost.by_model.map((m, i) => (
                  <li
                    key={m.model}
                    className="flex items-center justify-between text-sm py-1 border-b border-gray-50 last:border-0"
                  >
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-sm"
                        style={{
                          background:
                            MODEL_COLORS[i % MODEL_COLORS.length],
                        }}
                      />
                      <span className="font-medium text-gray-700">
                        {m.model}
                      </span>
                      <span className="text-xs text-gray-400">
                        ({m.rows} ครั้ง)
                      </span>
                    </span>
                    <span className="font-mono text-gray-700">
                      ฿{m.cost_thb.toFixed(4)}
                    </span>
                  </li>
                ))}
                {hasLegacy && (
                  <li className="flex items-center justify-between text-sm py-1 italic text-gray-500">
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="inline-block w-2.5 h-2.5 rounded-sm"
                        style={{ background: "#cbd5e1" }}
                      />
                      <span>ค่าประมาณ (flat-rate)</span>
                      <span className="text-xs text-gray-400">
                        ({cost.rows_estimated} ครั้ง)
                      </span>
                    </span>
                    <span className="font-mono">
                      ฿{(cost.estimated_usd * 36).toFixed(4)}
                    </span>
                  </li>
                )}
              </ul>
            </div>

            {!hasRealData && hasLegacy && (
              <p className="text-xs text-amber-700 mt-2 italic">
                ⚠️ ยังไม่มี row ใหม่ที่มี token data — รอคำถามใหม่หลัง deploy
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SatisfactionCard({ feedback }: { feedback: Overview["feedback"] }) {
  const { up, down, total, satisfaction_pct } = feedback;

  // Visual tone tracks the satisfaction band. >=80% green, 60-79 amber, <60 red,
  // unrated grey. Keeps it readable at-a-glance for execs scanning the page.
  let tone = "from-gray-50 to-gray-100 border-gray-200 text-gray-700";
  if (satisfaction_pct !== null) {
    if (satisfaction_pct >= 80) {
      tone = "from-green-50 to-emerald-100 border-emerald-200 text-emerald-700";
    } else if (satisfaction_pct >= 60) {
      tone = "from-amber-50 to-amber-100 border-amber-200 text-amber-700";
    } else {
      tone = "from-rose-50 to-rose-100 border-rose-200 text-rose-700";
    }
  }

  return (
    <div
      className={`bg-gradient-to-br ${tone} border rounded-2xl p-5 shadow-sm flex flex-col`}
    >
      <h3 className="font-semibold flex items-center gap-2 text-sm opacity-90">
        <ThumbsUp size={16} />
        ความพึงพอใจ
      </h3>
      {total === 0 ? (
        <p className="text-sm opacity-70 mt-4 italic">
          ยังไม่มีการให้ feedback
        </p>
      ) : (
        <>
          <p className="text-4xl font-bold mt-3">
            {satisfaction_pct !== null ? `${satisfaction_pct}%` : "—"}
          </p>
          <p className="text-xs opacity-80 mt-1">satisfaction rate</p>
          <div className="mt-auto pt-4 flex items-center gap-4 text-sm">
            <span className="inline-flex items-center gap-1">
              <ThumbsUp size={14} className="text-green-600" />
              <strong>{up}</strong>
              <span className="opacity-70">ถูกใจ</span>
            </span>
            <span className="inline-flex items-center gap-1">
              <ThumbsDown size={14} className="text-red-500" />
              <strong>{down}</strong>
              <span className="opacity-70">ไม่ถูกใจ</span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function RecentDownvotesCard({
  items,
  className = "",
}: {
  items: Overview["recent_downvotes"];
  className?: string;
}) {
  return (
    <div
      className={`bg-white rounded-2xl border border-gray-100 p-5 shadow-sm ${className}`}
    >
      <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
        <ThumbsDown size={16} className="text-rose-500" />
        คำตอบที่โดน 👎 ล่าสุด
        <span className="text-xs font-normal text-gray-400 ml-1">
          (admin ควรปรับ)
        </span>
      </h3>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-6 italic">
          ✓ ยังไม่มีคำตอบที่ถูก downvote
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((d) => (
            <li
              key={d.message_id}
              className="border-l-2 border-rose-300 pl-3 py-1"
            >
              <p
                className="text-sm font-medium text-gray-800 line-clamp-1"
                title={d.question}
              >
                {d.question}
              </p>
              {d.reason && (
                <p className="text-xs text-rose-600 mt-0.5">
                  เหตุผล: {d.reason}
                </p>
              )}
              <p className="text-[11px] text-gray-400 mt-0.5">
                โดย {d.username}
                {d.created_at && (
                  <>
                    {" · "}
                    {new Date(d.created_at).toLocaleString("th-TH", {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SafetyCard({
  safety,
  pendingCount,
}: {
  safety: Overview["safety"];
  pendingCount: number;
}) {
  const categories = Object.entries(safety.blocked_by_category)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
        <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
          <Shield size={16} className="text-red-500" />
          Safety
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <SafetyStat
            label="Blocked"
            value={safety.blocked_total}
            color="red"
          />
          <SafetyStat
            label="Failed login"
            value={safety.failed_logins}
            color="amber"
          />
          <SafetyStat
            label="Disabled"
            value={safety.login_blocked_disabled}
            color="gray"
          />
        </div>
        {categories.length > 0 && (
          <div className="mt-4">
            <p className="text-xs text-gray-500 mb-2 font-medium">
              ประเภทที่ blocked มากสุด
            </p>
            <div className="flex flex-wrap gap-1.5">
              {categories.map(([cat, count]) => (
                <span
                  key={cat}
                  className="inline-flex items-center gap-1 text-xs bg-red-50 border border-red-200 text-red-700 px-2 py-0.5 rounded"
                >
                  {cat}
                  <span className="text-red-500 font-medium">×{count}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-2xl border border-gray-100 p-5 shadow-sm">
        <h3 className="font-semibold text-gray-800 flex items-center gap-2 mb-3">
          <AlertTriangle size={16} className="text-amber-500" />
          ที่ต้องปรับปรุง
        </h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-gray-700">
                คำถามที่ตอบไม่ได้ (Pending)
              </p>
              <p className="text-xs text-gray-500">
                Admin ควรเพิ่มคำตอบใน Knowledge Base
              </p>
            </div>
            <span
              className={`text-2xl font-bold ${
                pendingCount > 5 ? "text-amber-600" : "text-gray-700"
              }`}
            >
              {pendingCount}
            </span>
          </div>
          {pendingCount === 0 && (
            <p className="text-sm text-green-600 italic">
              ✓ ทุกคำถามมีคำตอบ — KB ครอบคลุมดี!
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function SafetyStat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "red" | "amber" | "gray";
}) {
  const tones: Record<typeof color, string> = {
    red: "bg-red-50 text-red-700 border-red-200",
    amber: "bg-amber-50 text-amber-700 border-amber-200",
    gray: "bg-gray-50 text-gray-700 border-gray-200",
  };
  return (
    <div className={`${tones[color]} border rounded-xl p-2.5 text-center`}>
      <p className="text-xl font-bold">{value}</p>
      <p className="text-[10px] uppercase tracking-wide opacity-80">{label}</p>
    </div>
  );
}
