"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import PageHeader from "../components/ui/PageHeader";
import { api } from "../lib/api";

/* ── Types ───────────────────────────────────────────────────────── */

type Period = "24h" | "7d" | "30d";
type Direction = "up" | "down" | "flat";

interface KpiMetric {
  value: number;
  delta_pct: number | null;
  direction: Direction;
}

interface Overview {
  period: Period;
  requests: KpiMetric;
  success_rate: KpiMetric;
  avg_latency_ms: KpiMetric;
  spend_usd: KpiMetric;
  resources: { tenants: number; projects: number; models: number };
  requests_series: number[];
  success_series: number[];
  latency_series: number[];
  spend_series: number[];
}

interface VolumeBucket {
  label: string;
  openai: number;
  anthropic: number;
  google: number;
  other: number;
}
interface VolumeResp {
  period: Period;
  granularity: "hour" | "day";
  buckets: VolumeBucket[];
  providers: string[];
}

interface TopModel {
  model: string;
  provider: string;
  count: number;
}
interface TopModelsResp {
  period: Period;
  total: number;
  items: TopModel[];
}

interface RecentRequest {
  request_id: string;
  status: string;
  http_class: "2xx" | "4xx" | "5xx" | "unknown";
  // Derived HTTP status code from backend (see admin_dashboard.py). Null
  // when the internal status label doesn't map cleanly — display as "—".
  http_status: number | null;
  provider: string;
  model: string;
  latency_ms: number;
  error_code: string | null;
  created_at: string | null;
}

/* ── Constants ───────────────────────────────────────────────────── */

// Provider brand hex stays hardcoded — recharts fills don't resolve CSS vars.
const PROVIDER_COLOR: Record<string, string> = {
  openai: "#6366f1",
  anthropic: "#d97757",
  google: "#4285f4",
  other: "#64718a",
};

const PROVIDER_LABEL: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  other: "Others",
};

// Top-model bar gradients — CSS vars don't work inside linear-gradient() values
// in every browser so we keep the hex pairs here per provider family.
const PROVIDER_GRADIENT: Record<string, string> = {
  openai: "linear-gradient(90deg, #6366F1, #8B5CF6)",
  anthropic: "linear-gradient(90deg, #F59E0B, #F87171)",
  google: "linear-gradient(90deg, #06B6D4, #6366F1)",
  other: "linear-gradient(90deg, #64718A, #4B5673)",
};
const PROVIDER_GLOW: Record<string, string> = {
  openai: "rgba(99,102,241,0.45)",
  anthropic: "rgba(245,158,11,0.45)",
  google: "rgba(6,182,212,0.45)",
  other: "rgba(100,113,138,0.3)",
};

