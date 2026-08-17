"use client";

// AI messages render plain (no bubble, no avatar, no model label) so
// the conversation reads like a clean document. The action bar
// (copy / thumbs-up / thumbs-down / regenerate) appears on hover.
// The active model is shown only in the composer's model selector,
// never per-message. User messages stay as a soft gray pill aligned
// to the right.
//
// Markdown (incl. LaTeX via remark-math + rehype-katex, Phase 12) is
// rendered through the shared <Markdown> component — plugins, component
// overrides, citation pills, and the KaTeX stylesheet all live in
// Markdown.tsx so this file only decides WHEN to render markdown, not
// HOW. (The old TODO deferred KaTeX over stylesheet bundle weight; the
// shared component imports the CSS in its own module so only markdown-
// rendering routes pay for it — see Markdown.tsx for the rationale.)

import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import type { FallbackAttempt, Message } from "@/lib/types";
import { splitMarkdownBlocks } from "@/lib/markdownBlocks";
import { useI18nStore, useT } from "@/lib/i18n";
import { fallbackNotice } from "@/lib/errorMessages";
import { useVoiceOutput } from "@/hooks/useVoiceOutput";
import { useModelStore } from "@/stores/modelStore";
import { useVoiceStore } from "@/stores/voiceStore";

import { BranchNavigator } from "./BranchNavigator";
import { CatIndicator } from "./CatIndicator";
import { DocPlanCard } from "./DocPlanCard";
import type { DocPlan } from "./DocPlanCard";
import { ExcelPlanCard } from "./ExcelPlanCard";
import type { ExcelPlan } from "./ExcelPlanCard";
import { FeedbackButtons } from "./FeedbackButtons";
import { ImageGeneratingPlaceholder } from "./ImageGeneratingPlaceholder";
import { CanvasSourceProvider } from "@/components/canvas/CanvasSourceContext";

import { CodeRunCard } from "./CodeRunCard";
import { Markdown } from "./Markdown";
import { VideoGeneratingPlaceholder } from "./VideoGeneratingPlaceholder";
import { SearchCitations } from "./SearchCitations";
import { SearchingIndicator } from "./SearchingIndicator";
import { SourcesCard } from "./SourcesCard";
import { MessageAttachments } from "./MessageAttachments";
import { PptxPlanCard } from "./PptxPlanCard";
import type { PptxPlan } from "./PptxPlanCard";
import { ThinkingBlock } from "./ThinkingBlock";
import { ToolActivityIndicator } from "./ToolActivityIndicator";

interface Props {
  message: Message;
  convId: string;
  showFeedback?: boolean;
  showRegenerate?: boolean;
  onRegenerate?: () => void;
  onEdit?: (newContent: string) => void;
  onSwitchBranch?: (branchIndex: number) => void;
}

// Issue 1 — the "Sources (N)" attribution rail is hidden from end users.
// The backend still attaches ``context_sources`` to each assistant turn
// (useful for internal/debugging use), so we gate ONLY the UI render
// here rather than dropping the data. Flip to ``true`` to bring the rail
// back without touching the SourcesCard wiring below.
const SHOW_SOURCES_RAIL = false;

