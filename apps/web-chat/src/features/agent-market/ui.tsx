"use client";

// Small shared pieces of the Agent Market screens. Everything here uses the
// app's theme tokens only (tailwind.config.js), so light and dark follow the
// app's data-theme switch.

import Link from "next/link";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  ArrowLeftRight,
  Bot,
  CalendarClock,
  ChartLine,
  Check,
  ClipboardCheck,
  FolderOpen,
  LoaderCircle,
  Receipt,
  Table2,
  TriangleAlert,
} from "lucide-react";

import { Skeleton } from "@/components/ui/Skeleton";

import { useAmT } from "./i18n";
import { useAgentMarket } from "./useAgentMarket";

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Middle dot separator, kept out of JSX text. */
export const DOT = "·";

// ── agent icon tile ─────────────────────────────────────────────────────

const ICONS: Record<string, typeof Bot> = {
  chart: ChartLine,
  invoice: Receipt,
  receipt: Receipt,
  bank: ArrowLeftRight,
  expense: ClipboardCheck,
  po: Table2,
  ap: CalendarClock,
  folder: FolderOpen,
};

export function AgentGlyph({ icon, size = 16 }: { icon?: string; size?: number }) {
  const Icon = (icon && ICONS[icon]) || Bot;
  return <Icon width={size} height={size} strokeWidth={2} aria-hidden />;
}

/** The agent's identity tile: solid foreground tile for a real agent, a quiet
 *  one for a placeholder. */
export function AgentTile({ icon, size = 28, quiet = false }: { icon?: string; size?: number; quiet?: boolean }) {
  const radius = size >= 40 ? "rounded-xl" : "rounded-lg";
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className={cls(
        "inline-flex shrink-0 items-center justify-center",
        radius,
        quiet ? "border border-border bg-bg-elevated text-text-muted" : "bg-text-primary text-bg-base",
      )}
    >
      <AgentGlyph icon={icon} size={Math.round(size * 0.55)} />
    </span>
  );
}

// ── page chrome ─────────────────────────────────────────────────────────

/** A scrolling page inside the chat layout's main area. */
export function PageShell({ children, width = "max-w-5xl" }: { children: ReactNode; width?: string }) {
  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto bg-bg-base" data-testid="am-page">
      <div className={cls("mx-auto w-full flex-1 px-4 pb-16 pt-4 md:px-6 md:pt-6", width)}>{children}</div>
    </div>
  );
}

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="focus-ring inline-flex items-center gap-1.5 rounded-md py-1 text-sm text-text-muted transition-colors hover:text-text-primary"
    >
      <ArrowLeft width={15} height={15} aria-hidden />
      <span>{children}</span>
    </Link>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cls("rounded-xl border border-border bg-bg-surface px-4 py-4", className)}>{children}</section>;
}

export function Spin({ size = 14 }: { size?: number }) {
  return <LoaderCircle width={size} height={size} className="shrink-0 animate-spin" aria-hidden />;
}

// ── states ──────────────────────────────────────────────────────────────

export function PageSkeleton() {
  const t = useAmT();
  return (
    <PageShell>
      <div role="status" aria-live="polite" className="flex flex-col gap-4 pt-4">
        <span className="sr-only">{t("common.loading")}</span>
        <Skeleton className="h-7 w-48 rounded-md" />
        <Skeleton className="h-4 w-80 max-w-full rounded-md" />
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      </div>
    </PageShell>
  );
}

export function NotAvailable() {
  const t = useAmT();
  return (
    <PageShell width="max-w-xl">
      <div className="mt-16 flex flex-col items-center text-center" data-testid="am-not-available">
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-bg-surface text-text-muted">
          <Bot width={22} height={22} aria-hidden />
        </span>
        <h1 className="mt-4 text-lg font-semibold text-text-primary">{t("gate.title")}</h1>
        <p className="mt-1.5 text-sm text-text-secondary">{t("gate.body")}</p>
        <Link
          href="/chat"
          className="focus-ring mt-6 inline-flex items-center rounded-lg border border-border px-3.5 py-2 text-sm text-text-primary hover:bg-bg-elevated"
        >
          {t("gate.backToChat")}
        </Link>
      </div>
    </PageShell>
  );
}