/* ── Helpers ─────────────────────────────────────────────────────── */

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return "just now";
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(0)}k`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/* ── Counter animation (initial mount only) ─────────────────────── */

function useAnimatedNumber(target: number, duration = 800): number {
  const [val, setVal] = useState(0);
  const hasStartedRef = useRef(false);
  useEffect(() => {
    if (hasStartedRef.current) {
      setVal(target);
      return;
    }
    if (target === 0) {
      setVal(0);
      return;
    }
    hasStartedRef.current = true;
    const start = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(target * eased);
      if (t < 1) raf = requestAnimationFrame(step);
      else setVal(target);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return val;
}

/* ── Icons ───────────────────────────────────────────────────────── */

function LightningIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}
function CheckIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}
function ClockIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}
function DollarIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6" />
    </svg>
  );
}

/* ── Sparkline (SVG, absolute-anchored to card bottom) ──────────── */

function Sparkline({ series, color, gradId }: { series: number[]; color: string; gradId: string }) {
  if (!series || series.length < 2) return null;
  const max = Math.max(...series);
  const min = Math.min(...series);
  const range = max - min;
  const w = 100;
  const h = 40;
  const step = w / (series.length - 1);

  const allFlat = range === 0;
  const pts = series.map((v, i) => {
    const x = i * step;
    const y = allFlat ? h * 0.7 : h - ((v - min) / range) * h;
    return `${x},${y}`;
  });
  const linePath = `M ${pts.join(" L ")}`;
  const areaPath = `${linePath} L ${w},${h} L 0,${h} Z`;
  return (
    <svg
      data-sparkline={gradId}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{
        display: "block",
        width: "100%",
        height: "100%",
        opacity: allFlat ? 0.18 : 0.4,
        pointerEvents: "none",
      }}
      aria-hidden
    >
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth="1.3"
        strokeDasharray={allFlat ? "3 3" : undefined}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/* ── Delta chip ──────────────────────────────────────────────────── */

function DeltaChip({
  delta,
  direction,
  compareLabel,
  invertDeltaColor,
}: {
  delta: number | null;
  direction: Direction;
  compareLabel: string;
  invertDeltaColor?: boolean;
}) {
  if (delta === null) {
    return <span style={{ fontSize: 11, color: "var(--text-muted)" }}>No prior data</span>;
  }

  const good = invertDeltaColor ? direction === "down" : direction === "up";
  const bad = invertDeltaColor ? direction === "up" : direction === "down";
  const flat = direction === "flat";
  const color = flat
    ? "var(--text-tertiary)"
    : good
    ? "var(--accent-success)"
    : bad
    ? "var(--accent-danger)"
    : "var(--text-tertiary)";
  const bg = flat
    ? "rgba(100,116,139,0.10)"
    : good
    ? "rgba(16,185,129,0.14)"
    : bad
    ? "rgba(248,113,113,0.14)"
    : "rgba(100,116,139,0.10)";
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "—";

  return (
    <>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 2,
          background: bg,
          color,
          padding: "1px 5px",
          borderRadius: 3,
          fontSize: 10.5,
          fontWeight: 600,
          marginRight: 6,
        }}
      >
        <span style={{ fontSize: 8 }}>{arrow}</span>
        {direction === "flat" ? "—" : `${Math.abs(delta).toFixed(1)}%`}
      </span>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{compareLabel}</span>
    </>
  );
}

/* ── KPI Card ────────────────────────────────────────────────────── */

interface KpiProps {
  label: string;
  delta: number | null;
  direction: Direction;
  compareLabel: string;
  accent: string;
  accentRgb: string;
  icon: React.ReactNode;
  series: number[];
  sparkId: string;
  rawValue: number;
  formatValue: (n: number) => string;
  invertDeltaColor?: boolean;
  animationDelay?: number;
}

function KpiCard({
  label,
  delta,
  direction,
  compareLabel,
  accent,
  accentRgb,
  icon,
  series,
  sparkId,
  rawValue,
  formatValue,
  invertDeltaColor,
  animationDelay = 0,
}: KpiProps) {
  const animated = useAnimatedNumber(rawValue);

  return (
    <div
      className="dash-kpi"
      style={{
        background: "linear-gradient(180deg, var(--bg-surface) 0%, var(--bg-surface-2) 100%)",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: "13px 15px",
        position: "relative",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        transition: "transform 200ms ease, box-shadow 200ms ease",
        animation: `fadeInUp 0.5s ease-out ${animationDelay}s both`,
      }}
    >
      {/* Top accent line — sits above card background, outside the flex flow. */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 1.5,
          background: `linear-gradient(90deg, transparent, rgba(${accentRgb}, 0.65), transparent)`,
          zIndex: 2,
          pointerEvents: "none",
        }}
      />

      {/* Header row: label left, icon box right. */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 600,
            color: "var(--text-tertiary)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          {label}
        </span>
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: 6,
            background: `rgba(${accentRgb}, 0.14)`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: accent,
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
      </div>

      {/* Number + delta row — baseline-aligned so the "▲ 4.2%" sits on the
          digit baseline rather than under the number. */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 7, flexWrap: "wrap" }}>
        <span
          style={{
            fontSize: 23,
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.02em",
            lineHeight: 1.1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {formatValue(animated)}
        </span>
        <DeltaChip
          delta={delta}
          direction={direction}
          compareLabel={compareLabel}
          invertDeltaColor={invertDeltaColor}
        />
      </div>

      {/* Sparkline pinned to the bottom of the card via margin-top: auto so
          it never overlaps the number/delta row, regardless of card height. */}
      <div style={{ marginTop: "auto", width: "100%", height: 20 }}>
        <Sparkline series={series} color={accent} gradId={sparkId} />
      </div>
    </div>
  );
}

/* ── Period selector (segmented control) ────────────────────────── */

function PeriodSelector({ period, onChange }: { period: Period; onChange: (p: Period) => void }) {
  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        background: "var(--bg-elevated)",
        borderRadius: 8,
        padding: 3,
        gap: 2,
        height: "fit-content",
      }}
    >
      {(["24h", "7d", "30d"] as Period[]).map((p) => {
        const active = period === p;
        return (
          <button
            key={p}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(p)}
            style={{
              background: active ? "var(--bg-surface)" : "transparent",
              color: active ? "var(--text-primary)" : "var(--text-tertiary)",
              border: "none",
              borderRadius: 6,
              padding: "4px 12px",
              fontSize: 11.5,
              fontWeight: active ? 600 : 500,
              cursor: "pointer",
              boxShadow: active ? "0 1px 2px rgba(15,23,42,0.08)" : "none",
              transition: "background 120ms, color 120ms, box-shadow 120ms",
            }}
          >
            {p}
          </button>
        );
      })}
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function DashboardPage() {
  const [period, setPeriod] = useState<Period>("24h");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [volume, setVolume] = useState<VolumeResp | null>(null);
  const [topModels, setTopModels] = useState<TopModelsResp | null>(null);
  const [recent, setRecent] = useState<RecentRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number>(Date.now());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.get(`/admin/dashboard/overview?period=${period}`),
      api.get(`/admin/dashboard/volume?period=${period}`),
      api.get(`/admin/dashboard/top-models?period=${period}&limit=5`),
    ])
      .then(([o, v, t]) => {
        if (cancelled) return;
        setOverview(o);
        setVolume(v);
        setTopModels(t);
        setLastUpdated(Date.now());
      })
      .catch(() => {
        /* handled per-section */
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  const loadRecent = useCallback(async () => {
    try {
      const r = await api.get(`/admin/dashboard/recent-requests?limit=10`);
      setRecent(r.items || []);
    } catch {
      /* keep previous data */
    }
  }, []);

  useEffect(() => {
    loadRecent();
    const id = setInterval(loadRecent, 5000);
    return () => clearInterval(id);
  }, [loadRecent]);

  const compareLabel = useMemo(() => {
    if (period === "24h") return "vs. prior 24h";
    if (period === "7d") return "vs. prior week";
    return "vs. prior 30d";
  }, [period]);

  const updatedAgo = Math.max(0, Math.floor((Date.now() - lastUpdated) / 1000));

  return (
    <div className="dashboard-root">
      <PageStyles />

      <PageHeader
        title="Dashboard"
        subtitle={`Platform overview · updated ${updatedAgo}s ago`}
        actions={<PeriodSelector period={period} onChange={setPeriod} />}
      />

      {/* Row 1: 4 KPI cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 10,
          marginBottom: 10,
          marginTop: 18,
        }}
      >
        <KpiCard
          label="Requests"
          rawValue={overview?.requests.value ?? 0}
          formatValue={(n) => (overview ? fmt(n) : "—")}
          delta={overview?.requests.delta_pct ?? null}
          direction={overview?.requests.direction ?? "flat"}
          compareLabel={compareLabel}
          accent="#6366f1"
          accentRgb="99,102,241"
          icon={<LightningIcon color="#818cf8" />}
          series={overview?.requests_series ?? []}
          sparkId="spk-requests"
          animationDelay={0.05}
        />
        <KpiCard
          label="Success rate"
          rawValue={overview?.success_rate.value ?? 0}
          formatValue={(n) => (overview ? `${n.toFixed(1)}%` : "—")}
          delta={overview?.success_rate.delta_pct ?? null}
          direction={overview?.success_rate.direction ?? "flat"}
          compareLabel={compareLabel}
          accent="#10b981"
          accentRgb="16,185,129"
          icon={<CheckIcon color="#34d399" />}
          series={overview?.success_series ?? []}
          sparkId="spk-success"
          animationDelay={0.10}
        />
        <KpiCard
          label="Avg latency"
          rawValue={overview?.avg_latency_ms.value ?? 0}
          formatValue={(n) => (overview ? `${Math.round(n)}ms` : "—")}
          delta={overview?.avg_latency_ms.delta_pct ?? null}
          direction={overview?.avg_latency_ms.direction ?? "flat"}
          compareLabel={compareLabel}
          accent="#f59e0b"
          accentRgb="245,158,11"
          icon={<ClockIcon color="#fbbf24" />}
          series={overview?.latency_series ?? []}
          sparkId="spk-latency"
          invertDeltaColor
          animationDelay={0.15}
        />
        <KpiCard
          label="Spend"
          rawValue={overview?.spend_usd.value ?? 0}
          formatValue={(n) => (overview ? `$${n.toFixed(2)}` : "—")}
          delta={overview?.spend_usd.delta_pct ?? null}
          direction={overview?.spend_usd.direction ?? "flat"}
          compareLabel={compareLabel}
          accent="#a855f7"
          accentRgb="168,85,247"
          icon={<DollarIcon color="#c084fc" />}
          series={overview?.spend_series ?? []}
          sparkId="spk-spend"
          invertDeltaColor
          animationDelay={0.20}
        />
      </div>

      {/* Row 2: Volume chart + Top models */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.7fr 1fr",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <VolumeChart data={volume} loading={loading} />
        <TopModelsList data={topModels} loading={loading} />
      </div>

      {/* Row 3: Recent requests full width */}
      <RecentRequests items={recent} />
    </div>
  );
}

/* ── Volume chart ────────────────────────────────────────────────── */

function VolumeChart({ data, loading }: { data: VolumeResp | null; loading: boolean }) {
  const buckets = data?.buckets ?? [];
  const total = useMemo(
    () => buckets.reduce((acc, b) => acc + b.openai + b.anthropic + b.google + b.other, 0),
    [buckets]
  );
  const tickInterval = buckets.length > 0 ? Math.floor((buckets.length - 1) / 2) : 0;

  return (
    <section
      style={{
        background: "linear-gradient(180deg, var(--bg-surface) 0%, var(--bg-surface-2) 100%)",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: "14px 16px",
        animation: "fadeInUp 0.5s ease-out 0.25s both",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)" }}>Request volume</h3>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {total.toLocaleString()} requests total · by provider
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {["openai", "anthropic", "google", "other"].map((p) => (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: PROVIDER_COLOR[p], display: "inline-block" }} />
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{PROVIDER_LABEL[p]}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ width: "100%", height: 220, marginTop: 8 }}>
        {loading && !data ? (
          <div style={{ display: "grid", placeItems: "center", height: "100%", color: "var(--text-muted)", fontSize: 12 }}>
            Loading…
          </div>
        ) : total === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--text-muted)",
              fontSize: 12,
            }}
          >
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ marginBottom: 8, opacity: 0.5 }}
            >
              <path d="M3 3v18h18" />
              <path d="M7 14l4-4 4 4 5-5" />
            </svg>
            <div style={{ fontWeight: 500, color: "var(--text-tertiary)" }}>No requests yet</div>
            <div style={{ fontSize: 11, marginTop: 2 }}>
              Send a request to <code>/v1/chat/completions</code> to see data
            </div>
          </div>
        ) : (
          <ResponsiveContainer>
            <BarChart data={buckets} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                tickLine={false}
                axisLine={{ stroke: "var(--border-default)" }}
                interval={tickInterval || "preserveStartEnd"}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: "rgba(99,102,241,0.08)" }}
                contentStyle={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-default)",
                  borderRadius: 6,
                  fontSize: 12,
                  padding: "6px 10px",
                  color: "var(--text-primary)",
                }}
                labelStyle={{ color: "var(--text-primary)", fontWeight: 600 }}
              />
              <Bar
                dataKey="openai"
                stackId="a"
                fill={PROVIDER_COLOR.openai}
                name="OpenAI"
                isAnimationActive
                animationDuration={600}
                animationBegin={400}
              />
              <Bar
                dataKey="anthropic"
                stackId="a"
                fill={PROVIDER_COLOR.anthropic}
                name="Anthropic"
                isAnimationActive
                animationDuration={600}
                animationBegin={450}
              />
              <Bar
                dataKey="google"
                stackId="a"
                fill={PROVIDER_COLOR.google}
                name="Google"
                isAnimationActive
                animationDuration={600}
                animationBegin={500}
              />
              <Bar
                dataKey="other"
                stackId="a"
                fill={PROVIDER_COLOR.other}
                name="Others"
                radius={[3, 3, 0, 0]}
                isAnimationActive
                animationDuration={600}
                animationBegin={550}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

/* ── Top models list ─────────────────────────────────────────────── */

function TopModelsList({ data, loading }: { data: TopModelsResp | null; loading: boolean }) {
  const items = data?.items ?? [];
  const max = items.length ? items[0].count : 1;
  return (
    <section
      style={{
        background: "linear-gradient(180deg, var(--bg-surface) 0%, var(--bg-surface-2) 100%)",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: "14px 16px",
        animation: "fadeInUp 0.5s ease-out 0.3s both",
      }}
    >
      <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)" }}>Top models</h3>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, marginBottom: 10 }}>By request count</div>

      {loading && !data ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading…</div>
      ) : items.length === 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px 8px",
            color: "var(--text-muted)",
            fontSize: 12,
          }}
        >
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ marginBottom: 8, opacity: 0.5 }}
          >
            <rect x="3" y="10" width="4" height="10" rx="1" />
            <rect x="10" y="6" width="4" height="14" rx="1" />
            <rect x="17" y="13" width="4" height="7" rx="1" />
          </svg>
          <div style={{ fontWeight: 500, color: "var(--text-tertiary)" }}>No models yet</div>
          <div style={{ fontSize: 11, marginTop: 2 }}>No requests in this period.</div>
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {items.map((m, i) => {
            const gradient = PROVIDER_GRADIENT[m.provider] ?? PROVIDER_GRADIENT.other;
            const glow = PROVIDER_GLOW[m.provider] ?? PROVIDER_GLOW.other;
            const pct = Math.max(4, (m.count / max) * 100);
            return (
              <li
                key={`${m.provider}:${m.model}`}
                style={{ animation: `fadeInRight 0.4s ease-out ${0.4 + i * 0.04}s both` }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: 4,
                    gap: 8,
                  }}
                >
                  <span
                    style={{
                      fontFamily: "ui-monospace, SFMono-Regular, 'JetBrains Mono', monospace",
                      fontSize: 11.5,
                      color: "var(--text-primary)",
                      fontWeight: 500,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      minWidth: 0,
                    }}
                    title={m.model}
                  >
                    {m.model}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
                    {m.count.toLocaleString()}
                  </span>
                </div>
                <div style={{ height: 4, background: "var(--bg-elevated)", borderRadius: 999, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${pct}%`,
                      height: "100%",
                      background: gradient,
                      boxShadow: `0 0 8px ${glow}`,
                      animation: `growWidth 0.8s ease-out ${0.55 + i * 0.04}s both`,
                      transformOrigin: "left",
                    }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/* ── Recent requests ─────────────────────────────────────────────── */