function MessageBubbleImpl({
  message,
  convId,
  showFeedback,
  showRegenerate,
  onRegenerate,
  onEdit,
  onSwitchBranch,
}: Props) {
  const t = useT();
  const isUser = message.role === "user";
  const router = useRouter();

  // Inline-edit state for user messages.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const editRef = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);

  // Voice output — only the auto-read effect uses this now; the
  // manual speaker button was removed from the action row to match
  // ChatGPT's icon set (copy + feedback + regenerate).
  const { speak } = useVoiceOutput();
  const autoRead = useVoiceStore((s) => s.autoRead);

  // Auto-read assistant replies once streaming finishes. Guarded by
  // role + status + the autoRead setting so the effect is a no-op
  // for every other message in the thread. The ref keeps us from
  // re-speaking the same message when an unrelated prop changes.
  const autoReadFiredRef = useRef(false);
  useEffect(() => {
    if (autoReadFiredRef.current) return;
    if (!autoRead) return;
    if (isUser) return;
    if (message.isStreaming) return;
    if (message.status !== "completed") return;
    if (!message.content.trim()) return;
    autoReadFiredRef.current = true;
    void speak(message.content, message.id);
  }, [
    autoRead,
    isUser,
    message.isStreaming,
    message.status,
    message.content,
    message.id,
    speak,
  ]);

  useEffect(() => {
    if (!editing) return;
    setDraft(message.content);
    requestAnimationFrame(() => {
      const ta = editRef.current;
      if (!ta) return;
      ta.focus();
      const end = ta.value.length;
      ta.setSelectionRange(end, end);
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`;
    });
  }, [editing, message.content]);

  function submitEdit() {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === message.content.trim()) {
      setEditing(false);
      return;
    }
    onEdit?.(trimmed);
    setEditing(false);
  }

  async function copyMessageContent() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(message.content);
      } else {
        const ta = document.createElement("textarea");
        ta.value = message.content;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked */
    }
  }

  // ───────────────────────────── USER ─────────────────────────────
  if (isUser) {
    // File-only sends now reach this branch with content="" — the
    // backend accepts them and ships a placeholder prompt to the
    // Gateway. Skip the empty text pill in that case so the user
    // doesn't see a tiny empty rounded rectangle floating above
    // their attachment cards. Edit mode always renders the pill so
    // the user can ADD text to a file-only message.
    const hasText = editing || message.content.trim().length > 0;
    const hasFiles = (message.files?.length ?? 0) > 0;
    if (!hasText && !hasFiles) {
      // Defensive: a completely empty user turn shouldn't exist any
      // more. If one slipped through (legacy DB row), render nothing
      // rather than a phantom pill.
      return null;
    }
    return (
      <div className="group message-enter flex w-full justify-end">
        <div className="flex max-w-[85%] flex-col items-end gap-1.5">
          {/* Attachments render ABOVE the text bubble (matches Claude /
              ChatGPT) so the image/file the user sent reads first. */}
          {hasFiles ? (
            <MessageAttachments files={message.files!} />
          ) : null}

          {hasText ? (
            <div
              className="rounded-bubble bg-bubble-user px-4 py-2.5 text-[15px] leading-relaxed text-text-primary shadow-sm"
              style={{ borderRadius: "20px" }}
            >
              {editing ? (
                <div className="flex w-full flex-col gap-2">
                  <textarea
                    ref={editRef}
                    value={draft}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      const ta = e.currentTarget;
                      ta.style.height = "auto";
                      ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`;
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        submitEdit();
                      } else if (e.key === "Escape") {
                        setEditing(false);
                      }
                    }}
                    rows={2}
                    className="scrollbar-thin w-full min-w-[260px] resize-none rounded-lg border border-border-strong bg-bg-base px-3 py-2 text-[15px] text-text-primary focus:outline-none focus:ring-2 focus:ring-border-strong/40"
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setEditing(false)}
                      className="focus-ring rounded-md border border-border px-2.5 py-1 text-[12px] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={submitEdit}
                      className="focus-ring rounded-md bg-text-primary px-2.5 py-1 text-[12px] font-medium text-bg-base hover:opacity-90"
                    >
                      Save & Submit
                    </button>
                  </div>
                </div>
              ) : (
                <CollapsibleUserText content={message.content} />
              )}
            </div>
          ) : null}

          {/* User message actions — copy + edit, revealed on hover.
              Copy is suppressed when there's no text to copy; edit
              stays available so users can add text to a file-only
              message after the fact. */}
          {!editing ? (
            <div className="message-actions flex items-center gap-0.5">
              {hasText ? (
                <ActionButton
                  ariaLabel={copied ? t("a11y.copied") : t("a11y.copyMessage")}
                  onClick={copyMessageContent}
                  tone={copied ? "success" : "muted"}
                >
                  {copied ? <CheckIcon /> : <CopyIcon />}
                </ActionButton>
              ) : null}
              {onEdit && !message.isStreaming ? (
                <ActionButton
                  ariaLabel={t("a11y.editMessage")}
                  onClick={() => setEditing(true)}
                >
                  <EditIcon />
                </ActionButton>
              ) : null}
            </div>
          ) : null}

          {message.has_branches &&
          message.branch_count &&
          message.branch_count > 1 &&
          onSwitchBranch ? (
            <BranchNavigator
              branchIndex={message.branch_index ?? 0}
              branchCount={message.branch_count}
              onSwitch={onSwitchBranch}
            />
          ) : null}
        </div>
      </div>
    );
  }

  // ───────────────────────── ASSISTANT ────────────────────────────
  // Assistant messages render the body directly — no avatar, no model
  // label. The active model is shown only in the composer's model
  // selector, never per-message.
  return (
    <div className="group message-enter flex w-full flex-col gap-1.5">
      {/* T4 — Thinking Mode. Renders ABOVE the body so the user
          reads "thought first, then answered". Self-hides when
          neither reasoning_content nor is_thinking is set, so
          non-reasoning turns are byte-identical to the pre-T4
          layout. */}
      {(message.reasoning_content || message.is_thinking) ? (
        <ThinkingBlock
          content={message.reasoning_content ?? ""}
          isStreaming={message.is_thinking ?? false}
          durationMs={message.thinking_duration_ms}
        />
      ) : null}

      {/* Phase 8 — sandboxed run_python. Above the body for the same
          reason as ThinkingBlock: the model runs the code first and
          then explains the result. Absent on every turn that didn't
          call the tool. */}
      {message.code_runs?.map((run) => (
        <CodeRunCard key={run.id} run={run} />
      ))}

      {/* A2 — generic activity for tools without a dedicated indicator
          (memory/state/past-search/canvas + unknown/MCP tools). Same
          zone as the code cards: the tool runs first, the explanation
          streams after. Rows exist only while a call is in flight. */}
      {message.tool_activities?.length ? (
        <ToolActivityIndicator activities={message.tool_activities} />
      ) : null}

      {/* Body */}
      <div className="prose-chat w-full text-text-primary">
        <AssistantContent message={message} convId={convId} />
      </div>

      {/* B1 — error bubble. Renders a distinct, red-tinted notice when
          the stream failed terminally (instead of leaving a blank
          reply). The localized Thai message + optional attempt log come
          from the SSE ``error`` frame via the chat store. */}
      {message.error_message ? (
        <ErrorNotice
          message={message.error_message}
          attempts={message.error_attempts}
        />
      ) : null}

      {/* B2 — fallback notice. A small line under the reply when a
          different model answered than the one requested. */}
      {message.fallback_info ? (
        <FallbackNotice
          requestedModel={message.fallback_info.requested_model}
          actualModel={message.fallback_info.actual_model}
        />
      ) : null}

      {/* Image-generation placeholder — occupies the attachment slot
          while the backend generates an image, then the real thumbnail
          (message_files) replaces it in place. */}
      {message.is_generating_image &&
      !(message.files && message.files.length > 0) ? (
        <ImageGeneratingPlaceholder />
      ) : null}

      {/* Video-generation placeholder — same swap-in-place behaviour. */}
      {message.is_generating_video &&
      !(message.files && message.files.length > 0) ? (
        <VideoGeneratingPlaceholder />
      ) : null}

      {message.files && message.files.length > 0 ? (
        <MessageAttachments files={message.files} />
      ) : null}

      {/* Web-search citation chips — the sources the assistant cited.
          Live via message_sources, hydrated from the API on reload. */}
      {message.search_sources && message.search_sources.length > 0 ? (
        <SearchCitations sources={message.search_sources} />
      ) : null}

      {/* S7 — "Sources used" rail. Renders only when the
          assistant turn drew on saved memory / state / search /
          compaction. Skipped during streaming so the card doesn't
          flash in before all sources are persisted server-side.
          Issue 1 — gated off (SHOW_SOURCES_RAIL) so end users don't
          see it; the underlying data is still delivered. */}
      {SHOW_SOURCES_RAIL &&
      !message.isStreaming &&
      message.context_sources &&
      message.context_sources.length > 0 ? (
        <SourcesCard
          sources={message.context_sources}
          onEditMemory={() => router.push("/chat/memory")}
          onOpenConversation={(id) => {
            if (id) router.push(`/chat/${id}`);
          }}
        />
      ) : null}

      {/* Action row — copy, thumbs-up, thumbs-down, regenerate.
          Always visible on AI messages (matches ChatGPT). Hidden only
          during streaming so the buttons don't reflow as the bubble
          grows. */}
      <div className="mt-2 flex items-center gap-1">
        <div
          className={`flex items-center gap-0.5 transition-opacity ${
            message.isStreaming ? "pointer-events-none opacity-0" : "opacity-100"
          }`}
        >
          <ActionButton
            ariaLabel={copied ? t("a11y.copied") : t("a11y.copyMessage")}
            onClick={copyMessageContent}
            tone={copied ? "success" : "muted"}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </ActionButton>
          {showFeedback ? (
            <FeedbackButtons
              convId={convId}
              messageId={message.id}
              current={message.feedback ?? null}
            />
          ) : null}
          {showRegenerate && onRegenerate ? (
            <ActionButton ariaLabel={t("a11y.regenerate")} onClick={onRegenerate}>
              <RegenerateIcon />
            </ActionButton>
          ) : null}
        </div>
        {message.status === "error" ? (
          <span className="ml-2 text-[11px] text-status-danger">
            {t("msg.aiError")}
          </span>
        ) : null}
        {message.status === "cancelled" ? (
          <span className="ml-2 text-[11px] text-text-muted">{t("msg.stopped")}</span>
        ) : null}
        {!message.isStreaming ? <MessageMeta message={message} /> : null}
        {!message.isStreaming ? (
          <MessageTime createdAt={message.created_at} />
        ) : null}
      </div>
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
const USER_COLLAPSE_VISIBLE_LINES = 8; // must match globals.css .user-msg-text.is-collapsed

function CollapsibleUserText({ content }: { content: string }) {
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
  return (
    <>
      <span
        ref={textRef}
        className={`user-msg-text whitespace-pre-wrap break-words${
          collapsed ? " is-collapsed" : ""
        }`}
      >
        {content}
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

function MessageTime({ createdAt }: { createdAt?: string }) {
  const locale = useI18nStore((s) => s.locale);
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

// Subtle per-message metadata: "GPT-4o · 342 tokens · 1.2s". Tokens
// come from the message_complete SSE frame (persisted server-side, so
// they survive reloads); the duration is local-only and disappears on
// reload. Renders nothing when no token data is available.
function MessageMeta({ message }: { message: Message }) {
  const models = useModelStore((s) => s.models);
  const usage = message.token_usage;
  if (!usage) return null;
  const total = (usage.input ?? 0) + (usage.output ?? 0);
  if (total <= 0) return null;
  const name = message.model_id
    ? (models.find((m) => m.id === message.model_id)?.name ?? message.model_id)
    : null;
  const parts: string[] = [];
  if (name) parts.push(name);
  parts.push(`${total.toLocaleString()} tokens`);
  if (message.response_time_ms && message.response_time_ms > 0) {
    parts.push(`${(message.response_time_ms / 1000).toFixed(1)}s`);
  }
  return (
    <span className="ml-2 text-[11px] text-text-muted">
      {parts.join(" · ")}
    </span>
  );
}

// B1 — terminal error notice. Red-tinted card distinct from normal
// message text so a failed turn reads unambiguously as an error. When
// the whole fallback chain failed, the per-model attempt log is shown
// in a collapsible list so the user (and support) can see what was
// tried and why each failed.
function ErrorNotice({
  message,
  attempts,
}: {
  message: string;
  attempts?: FallbackAttempt[];
}) {
  const t = useT();
  const hasAttempts = Array.isArray(attempts) && attempts.length > 0;
  return (
    <div
      role="alert"
      className="mt-1 rounded-lg border border-status-danger/30 bg-status-danger/10 px-3 py-2 text-sm text-status-danger"
    >
      <div className="flex items-start gap-2">
        <WarningIcon />
        <span className="leading-snug">{message}</span>
      </div>
      {hasAttempts ? (
        <details className="mt-1.5 pl-6 text-[11px] text-status-danger/80">
          <summary className="cursor-pointer select-none">
            {t("msg.attempts", { n: String(attempts!.length) })}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {attempts!.map((a, i) => (
              <li key={i}>
                {a.model ?? a.provider ?? "model"}
                {a.status === "success"
                  ? ` — ${t("msg.attemptOk")}`
                  : ` — ${a.error ?? a.error_code ?? t("msg.attemptFail")}`}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

// B2 — fallback notice. Small muted line under a reply that was served
// by a different model than requested (ChatGPT-style attribution).
function FallbackNotice({
  requestedModel,
  actualModel,
}: {
  requestedModel: string;
  actualModel: string;
}) {
  return (
    <div className="mt-0.5 text-[11px] text-text-muted">
      {fallbackNotice(requestedModel, actualModel)}
    </div>
  );
}

function WarningIcon() {
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

function ActionButton({
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
function CopyIcon() {
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
function CheckIcon() {
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
function EditIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 20h9M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" />
    </svg>
  );
}
function RegenerateIcon() {
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
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5" />
    </svg>
  );
}
// StreamingMarkdown renders an in-flight assistant message through the
// SAME shared <Markdown> pipeline (plugins + component overrides) as
// completed messages (Fix 2) so bullets / lists / headings / tables format live as
// tokens arrive instead of showing raw "-", "#", "**", "|" that only snap
// into elements — reflowing the whole block — when the stream ends.
// remark-gfm tolerates incomplete syntax (an unclosed ** stays literal,
// an unterminated ``` is an in-progress code block, a half table stays
// text until the delimiter row arrives). memo() skips the re-parse when a
// sibling state change re-renders the bubble without changing the text.
//
// Plan cards (:::excel-plan etc.) are intentionally NOT segmented while
// streaming — they only mount on the terminal "complete" event so the
// user doesn't see a half-parsed plan flicker into a card; until then the
// fence shows as text.
//
// Per-block memoization (BlockDiff port): the content is split into
// top-level blocks (fence/math aware — see lib/markdownBlocks.ts) and each
// block parses through its own memoized <Markdown>. Appending a token only
// changes the last block's text, so every earlier block skips the
// react-markdown + rehype re-parse instead of the whole message re-parsing
// once per rAF flush.
const MarkdownBlock = memo(function MarkdownBlock({ text }: { text: string }) {
  return <Markdown citations>{text}</Markdown>;
});

const StreamingMarkdown = memo(function StreamingMarkdown({
  content,
}: {
  content: string;
}) {
  return (
    <div className="streaming-body">
      {splitMarkdownBlocks(content).map((b) => (
        <MarkdownBlock key={b.key} text={b.text} />
      ))}
      <span className="streaming-cursor-block" aria-hidden />
    </div>
  );
});

function AssistantContent(props: { message: Message; convId: string }) {
  // Phase 6 / C3 — every code block rendered inside this message can
  // resolve which conversation + message it belongs to, so "Open in
  // Canvas" binds the artifact to a persisted version lineage.
  return (
    <CanvasSourceProvider
      conversationId={props.convId}
      messageId={props.message.id}
    >
      <AssistantContentBody {...props} />
    </CanvasSourceProvider>
  );
}

function AssistantContentBody({
  message,
  convId,
}: {
  message: Message;
  convId: string;
}) {
  const hasContent = message.content.length > 0;
  const isStreaming = message.isStreaming;

  if (!hasContent && isStreaming) {
    // While an image is generating, the ImageGeneratingPlaceholder
    // below the body already communicates progress — duplicate
    // typing dots would read as a second, unrelated loading state.
    if (message.is_generating_image) return null;
    // Same for video generation — the VideoGeneratingPlaceholder covers it.
    if (message.is_generating_video) return null;
    // While searching the web, show the searching indicator in place of
    // the typing dots. It self-hides once the answer streams in.
    if (message.is_searching) return <SearchingIndicator />;
    return <CatIndicator labelKey="gen.working" />;
  }

  if (isStreaming) {
    // Render markdown incrementally with the SAME pipeline as the
    // completed path (Fix 2) so lists/headings/tables format live and
    // don't reflow when the stream ends. Plan cards (:::excel-plan etc.)
    // are intentionally NOT segmented here — they only mount on the
    // terminal "complete" event so the user doesn't see a half-parsed
    // plan flicker into a card; until then the fence shows as text.
    return <StreamingMarkdown content={message.content} />;
  }

  // Split the completed content into prose / plan segments. Each
  // segment renders independently so the card mounts inline wherever
  // the AI placed the fenced block. :::excel-plan, :::doc-plan, and
  // :::pptx-plan are all recognised; everything else passes through
  // to the shared <Markdown> component.
  const segments = parsePlanSegments(message.content || "");
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "excel-plan") {
          return <ExcelPlanCard key={i} convId={convId} plan={seg.plan} />;
        }
        if (seg.kind === "doc-plan") {
          return <DocPlanCard key={i} convId={convId} plan={seg.plan} />;
        }
        if (seg.kind === "pptx-plan") {
          return <PptxPlanCard key={i} convId={convId} plan={seg.plan} />;
        }
        return (
          <Markdown key={i} citations>
            {seg.text}
          </Markdown>
        );
      })}
    </>
  );
}

// :::excel-plan / :::doc-plan / :::pptx-plan detection. The fences
// all use the Pandoc-style triple-colon container — react-markdown
// doesn't parse them natively, so we strip them out before
// rendering and substitute the matching card at the right
// position. Malformed JSON inside a fence falls back to rendering
// the raw block as text so the user can see what the AI produced.
type PlanSegment =
  | { kind: "text"; text: string }
  | { kind: "excel-plan"; plan: ExcelPlan }
  | { kind: "doc-plan"; plan: DocPlan }
  | { kind: "pptx-plan"; plan: PptxPlan };

// Single combined regex with a capture group on the plan type so
// one pass handles all three fence flavours in document order.
// Using a single pass also stops us from double-processing content
// when the AI emits more than one of these in the same message.
const PLAN_FENCE = /:::(excel-plan|doc-plan|pptx-plan)\s*\n([\s\S]*?)\n:::/g;

function parsePlanSegments(content: string): PlanSegment[] {
  const out: PlanSegment[] = [];
  let last = 0;
  PLAN_FENCE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = PLAN_FENCE.exec(content)) !== null) {
    const before = content.slice(last, match.index);
    if (before.trim()) out.push({ kind: "text", text: before });
    const kind = match[1] as "excel-plan" | "doc-plan" | "pptx-plan";
    const raw = match[2].trim();
    const parsed = tryParseJson(raw);
    if (parsed && typeof parsed === "object") {
      if (kind === "excel-plan") {
        out.push({ kind: "excel-plan", plan: parsed as ExcelPlan });
      } else if (kind === "doc-plan") {
        out.push({ kind: "doc-plan", plan: parsed as DocPlan });
      } else {
        out.push({ kind: "pptx-plan", plan: parsed as PptxPlan });
      }
    } else {
      out.push({ kind: "text", text: "```json\n" + raw + "\n```" });
    }
    last = match.index + match[0].length;
  }
  const tail = content.slice(last);
  if (tail.trim() || out.length === 0) out.push({ kind: "text", text: tail });
  return out;
}

function tryParseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function areEqual(prev: Props, next: Props): boolean {
  const a = prev.message;
  const b = next.message;
  if (a === b) {
    return (
      prev.showFeedback === next.showFeedback &&
      prev.showRegenerate === next.showRegenerate
    );
  }
  return (
    a.id === b.id &&
    a.content === b.content &&
    a.status === b.status &&
    a.isStreaming === b.isStreaming &&
    a.branch_index === b.branch_index &&
    a.branch_count === b.branch_count &&
    a.has_branches === b.has_branches &&
    a.token_usage === b.token_usage &&
    a.response_time_ms === b.response_time_ms &&
    // T4 — re-render while reasoning text streams in. Without these,
    // a turn that updates ONLY reasoning_content / is_thinking /
    // thinking_duration_ms (no content delta or status flip) would be
    // memoized stale and the ThinkingBlock would freeze mid-stream.
    a.reasoning_content === b.reasoning_content &&
    a.is_thinking === b.is_thinking &&
    a.thinking_duration_ms === b.thinking_duration_ms &&
    // Image-generation placeholder — re-render when the flag flips
    // (set by the image_generating SSE event, cleared by
    // message_files / message_complete / error).
    a.is_generating_image === b.is_generating_image &&
    a.is_generating_video === b.is_generating_video &&
    a.files === b.files &&
    // Web search — re-render when the searching flag flips or the
    // citation sources arrive (live via message_sources / on reload).
    a.is_searching === b.is_searching &&
    a.search_sources === b.search_sources &&
    // Phase 8 — re-render when a run_python frame lands (the store
    // replaces the array on every upsert, so identity is enough).
    a.code_runs === b.code_runs &&
    a.tool_activities === b.tool_activities &&
    // S7 — re-render when the per-turn context_sources array
    // arrives or changes. The first render after a stream
    // finishes carries no sources (they persist async); the next
    // message-list refresh hydrates them and we want the
    // SourcesCard to appear.
    a.context_sources === b.context_sources &&
    // B1/B2 — re-render when a terminal error or a fallback notice
    // lands on the message (both arrive as their own SSE frame after
    // content, so a stale memo would hide them).
    a.error_message === b.error_message &&
    a.error_attempts === b.error_attempts &&
    a.fallback_info === b.fallback_info &&
    prev.showFeedback === next.showFeedback &&
    prev.showRegenerate === next.showRegenerate
  );
}

export const MessageBubble = memo(MessageBubbleImpl, areEqual);
