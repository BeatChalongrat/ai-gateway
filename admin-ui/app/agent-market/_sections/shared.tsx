"use client";

// What the Agent Market sections share: the organisation row loader, number
// and date formatting, the one-line note an action leaves, the reason form
// (opening someone else's run, removing a run's files early), a run's
// outcome in words, and a step's name in words.
//
// Every refusal is shown as chat-edge wrote it (amErrorMessage): the server's
// sentence says what to do and that nothing changed.

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";

import Modal from "../../../components/ui/Modal";
import { Icon, type IconName } from "../../../components/ui/Icon";
import { useT, type DictKey } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type AdminOrg, type AgentLook, type Pricing } from "../../../lib/agentMarketApi";
import { useTheme } from "../../../components/ThemeProvider";

export type Translate = ReturnType<typeof useT>;

/** The platform's rates when the server sends none (3,000,000 credits = 1 USD). */
export const DEFAULT_PRICING: Pricing = { credits_per_usd: 3_000_000, thb_per_usd: 35 };

export const DASH = "—";

// ── numbers and dates ────────────────────────────────────────────────────

const creditsFmt = new Intl.NumberFormat("th-TH", { maximumFractionDigits: 2 });
const bahtFmt = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function formatCredits(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? creditsFmt.format(v) : "0";
}

/** baht = credits / credits_per_usd × thb_per_usd */
export function creditsToBaht(credits: number | null | undefined, p: Pricing): number | null {
  if (credits === null || credits === undefined || !Number.isFinite(credits) || !(p.credits_per_usd > 0)) return null;
  return (credits / p.credits_per_usd) * p.thb_per_usd;
}

export function formatBaht(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  if (v > 0 && v < 0.01) return "< 0.01";
  return bahtFmt.format(v);
}

export const bahtOf = (credits: number | null | undefined, p: Pricing) => formatBaht(creditsToBaht(credits, p));

/** "1,000" as a person types it → "1000" (the server reads a plain number). */
export const plainNumber = (s: string) => s.replace(/[,\s]/g, "");

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return new Intl.DateTimeFormat(locale === "th" ? "th-TH" : "en-GB", { dateStyle: "medium", timeStyle: "short" }).format(d);
}

