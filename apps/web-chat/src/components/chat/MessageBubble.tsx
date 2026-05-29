"use client";

// AI messages render plain (no bubble, no avatar, no model label) so
// the conversation reads like a clean document. The action bar
// (copy / thumbs-up / thumbs-down / regenerate) appears on hover.
// The active model is shown only in the composer's model selector,
// never per-message. User messages stay as a soft gray pill aligned
// to the right.
//
// TODO(phase2.session2): add LaTeX support via remark-math + rehype-katex.
// Skipped here to avoid pulling katex's 100kB+ stylesheet into the chat
// bundle for a feature most prompts don't need.

import { memo, useEffect, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import type { Message } from "@/lib/types";
import { useVoiceOutput } from "@/hooks/useVoiceOutput";
import { useVoiceStore } from "@/stores/voiceStore";

import { BranchNavigator } from "./BranchNavigator";
import { CodeBlock } from "./CodeBlock";
import { DocPlanCard } from "./DocPlanCard";
import type { DocPlan } from "./DocPlanCard";
import { ExcelPlanCard } from "./ExcelPlanCard";
import type { ExcelPlan } from "./ExcelPlanCard";
import { FeedbackButtons } from "./FeedbackButtons";
import { SourcesCard } from "./SourcesCard";
import { MessageAttachments } from "./MessageAttachments";
import { PptxPlanCard } from "./PptxPlanCard";
import type { PptxPlan } from "./PptxPlanCard";
import { StreamingContent } from "./StreamingContent";
import { ThinkingBlock } from "./ThinkingBlock";

interface Props {
  message: Message;
  convId: string;
  showFeedback?: boolean;
  showRegenerate?: boolean;
  onRegenerate?: () => void;
  onEdit?: (newContent: string) => void;
  onSwitchBranch?: (branchIndex: number) => void;
}

// Citation marker — RAG context tells the model to cite sources using
// the literal pattern ``[Source: <filename>]``. We post-process the
// assistant text to convert that pattern into a markdown link with a
// special internal href scheme; the custom anchor renderer below
// detects that scheme and renders a pill instead of a regular link.
//
// Why a hidden link scheme? It survives every place ReactMarkdown
// passes children through (paragraphs, list items, table cells)
// without us re-implementing the renderer for each node type.
const CITATION_PREFIX = "#__rag_citation:";
const CITATION_PATTERN = /\[Source:\s*([^\]]+?)\]/g;

function renderCitations(text: string): string {
  // Escape any existing parentheses inside the filename so the markdown
  // link grammar parses cleanly. We don't try to be clever — citations
  // come from the indexer which already sanitizes filenames, so the
  // edge cases here are vanishingly rare.
  return text.replace(CITATION_PATTERN, (_, raw: string) => {
    const filename = raw.trim();
    const safe = filename
      .replace(/\(/g, "%28")
      .replace(/\)/g, "%29");
    return `[__RAG_CITATION__${filename}](${CITATION_PREFIX}${safe})`;
  });
}

const markdownComponents = {
  a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const href = props.href ?? "";
    if (href.startsWith(CITATION_PREFIX)) {
      const filename = decodeURIComponent(href.slice(CITATION_PREFIX.length));
      return <CitationPill filename={filename} />;
    }
    return <a {...props} target="_blank" rel="noopener noreferrer" />;
  },
  pre: (props: { children?: React.ReactNode }) => <CodeBlock {...props} />,
};

function CitationPill({ filename }: { filename: string }) {
  return (
    <span
      title={`From knowledge base: ${filename}`}
      className="mx-0.5 inline-flex items-center gap-1 rounded-full border border-border bg-bg-elevated px-2 py-0.5 align-baseline text-[11px] font-medium text-text-secondary"
    >
      <svg
        width={10}
        height={10}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <path d="M14 3v6h6" />
      </svg>
      {filename}
    </span>
  );
}

function MessageBubbleImpl({
  message,
  convId,
  showFeedback,
  showRegenerate,
  onRegenerate,
  onEdit,
  onSwitchBranch,
}: Props) {
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
                <span className="whitespace-pre-wrap break-words">
                  {message.content}
                </span>
              )}
            </div>
          ) : null}

          {hasFiles ? (
            <MessageAttachments files={message.files!} />
          ) : null}

          {/* User message actions — copy + edit, revealed on hover.
              Copy is suppressed when there's no text to copy; edit
              stays available so users can add text to a file-only
              message after the fact. */}
          {!editing ? (
            <div className="message-actions flex items-center gap-0.5">
              {hasText ? (
                <ActionButton
                  ariaLabel={copied ? "Copied" : "Copy message"}
                  onClick={copyMessageContent}
                  tone={copied ? "success" : "muted"}
                >
                  {copied ? <CheckIcon /> : <CopyIcon />}
                </ActionButton>
              ) : null}
              {onEdit && !message.isStreaming ? (
                <ActionButton
                  ariaLabel="Edit message"
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

      {/* Body */}
      <div className="prose-chat w-full text-text-primary">
        <AssistantContent message={message} convId={convId} />
      </div>

      {message.files && message.files.length > 0 ? (
        <MessageAttachments files={message.files} />
      ) : null}

      {/* S7 — "Sources used" rail. Renders only when the
          assistant turn drew on saved memory / state / search /
          compaction. Skipped during streaming so the card doesn't
          flash in before all sources are persisted server-side. */}
      {!message.isStreaming &&
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
            ariaLabel={copied ? "Copied" : "Copy message"}
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
            <ActionButton ariaLabel="Regenerate" onClick={onRegenerate}>
              <RegenerateIcon />
            </ActionButton>
          ) : null}
        </div>
        {message.status === "error" ? (
          <span className="ml-2 text-[11px] text-status-danger">
            AI service error
          </span>
        ) : null}
        {message.status === "cancelled" ? (
          <span className="ml-2 text-[11px] text-text-muted">stopped</span>
        ) : null}
      </div>
    </div>
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
function AssistantContent({ message, convId }: { message: Message; convId: string }) {
  const hasContent = message.content.length > 0;
  const isStreaming = message.isStreaming;

  if (!hasContent && isStreaming) {
    return (
      <span className="typing-indicator" aria-label="Thinking">
        <span />
        <span />
        <span />
      </span>
    );
  }

  if (isStreaming) {
    // During streaming we render plain text + simple code-fence
    // splitting. The :::excel-plan card only mounts once the
    // assistant's terminal "complete" event arrives so the user
    // doesn't see a half-parsed plan flicker into a card.
    return <StreamingContent content={message.content} />;
  }

  // Split the completed content into prose / plan segments. Each
  // segment renders independently so the card mounts inline wherever
  // the AI placed the fenced block. :::excel-plan, :::doc-plan, and
  // :::pptx-plan are all recognised; everything else passes through
  // to ReactMarkdown.
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
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={markdownComponents}
          >
            {renderCitations(seg.text)}
          </ReactMarkdown>
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
    a.files === b.files &&
    // S7 — re-render when the per-turn context_sources array
    // arrives or changes. The first render after a stream
    // finishes carries no sources (they persist async); the next
    // message-list refresh hydrates them and we want the
    // SourcesCard to appear.
    a.context_sources === b.context_sources &&
    prev.showFeedback === next.showFeedback &&
    prev.showRegenerate === next.showRegenerate
  );
}

export const MessageBubble = memo(MessageBubbleImpl, areEqual);
