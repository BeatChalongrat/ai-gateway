"use client";

// The parts a message is made of, once: the person's bubble and its
// collapse, the answer's body, the action buttons under a message, the
// failed-turn card, the quiet note, the time. MessageBubble assembles them
// for a chat message; an agent's workspace assembles the same parts for its
// thread (AM5) — so a message looks and behaves the same wherever it is
// shown, and a change to one is a change to both.

import { forwardRef, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from "react";
import { Copy } from "lucide-react";

import { toast } from "@/components/ui/Toast";
import { copyTextToClipboard } from "@/lib/clipboard";
import { useLocale, useT } from "@/lib/i18n";
import { splitTerminalBlocks } from "@/lib/terminalText";

// ─────────────────────────── the person's message ───────────────────────────

/** The person's side of a turn: attachments above the text (matches Claude /
 *  ChatGPT, so the file they sent reads first), the bubble, then what hangs
 *  under it (actions, the branch navigator).
 *
 *  No entrance animation on the user's own row: the text has just left the
 *  composer and is being scrolled to the anchor at the same moment, and a
 *  bubble fading in from transparent while it moves reads as "vanished, then
 *  reappeared somewhere else" (measured frame by frame in FIX47.5). */
export function UserMessageRow({
  attachments,
  children,
  below,
  ...rest
}: { attachments?: ReactNode; children?: ReactNode; below?: ReactNode } & Omit<HTMLAttributes<HTMLDivElement>, "children">) {
  return (
    <div className="group flex w-full justify-end" {...rest}>
      <div className="flex max-w-[85%] flex-col items-end gap-1.5">
        {attachments}
        {children}
        {below}
      </div>
    </div>
  );
}

/** The bubble itself. FIX107 A1 — max-w-full: a line with nowhere to break
 *  (a terminal table's border) used to set the bubble's width and push it
 *  past the column and off the screen. */
export function UserTextPill({ children }: { children: ReactNode }) {
  return (
    <div
      className="max-w-full rounded-bubble bg-bubble-user px-4 py-2.5 text-[15px] leading-relaxed text-text-primary shadow-sm"
      style={{ borderRadius: "20px" }}
    >
      {children}
    </div>
  );
}

// P2 — long USER message collapse. A user message taller than
// USER_COLLAPSE_THRESHOLD_LINES rendered line-boxes starts collapsed to
// USER_COLLAPSE_VISIBLE_LINES with a Show more / Show less toggle; at or
// under the threshold it renders exactly as before, with no toggle.
// Decided from the RENDERED height (scrollHeight vs. line-height), never
// from "\n" count — Thai text wraps, so one logical line can occupy many
// line-boxes. Assistant replies never collapse (this component is only
// reachable from the user branch), and edit mode replaces it with the
// full-text textarea. Display-only: copy/export read message.content
// from the store, untouched by the cap.
const USER_COLLAPSE_THRESHOLD_LINES = 12;
// USER_COLLAPSE_VISIBLE_LINES = 8 lives in globals.css (.user-msg-text.is-collapsed)

export function CollapsibleUserText({ content }: { content: string }) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [canCollapse, setCanCollapse] = useState(false);
  const textRef = useRef<HTMLSpanElement>(null);

  // Measure before paint so a long message never flashes full-height,
  // then re-measure on reflow (window/sidebar width changes) via
  // ResizeObserver — no polling. scrollHeight reports the full content
  // height even while the CSS cap is applied, so one measurement path
  // serves both states.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    const measure = () => {
      const lineHeight = parseFloat(window.getComputedStyle(el).lineHeight);
      if (!Number.isFinite(lineHeight) || lineHeight <= 0) return;
      // Half-line tolerance: an exactly-threshold-height message must
      // NOT collapse, and integer scrollHeight rounding must not flip it.
      setCanCollapse(
        el.scrollHeight >
          lineHeight * USER_COLLAPSE_THRESHOLD_LINES + lineHeight / 2,
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [content]);

  const collapsed = canCollapse && !expanded;
  const segments = splitTerminalBlocks(content);
  return (
    <>
      <span
        ref={textRef}
        className={`user-msg-text whitespace-pre-wrap break-words${
          collapsed ? " is-collapsed" : ""
        }`}
      >
        {segments.length === 1 && segments[0]!.kind === "text"
          ? content
          : segments.map((s, i) =>
              s.kind === "terminal" ? (
                <span key={i} className="user-msg-terminal" data-testid="user-msg-terminal">
                  {s.text}
                </span>
              ) : (
                <span key={i}>{s.text}</span>
              ),
            )}
      </span>
      {canCollapse ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={(e) => {
            const btn = e.currentTarget;
            const willCollapse = expanded;
            setExpanded(!expanded);
            // Collapsing a very long message pulls the toggle up — keep
            // it (and the message tail the user was at) in view with the
            // minimal scroll. Never fired on expand, so reading position
            // is preserved there.
            if (willCollapse) {
              requestAnimationFrame(() =>
                btn.scrollIntoView?.({ block: "nearest" }),
              );
            }
          }}
          className="user-msg-toggle focus-ring"
        >
          {expanded ? t("msg.showLess") : t("msg.showMore")}
        </button>
      ) : null}
    </>
  );
}