/** Wrap every Agent Market page: skeleton while unknown, a calm "not
 *  available" state when the flag is off or the account is not allowed. */
export function FeatureGate({ children }: { children: ReactNode }) {
  const { state } = useAgentMarket();
  if (state === "unknown") return <PageSkeleton />;
  if (state !== "available") return <NotAvailable />;
  return <>{children}</>;
}

export function ErrorPanel({ message, onRetry, title }: { message: string; onRetry?: () => void; title?: string }) {
  const t = useAmT();
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-status-danger/40 bg-status-danger/10 px-4 py-3 text-sm text-text-primary">
      <TriangleAlert width={18} height={18} className="mt-0.5 shrink-0 text-status-danger" aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <div className="font-medium">{title}</div> : null}
        <div className={title ? "mt-0.5 text-text-secondary" : undefined}>{message}</div>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="focus-ring shrink-0 rounded-md border border-border bg-bg-surface px-2.5 py-1 text-xs font-medium text-text-primary hover:bg-bg-elevated"
        >
          {t("common.retry")}
        </button>
      ) : null}
    </div>
  );
}

// ── small pieces ────────────────────────────────────────────────────────

export type Tone = "neutral" | "success" | "warning" | "danger";

const PILL_TONE: Record<Tone, string> = {
  neutral: "border-border bg-bg-elevated text-text-secondary",
  success: "border-status-success/40 bg-status-success/10 text-status-success",
  warning: "border-status-warning/40 bg-status-warning/10 text-status-warning",
  danger: "border-status-danger/40 bg-status-danger/10 text-status-danger",
};

export function Pill({ tone = "neutral", size = "sm", children }: { tone?: Tone; size?: "sm" | "md"; children: ReactNode }) {
  return (
    <span
      className={cls(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border font-medium",
        size === "md" ? "px-2.5 py-1 text-xs" : "px-2 py-0.5 text-[11px]",
        PILL_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" }) {
  return (
    <span
      className={cls(
        "inline-flex items-center rounded-md px-2 py-1 text-[11px]",
        tone === "success" ? "bg-status-success/10 text-status-success" : "bg-bg-elevated text-text-secondary",
      )}
    >
      {children}
    </span>
  );
}

export function CheckLine({ children, ok = true }: { children: ReactNode; ok?: boolean }) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className={cls("mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center", ok ? "text-status-success" : "text-status-danger")} aria-hidden>
        {ok ? (
          <Check width={15} height={15} strokeWidth={2.5} />
        ) : (
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
        )}
      </span>
      <span className="min-w-0 flex-1 text-text-secondary">{children}</span>
    </li>
  );
}

/** Primary / secondary link-buttons in the app's idiom (inverted primary). */
export const BTN_PRIMARY =
  "focus-ring inline-flex items-center justify-center gap-2 rounded-lg bg-text-primary px-3.5 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_SECONDARY =
  "focus-ring inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-transparent px-3.5 py-2 text-sm text-text-primary transition-colors hover:bg-bg-elevated disabled:cursor-not-allowed disabled:opacity-50";
export const BTN_PRIMARY_SMALL =
  "focus-ring inline-flex items-center justify-center gap-1.5 rounded-lg bg-text-primary px-2.5 py-1.5 text-xs font-medium text-bg-base transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40";
export const BTN_DANGER_OUTLINE =
  "focus-ring inline-flex items-center justify-center gap-2 rounded-lg border border-status-danger/40 bg-transparent px-3.5 py-2 text-sm text-status-danger transition-colors hover:bg-status-danger/10 disabled:cursor-not-allowed disabled:opacity-50";
export const BTN_SMALL =
  "focus-ring inline-flex items-center justify-center gap-1.5 rounded-lg border border-border bg-transparent px-2.5 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated disabled:cursor-not-allowed disabled:opacity-50";
