"use client";

// Small shared pieces of the Agent Market screens. Everything here uses the
// app's theme tokens only (tailwind.config.js), so light and dark follow the
// app's data-theme switch.
//
// AM6 — buttons, the spinner and the dialog are the app's (components/ui:
// Button and ButtonLink, Spinner, Modal), imported where they are used. What
// is left here is what the app has no component for.

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
  Receipt,
  Table2,
  TriangleAlert,
} from "lucide-react";

import { Button, ButtonLink } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useThemeStore } from "@/stores/themeStore";

import { useAmT } from "./i18n";
import type { Look, Manifest } from "./types";
import { useAgentMarket } from "./useAgentMarket";

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** AM5 wrapped the app's own components (the composer, a message, a file
 *  card, the model picker) so that they read in Thai inside Agent Market.
 *  AM11: Agent Market follows the app's language, and so do they — this sets
 *  no language any more. The name and the shape stay, so no caller changes. */
export function Thai({ children }: { children: ReactNode }) {
  return <>{children}</>;
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

// AM10 — the icon ships inside the agent package (docs/agent-market/ARCHITECTURE.md,
// "The icon"): the file is a shape, and the client paints it with the agent's
// accent for the theme in use, so every agent is one visual language with its
// own colour, at 20 px in the sidebar and at 48 px on its page alike. The runner
// checked the markup before it registered it; this is the client's own look at
// it before the markup goes into a style — a mask image can run nothing, but
// nothing unchecked goes in anyway.
const ICON_MAX_BYTES = 4096;

export function safeIconSVG(svg?: string | null): string | null {
  if (!svg) return null;
  const s = svg.trim();
  if (s.length > ICON_MAX_BYTES || !s.startsWith("<svg") || !s.endsWith("</svg>")) return null;
  if (/<\s*(script|style|foreignObject|image|use|a|text|iframe)\b|[\s"']on[a-z]+\s*=|href\s*=|url\(|javascript:|<!/i.test(s)) return null;
  return s;
}

const HEX = /^#[0-9a-f]{6}$/i;

/** The look of an agent, from whatever carries it (a rail row, an install row, a manifest). */
export function lookOf(...sources: Array<Look | Manifest | null | undefined>): Look {
  const out: Look = {};
  for (const s of sources) {
    if (!s) continue;
    const svg = safeIconSVG(s.icon_svg);
    if (!out.icon_svg && svg) out.icon_svg = svg;
    if (!out.accent && s.accent && HEX.test(s.accent.light) && HEX.test(s.accent.dark)) out.accent = s.accent;
  }
  return out;
}

/** The agent's colour for the theme in use (null when the agent has none). */
export function useAccent(look?: Look | null): string | null {
  const theme = useThemeStore((s) => s.theme);
  const a = look?.accent;
  if (!a || !HEX.test(a.light) || !HEX.test(a.dark)) return null;
  return theme === "light" ? a.light : a.dark;
}

/** The agent's identity tile: the package's own icon in its colour on a tint of
 *  it; the glyph on a solid tile for an agent that ships none; a quiet outlined
 *  one for a placeholder. */
export function AgentTile({ icon, look, size = 28, quiet = false }: { icon?: string; look?: Look | null; size?: number; quiet?: boolean }) {
  const radius = size >= 40 ? "rounded-xl" : "rounded-lg";
  const accent = useAccent(look);
  const svg = quiet ? null : safeIconSVG(look?.icon_svg);
  if (svg && accent) {
    const mask = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    const glyph = Math.round(size * 0.58);
    return (
      <span
        aria-hidden
        data-testid="am-tile"
        data-accent={accent}
        style={{ width: size, height: size, backgroundColor: `${accent}24` }}
        className={cls("inline-flex shrink-0 items-center justify-center", radius)}
      >
        <span
          style={{
            width: glyph,
            height: glyph,
            backgroundColor: accent,
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

/** A small dot of the agent's colour, for lists that have no room for a tile. */
export function AccentDot({ look, size = 8 }: { look?: Look | null; size?: number }) {
  const accent = useAccent(look);
  if (!accent) return null;
  return (
    <span aria-hidden data-testid="am-accent-dot" style={{ width: size, height: size, backgroundColor: accent }} className="inline-block shrink-0 rounded-full" />
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
        <ButtonLink href="/chat" variant="secondary" className="mt-6">
          {t("gate.backToChat")}
        </ButtonLink>
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
  return <Thai>{children}</Thai>;
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
        <Button type="button" variant="secondary" size="sm" className="shrink-0" onClick={onRetry}>
          {t("common.retry")}
        </Button>
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