// ───────────────────────────── the answer ─────────────────────────────

/** The answer's body: plain prose in the column (no bubble, no avatar). */
export const AssistantProse = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function AssistantProse(
  { children, ...rest },
  ref,
) {
  return (
    <div ref={ref} className="prose-chat relative w-full text-text-primary" {...rest}>
      {children}
    </div>
  );
});

// ───────────────────────── actions under a message ─────────────────────────

export function ActionButton({
  ariaLabel,
  onClick,
  children,
  tone = "muted",
}: {
  ariaLabel: string;
  onClick: () => void;
  children: React.ReactNode;
  tone?: "muted" | "success";
}) {
  const colorClass =
    tone === "success"
      ? "text-status-success"
      : "text-text-muted hover:text-text-secondary";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      title={ariaLabel}
      className={`focus-ring tap-44 rounded p-1.5 transition-colors hover:bg-bg-elevated ${colorClass}`}
    >
      {children}
    </button>
  );
}

// Icon set — Lucide-style 18px / stroke-1.5 stroke icons. All four
// share weight + cap so the assistant action row reads as one row
// of related controls instead of a grab-bag of paths.
export function CopyIcon() {
  return (
    <svg
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </svg>
  );
}
export function CheckIcon() {
  return (
    <svg
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

/** FIX47.6 Part D — no silent failure: the copy either shows the ✓ state or
 *  says why it didn't. The old empty catch hid every failure; in the desktop
 *  shell writeText is refused (permission-denied) and copy did nothing at
 *  all, measured live. The shared helper also runs the execCommand fallback
 *  on refusal (not only on absence) and honours its boolean. */
export function useCopyText(text: string): { copied: boolean; copy: () => Promise<void> } {
  const t = useT();
  const [copied, setCopied] = useState(false);
  async function copy() {
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } else {
      toast(t("msg.copyFailed"));
    }
  }
  return { copied, copy };
}

/** Copy, as the button under a message. */
export function CopyAction({ copied, onCopy }: { copied: boolean; onCopy: () => void }) {
  const t = useT();
  return (
    <ActionButton
      ariaLabel={copied ? t("a11y.copied") : t("a11y.copyMessage")}
      onClick={onCopy}
      tone={copied ? "success" : "muted"}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </ActionButton>
  );
}

// Phase 7 — per-message timestamp. The data was always on the message
// (created_at) but never rendered, so a long conversation gave no sense
// of when anything happened.
//
// Compact by design: time-only for today ("14:32"), date + time once
// it's older, with the FULL localized timestamp on hover/screen-reader
// via <time title>. Rendered in the browser's local zone (Asia/Bangkok
// for these users) because toLocaleTimeString resolves the host zone —
// the server stores UTC.
export function formatMessageTime(
  createdAt: string | undefined,
  locale: string,
  now: Date = new Date(),
): { short: string; full: string } | null {
  if (!createdAt) return null;
  const d = new Date(createdAt);
  if (Number.isNaN(d.getTime())) return null;
  const tag = locale === "th" ? "th-TH" : "en-US";
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString(tag, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const short = sameDay
    ? time
    : `${d.toLocaleDateString(tag, { day: "numeric", month: "short" })} ${time}`;
  return { short, full: d.toLocaleString(tag) };
}

export function MessageTime({ createdAt }: { createdAt?: string }) {
  const locale = useLocale();
  const parts = formatMessageTime(createdAt, locale);
  if (!parts) return null;
  return (
    <time
      dateTime={createdAt}
      title={parts.full}
      className="ml-2 text-[11px] text-text-muted"
      data-testid="message-time"
    >
      {parts.short}
    </time>
  );
}

// ───────────────────────── when a turn did not work ─────────────────────────

export function WarningIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="mt-0.5 shrink-0"
      aria-hidden="true"
    >
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

// B1 — terminal error notice. Red-tinted card distinct from normal
// message text so a failed turn reads unambiguously as an error: what
// happened, then the ways forward (each an ErrorCardAction), then the
// reference a person can quote to support, then whatever detail the
// surface has (the chat: what was tried).
export function ErrorCard({
  message,
  actions,
  referenceId,
  detail,
}: {
  message: ReactNode;
  actions?: ReactNode;
  /** FIX119 — the run id a person can quote to support; copyable. */
  referenceId?: string;
  detail?: ReactNode;
}) {
  const t = useT();
  return (
    <div
      role="alert"
      className="mt-1 rounded-lg border border-status-danger/30 bg-status-danger/10 px-3 py-2 text-sm text-status-danger"
    >
      <div className="flex items-start gap-2">
        <WarningIcon />
        <span className="leading-snug">{message}</span>
      </div>
      {actions}
      {referenceId ? (
        <div className="mt-1.5 flex max-w-full items-center gap-1.5 pl-6 text-[11px] text-status-danger/80" data-testid="error-reference">
          <span>{t("error.referenceId")}</span>
          <code className="min-w-0 select-all break-all font-mono">{referenceId}</code>
          <button
            type="button"
            aria-label={t("error.copyReference")}
            title={t("error.copyReference")}
            className="inline-flex shrink-0 items-center rounded p-0.5 hover:bg-status-danger/10"
            onClick={async () => {
              const ok = await copyTextToClipboard(referenceId);
              toast(t(ok ? "error.referenceCopied" : "toast.copyFailed"));
            }}
          >
            <Copy size={12} aria-hidden="true" />
          </button>
        </div>
      ) : null}
      {detail}
    </div>
  );
}

/** One way forward on a failed turn: an icon and a sentence, underlined. */
export function ErrorCardAction({
  icon,
  children,
  onClick,
  disabled,
  testId,
}: {
  icon: ReactNode;
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled}
      className={`mt-2 inline-flex max-w-full items-center gap-2 text-left underline${disabled === undefined ? "" : " disabled:opacity-50"}`}
      onClick={onClick}
    >
      {icon}
      <span className="min-w-0 break-words">{children}</span>
    </button>
  );
}

/** A state that is not a failure, said quietly under the message: waiting
 *  on the link or the service (neutral), or something the person should
 *  know before they rely on the answer (warning). */
export function NoteCard({
  tone = "neutral",
  role = "status",
  children,
  className,
  ...rest
}: { tone?: "neutral" | "warning"; role?: string; children: ReactNode } & Omit<HTMLAttributes<HTMLDivElement>, "role" | "children">) {
  const look =
    tone === "warning"
      ? "border-status-warning/30 bg-status-warning/10"
      : "border-border bg-bg-surface";
  return (
    <div role={role} className={`mt-1 ${className ? `${className} ` : ""}rounded-lg border ${look} px-3 py-2 text-sm text-text-secondary`} {...rest}>
      {children}
    </div>
  );
}