function RecentRequests({ items }: { items: RecentRequest[] }) {
  const seenRef = useRef<Set<string> | null>(null);
  const mountedAtRef = useRef<number>(Date.now());
  const [filter, setFilter] = useState("");

  const annotatedItems = useMemo(() => {
    if (seenRef.current === null) {
      seenRef.current = new Set(items.map((i) => i.request_id));
      return items.map((r, i) => ({ r, isNew: false, index: i }));
    }
    const seen = seenRef.current;
    const out = items.map((r, i) => {
      const isNew = !seen.has(r.request_id);
      if (isNew) seen.add(r.request_id);
      return { r, isNew, index: i };
    });
    return out;
  }, [items]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return annotatedItems;
    const q = filter.toLowerCase();
    return annotatedItems.filter(
      ({ r }) =>
        (r.model ?? "").toLowerCase().includes(q) ||
        (r.provider ?? "").toLowerCase().includes(q) ||
        (r.status ?? "").toLowerCase().includes(q)
    );
  }, [annotatedItems, filter]);

  const initialMount = Date.now() - mountedAtRef.current < 50;

  return (
    <section
      style={{
        background: "linear-gradient(180deg, var(--bg-surface) 0%, var(--bg-surface-2) 100%)",
        border: "1px solid var(--border-default)",
        borderRadius: 10,
        padding: "14px 16px",
        animation: "fadeInUp 0.5s ease-out 0.35s both",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)" }}>Recent requests</h3>
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: "var(--accent-success)",
                background: "rgba(16,185,129,0.14)",
                border: "1px solid rgba(16,185,129,0.25)",
                borderRadius: 999,
                padding: "1px 7px",
                letterSpacing: "0.04em",
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <span
                className="live-dot"
                style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent-success)" }}
              />
              LIVE
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            Last {items.length} · auto-refresh 5s
          </div>
        </div>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by model, provider, status…"
          style={{
            height: 28,
            padding: "0 10px",
            fontSize: 11.5,
            background: "var(--bg-base)",
            border: "1px solid var(--border-default)",
            borderRadius: 6,
            color: "var(--text-primary)",
            width: 240,
            outline: "none",
          }}
        />
      </div>

      {filtered.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
          {filter ? "No matching requests." : "No recent requests."}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr
                style={{
                  textAlign: "left",
                  color: "var(--text-muted)",
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                <th style={{ padding: "6px 8px 6px 0", fontWeight: 500 }}>Status</th>
                <th style={{ padding: "6px 8px", fontWeight: 500 }}>Provider</th>
                <th style={{ padding: "6px 8px", fontWeight: 500 }}>Model</th>
                <th style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>Latency</th>
                <th style={{ padding: "6px 0 6px 8px", fontWeight: 500, textAlign: "right" }}>When</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(({ r, isNew, index }) => {
                // Range-based colouring off the derived http_status code.
                // Falls back to neutral grey when the code is null (the
                // backend couldn't map the internal status label).
                const code = r.http_status;
                const tier: "success" | "info" | "danger" | "critical" | "neutral" =
                  code == null
                    ? "neutral"
                    : code >= 500
                    ? "critical"
                    : code >= 400
                    ? "danger"
                    : code >= 300
                    ? "info"
                    : code >= 200
                    ? "success"
                    : "neutral";

                const rowBg =
                  tier === "danger" || tier === "critical"
                    ? "rgba(248,113,113,0.05)"
                    : "transparent";
                const statusColor =
                  tier === "success"
                    ? "var(--accent-success)"
                    : tier === "info"
                    ? "var(--accent-cyan)"
                    : tier === "danger"
                    ? "var(--accent-warning)"
                    : tier === "critical"
                    ? "var(--accent-danger)"
                    : "var(--text-tertiary)";
                const statusBg =
                  tier === "success"
                    ? "rgba(16,185,129,0.14)"
                    : tier === "info"
                    ? "rgba(6,182,212,0.14)"
                    : tier === "danger"
                    ? "rgba(245,158,11,0.14)"
                    : tier === "critical"
                    ? "rgba(248,113,113,0.18)"
                    : "rgba(100,116,139,0.12)";
                const statusBorder =
                  tier === "success"
                    ? "rgba(16,185,129,0.25)"
                    : tier === "info"
                    ? "rgba(6,182,212,0.25)"
                    : tier === "danger"
                    ? "rgba(245,158,11,0.25)"
                    : tier === "critical"
                    ? "rgba(248,113,113,0.30)"
                    : "rgba(100,116,139,0.18)";

                const delay = initialMount ? 0.45 + Math.min(index, 10) * 0.02 : 0;
                const animation = isNew
                  ? "slideInRow 0.35s ease-out both"
                  : `slideInRow 0.35s ease-out ${delay}s both`;

                return (
                  <tr
                    key={r.request_id}
                    style={{
                      background: rowBg,
                      borderTop: "1px solid var(--border-subtle)",
                      animation,
                    }}
                  >
                    <td style={{ padding: "7px 8px 7px 0", minWidth: 60 }}>
                      <span
                        style={{
                          color: statusColor,
                          fontSize: 10.5,
                          fontWeight: 600,
                          background: statusBg,
                          border: `1px solid ${statusBorder}`,
                          borderRadius: 4,
                          padding: "2px 6px",
                          letterSpacing: "0.02em",
                          fontVariantNumeric: "tabular-nums",
                        }}
                        title={r.status || undefined}
                      >
                        {r.http_status ?? "—"}
                      </span>
                    </td>
                    <td style={{ padding: "7px 8px", color: "var(--text-tertiary)", textTransform: "capitalize" }}>
                      {r.provider || "—"}
                    </td>
                    <td
                      style={{
                        padding: "7px 8px",
                        color: "var(--text-primary)",
                        fontFamily: "ui-monospace, SFMono-Regular, 'JetBrains Mono', monospace",
                        fontSize: 11,
                      }}
                      title={r.model}
                    >
                      {r.model || "—"}
                    </td>
                    <td
                      style={{
                        padding: "7px 8px",
                        color: "var(--text-primary)",
                        textAlign: "right",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {r.latency_ms ? `${r.latency_ms}ms` : "—"}
                    </td>
                    <td
                      style={{
                        padding: "7px 0 7px 8px",
                        color: "var(--text-muted)",
                        textAlign: "right",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {timeAgo(r.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ── Page-level styles (keyframes + hover + reduced-motion) ─────── */

function PageStyles() {
  return (
    <style jsx global>{`
      .dashboard-root .dash-kpi:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px -8px rgba(15, 23, 42, 0.18);
      }

      .dashboard-root .live-dot {
        animation: pulseDot 1.5s ease-in-out infinite;
      }

      @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @keyframes fadeInRight {
        from { opacity: 0; transform: translateX(-10px); }
        to { opacity: 1; transform: translateX(0); }
      }
      @keyframes growWidth {
        from { transform: scaleX(0); }
        to { transform: scaleX(1); }
      }
      @keyframes pulseDot {
        0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }
        50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
      }
      @keyframes slideInRow {
        from { opacity: 0; transform: translateX(-6px); }
        to { opacity: 1; transform: translateX(0); }
      }

      @media (prefers-reduced-motion: reduce) {
        .dashboard-root,
        .dashboard-root *,
        .dashboard-root *::before,
        .dashboard-root *::after {
          animation: none !important;
          transition: none !important;
        }
      }
    `}</style>
  );
}
