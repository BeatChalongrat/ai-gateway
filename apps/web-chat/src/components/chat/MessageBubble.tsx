"use client";

// Redesigned for Phase 3: AI messages render plain (no bubble) with
// an avatar + model-name label row and a hover-revealed action bar.
// User messages stay as a soft gray pill aligned to the right.
//
// TODO(phase2.session2): add LaTeX support via remark-math + rehype-katex.
// Skipped here to avoid pulling katex's 100kB+ stylesheet into the chat
// bundle for a feature most prompts don't need.

import { memo, useEffect, useRef, useState } from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import type { Message } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import { AssistantAvatar } from "./AssistantAvatar";
import { BranchNavigator } from "./BranchNavigator";
import { CodeBlock } from "./CodeBlock";
import { FeedbackButtons } from "./FeedbackButtons";
import { MessageAttachments } from "./MessageAttachments";
import { StreamingContent } from "./StreamingContent";

interface Props {
  message: Message;
  convId: string;
  modelId?: string | null;
  /** Always shown on the AI label row when known; rendered as a
   *  separate pill on user messages only when the conversation
   *  mixes multiple models. */
  showModelBadge?: boolean;
  showFeedback?: boolean;
  showRegenerate?: boolean;
  onRegenerate?: () => void;
  onEdit?: (newContent: string) => void;
  onSwitchBranch?: (branchIndex: number) => void;
}

const markdownComponents = {
  a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
  pre: (props: { children?: React.ReactNode }) => <CodeBlock {...props} />,
};

function MessageBubbleImpl({
  message,
  convId,
  modelId,
  showModelBadge,
  showFeedback,
  showRegenerate,
  onRegenerate,
  onEdit,
  onSwitchBranch,
}: Props) {
  const isUser = message.role === "user";
  const effectiveModelId = message.model_id ?? modelId ?? null;
  const modelEntry = useModelStore((s) =>
    effectiveModelId ? s.getModelById(effectiveModelId) : undefined,
  );

  // Inline-edit state for user messages.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const editRef = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);

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
    return (
      <div className="group message-enter flex w-full justify-end">
        <div className="flex max-w-[85%] flex-col items-end gap-1.5">
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
                  className="scrollbar-thin w-full min-w-[260px] resize-none rounded-lg border border-accent bg-bg-base px-3 py-2 text-[15px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/30"
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
                    className="focus-ring rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-white hover:bg-accent-hover"
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

          {message.files && message.files.length > 0 ? (
            <MessageAttachments files={message.files} />
          ) : null}

          {/* User message actions — copy + edit, revealed on hover */}
          {!editing ? (
            <div className="message-actions flex items-center gap-0.5">
              <ActionButton
                ariaLabel={copied ? "Copied" : "Copy message"}
                onClick={copyMessageContent}
                tone={copied ? "success" : "muted"}
              >
                {copied ? <CheckIcon /> : <CopyIcon />}
              </ActionButton>
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
  const labelName = modelEntry?.name ?? effectiveModelId ?? "SRAN Chat";
  return (
    <div className="group message-enter flex w-full flex-col gap-2">
      {/* Avatar + model label row */}
      <div className="flex items-center gap-2">
        <AssistantAvatar modelId={effectiveModelId} size={26} />
        <span className="text-[13px] font-medium text-text-secondary">
          {labelName}
        </span>
        {showModelBadge && effectiveModelId ? (
          <span className="text-[10px] text-text-muted">
            · {effectiveModelId}
          </span>
        ) : null}
      </div>

      {/* Body */}
      <div className="prose-chat w-full pl-9 text-text-primary">
        <AssistantContent message={message} />
      </div>

      {message.files && message.files.length > 0 ? (
        <div className="pl-9">
          <MessageAttachments files={message.files} />
        </div>
      ) : null}

      {/* Action row */}
      <div className="flex items-center gap-1 pl-9">
        <div
          className={`message-actions flex items-center gap-0.5 ${
            message.isStreaming ? "opacity-0" : ""
          }`}
        >
          <ActionButton
            ariaLabel={copied ? "Copied" : "Copy message"}
            onClick={copyMessageContent}
            tone={copied ? "success" : "muted"}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </ActionButton>
          {showRegenerate && onRegenerate ? (
            <ActionButton ariaLabel="Regenerate" onClick={onRegenerate}>
              <RegenerateIcon />
            </ActionButton>
          ) : null}
          {showFeedback ? (
            <FeedbackButtons
              convId={convId}
              messageId={message.id}
              current={message.feedback ?? null}
            />
          ) : null}
        </div>
        {message.token_usage ? (
          <span className="ml-2 text-[10px] text-text-muted">
            {message.token_usage.input + message.token_usage.output} tokens
          </span>
        ) : null}
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
      : "text-text-muted hover:text-text-primary";
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

function CopyIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="9" y="9" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.7" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}
function CheckIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function EditIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 20h9M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function RegenerateIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function AssistantContent({ message }: { message: Message }) {
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
    return <StreamingContent content={message.content} />;
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={markdownComponents}
    >
      {message.content || ""}
    </ReactMarkdown>
  );
}

function areEqual(prev: Props, next: Props): boolean {
  const a = prev.message;
  const b = next.message;
  if (a === b) {
    return (
      prev.showFeedback === next.showFeedback &&
      prev.showRegenerate === next.showRegenerate &&
      prev.showModelBadge === next.showModelBadge
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
    prev.showFeedback === next.showFeedback &&
    prev.showRegenerate === next.showRegenerate &&
    prev.showModelBadge === next.showModelBadge &&
    prev.modelId === next.modelId
  );
}

export const MessageBubble = memo(MessageBubbleImpl, areEqual);