export function formatBytes(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return DASH;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// ── the organisation row ─────────────────────────────────────────────────

export function useOrg(): { org: AdminOrg | null; error: unknown; reload: () => void } {
  const [org, setOrg] = useState<AdminOrg | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    amAdmin.org().then(
      (r) => {
        if (!alive) return;
        setOrg(r);
        setError(null);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { org, error, reload };
}

// ── small pieces ─────────────────────────────────────────────────────────

export const muted: CSSProperties = { fontSize: "0.75rem", color: "var(--text-muted)", margin: 0 };
export const secondary: CSSProperties = { fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 };
export const h2: CSSProperties = { fontSize: "0.9375rem", fontWeight: 600, color: "var(--text-primary)", margin: 0 };
export const linkStyle: CSSProperties = { color: "var(--accent-primary)", textDecoration: "underline", textUnderlineOffset: 2 };
export const code: CSSProperties = {
  fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
  fontSize: "0.72rem",
  background: "var(--bg-elevated)",
  borderRadius: 4,
  padding: "0.05rem 0.3rem",
  wordBreak: "break-all",
};

/** A warning band that reads in both themes. */
export const warnBand: CSSProperties = {
  border: "1px solid color-mix(in srgb, var(--accent-warning) 45%, transparent)",
  background: "color-mix(in srgb, var(--accent-warning) 10%, transparent)",
  color: "var(--text-primary)",
  borderRadius: "var(--radius-md)",
  padding: "0.6rem 0.8rem",
  fontSize: "0.8125rem",
  margin: 0,
};

export type Msg = { text: string; ok: boolean } | null;

/** The line an action leaves: the server's sentence on a refusal. */
export function Note({ msg, testId = "am-note" }: { msg: Msg; testId?: string }) {
  if (!msg) return null;
  return (
    <span
      role={msg.ok ? "status" : "alert"}
      data-testid={testId}
      style={{ fontSize: "0.75rem", color: msg.ok ? "var(--accent-success)" : "var(--accent-danger)" }}
    >
      {msg.text}
    </span>
  );
}

/** A sentence with "{link}" in it, the link put in that place. */
export function withLink(sentence: string, link: ReactNode): ReactNode {
  const at = sentence.indexOf("{link}");
  if (at < 0) return sentence;
  return (
    <>
      {sentence.slice(0, at)}
      {link}
      {sentence.slice(at + "{link}".length)}
    </>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label style={{ display: "block", minWidth: 0 }}>
      <span className="sran-label">{label}</span>
      {children}
      {hint ? <span style={{ ...muted, display: "block", marginTop: "0.25rem" }}>{hint}</span> : null}
    </label>
  );
}

export function Loading() {
  const t = useT();
  return (
    <p role="status" style={secondary}>
      {t("state.loading")}
    </p>
  );
}

/** A load that failed: the server's sentence, and a retry. */
export function LoadError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const t = useT();
  return (
    <div role="alert" className="sran-card" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.75rem" }}>
      <span style={{ ...secondary, color: "var(--accent-danger)" }}>{amErrorMessage(error, t("state.errorLoad"))}</span>
      {onRetry ? (
        <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onRetry}>
          {t("action.retry")}
        </button>
      ) : null}
    </div>
  );
}

const AGENT_ICONS: Record<string, IconName> = { bank: "building", expense: "dollar", invoice: "inbox", chart: "box", po: "inbox" };

// AM10 — the icon ships inside the agent package: a shape the client paints
// with the agent's accent for the theme in use (the same paint as the chat
// app's tile), so an agent looks the same here as in the chat. The runner
// checked the markup; this is the admin app's own look before it goes into a
// style.
const ICON_MAX_BYTES = 4096;
const HEX = /^#[0-9a-f]{6}$/i;

export function safeIconSVG(svg?: string | null): string | null {
  if (!svg) return null;
  const s = svg.trim();
  if (s.length > ICON_MAX_BYTES || !s.startsWith("<svg") || !s.endsWith("</svg>")) return null;
  if (/<\s*(script|style|foreignObject|image|use|a|text|iframe)\b|[\s"']on[a-z]+\s*=|href\s*=|url\(|javascript:|<!/i.test(s)) return null;
  return s;
}

export function useAccent(look?: AgentLook | null): string | null {
  const { theme } = useTheme();
  const a = look?.accent;
  if (!a || !HEX.test(a.light) || !HEX.test(a.dark)) return null;
  return theme === "light" ? a.light : a.dark;
}

export function AgentTile({ icon, look, quiet, size = 32 }: { icon?: string; look?: AgentLook | null; quiet?: boolean; size?: number }) {
  const accent = useAccent(look);
  const svg = quiet ? null : safeIconSVG(look?.icon_svg);
  const box = { width: size, height: size, flexShrink: 0, display: "inline-flex", alignItems: "center", justifyContent: "center", borderRadius: size >= 40 ? 12 : 8 } as const;
  if (svg && accent) {
    const mask = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    const glyph = Math.round(size * 0.58);
    return (
      <span aria-hidden data-testid="am-tile" data-accent={accent} style={{ ...box, background: `${accent}24` }}>
        <span
          style={{
            width: glyph,
            height: glyph,
            background: accent,
            WebkitMaskImage: mask,
            maskImage: mask,
            WebkitMaskRepeat: "no-repeat",
            maskRepeat: "no-repeat",
            WebkitMaskSize: "contain",
            maskSize: "contain",
            WebkitMaskPosition: "center",
            maskPosition: "center",
          }}
        />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      data-testid="am-tile"
      style={{
        ...box,
        border: "1px solid var(--border-default)",
        background: quiet ? "var(--bg-elevated)" : "color-mix(in srgb, var(--accent-primary) 14%, transparent)",
        color: quiet ? "var(--text-muted)" : "var(--accent-primary)",
      }}
    >
      <Icon name={(icon && AGENT_ICONS[icon]) || "box"} size={Math.round(size / 2)} />
    </span>
  );
}

/** The looks of every agent, by slug — read once from the catalogue, for lists that name an agent. */
export function useLooks(): Record<string, AgentLook> {
  const [looks, setLooks] = useState<Record<string, AgentLook>>({});
  useEffect(() => {
    let alive = true;
    // a list without the catalogue (a section on its own) simply shows no tiles
    Promise.resolve()
      .then(() => amAdmin.catalog())
      .then((r) => {
        if (!alive) return;
        const out: Record<string, AgentLook> = {};
        for (const a of r.agents ?? []) out[a.slug] = { icon_svg: a.manifest?.icon_svg, accent: a.manifest?.accent };
        setLooks(out);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return looks;
}

/** An agent's name with its tile, for a table cell. */
export function AgentName({ name, look }: { name: string; look?: AgentLook | null }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
      <AgentTile look={look} size={22} />
      <span>{name}</span>
    </span>
  );
}

// ── a run's outcome and steps in words ───────────────────────────────────

/** AM6: a run is named by what it came to (a round that finished open is no failure). */
export function outcomeOf(run: { outcome?: string | null; status?: string | null }): string {
  if (run.outcome) return run.outcome;
  return run.status === "succeeded" ? "completed" : (run.status ?? "");
}

const OUTCOME_KEYS: Record<string, DictKey> = {
  queued: "am.status.queued",
  running: "am.status.running",
  awaiting_input: "am.status.awaiting_input",
  completed: "am.status.succeeded",
  succeeded: "am.status.succeeded",
  failed: "am.status.failed",
  cancelled: "am.status.cancelled",
};

/** The words for an outcome; `reason` is the run's reason code (never shown itself). */
export function outcomeLabel(outcome: string, reason: string | null | undefined, t: Translate): string {
  if (outcome === "completed_open") return t(reason === "not_tied" ? "am.outcome.open.notTied" : "am.outcome.open.gate");
  const key = OUTCOME_KEYS[outcome];
  return key ? t(key) : outcome || DASH;
}

export type BadgeVariant = "success" | "warning" | "error" | "info" | "neutral";

export function outcomeVariant(outcome: string): BadgeVariant {
  switch (outcome) {
    case "completed":
    case "succeeded":
      return "success";
    case "completed_open":
    case "awaiting_input":
      return "warning";
    case "failed":
      return "error";
    case "running":
    case "queued":
      return "info";
    default:
      return "neutral";
  }
}

export const FINISHED = new Set(["succeeded", "failed", "cancelled"]);

export function stepLabel(name: string, t: Translate): string {
  if (name.startsWith("parse:")) return t("am.step.parse", { file: name.slice(6) });
  if (name.startsWith("preview:")) return t("am.step.preview", { file: name.slice(8) });
  if (name.startsWith("clarify:")) {
    if (name.includes("postcompute")) return t("am.step.clarify.postcompute");
    if (name.includes("preflight")) return t("am.step.clarify.preflight");
    return t("am.step.clarify.other");
  }
  const known: Record<string, DictKey> = {
    preflight: "am.step.preflight",
    compute: "am.step.compute",
    postcompute: "am.step.postcompute",
    narrative: "am.step.narrative",
    render: "am.step.render",
    publish: "am.step.publish",
    gate: "am.step.gate",
    stopped: "am.step.stopped",
  };
  return known[name] ? t(known[name]) : name;
}

// ── the reason form ──────────────────────────────────────────────────────

export const REASON_MIN = 8;

/** A reason (≥ 8 characters) for an action the run's owner is told about. */
export function ReasonForm({
  confirm,
  danger = false,
  onCancel,
  onConfirm,
}: {
  confirm: string;
  danger?: boolean;
  onCancel?: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const t = useT();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    const r = reason.trim();
    if (r.length < REASON_MIN) {
      setError(t("am.runs.reasonShort"));
      return;
    }
    setBusy(true);
    setError(null);
    onConfirm(r).then(
      () => {
        setBusy(false);
        setReason("");
      },
      (err) => {
        setBusy(false);
        setError(amErrorMessage(err, t("am.failed")));
      },
    );
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) submit();
      }}
      style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}
    >
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={3}
        maxLength={500}
        aria-label={t("field.reason")}
        placeholder={t("am.runs.reasonPlaceholder")}
        className="sran-input"
        style={{ resize: "vertical" }}
        data-testid="am-reason-text"
      />
      <span style={muted}>{t("am.runs.reasonHint")}</span>
      {error ? (
        <span role="alert" style={{ fontSize: "0.75rem", color: "var(--accent-danger)" }} data-testid="am-reason-error">
          {error}
        </span>
      ) : null}
      <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
        {onCancel ? (
          <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onCancel} disabled={busy}>
            {t("action.cancel")}
          </button>
        ) : null}
        <button
          type="submit"
          className={`sran-btn sran-btn-sm ${danger ? "sran-btn-danger" : "sran-btn-primary"}`}
          disabled={busy}
          data-testid="am-reason-go"
        >
          {busy ? t("ui.modal.processing") : confirm}
        </button>
      </div>
    </form>
  );
}

export function ReasonDialog({
  open,
  title,
  body,
  confirm,
  danger,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  confirm: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <p style={{ ...secondary, lineHeight: 1.6, marginBottom: "0.9rem" }}>{body}</p>
      <ReasonForm confirm={confirm} danger={danger} onCancel={onClose} onConfirm={onConfirm} />
    </Modal>
  );
}
