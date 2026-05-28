"use client";

// MemoryCard — one row of the Memory Settings list.
//
// Shows:
//   - Pin glyph (if pinned) + content text (the main line)
//   - Metadata line: category · type · saved {time ago}
//   - Source line: where this memory came from (a conversation,
//     direct API input, AI-detected, etc.)
//   - Usage line (only when use_count > 0)
//   - Action buttons: Edit, Pin/Unpin, Delete
//
// Visual language matches the rest of the chat surface: muted
// monochrome (no per-type accent colours), the CSS-variable
// tokens (text-text-primary / -secondary / -tertiary / -muted),
// border-border-subtle / border-border-default for grouping.

import { Pencil, Pin, PinOff, X } from "lucide-react";

import { timeAgo } from "@/components/workspace/workspaceUtil";
import type { Memory } from "@/lib/types";

// Human labels for the memory_type enum the API exposes. Kept
// here so the dropdown in MemoryFormDialog + the metadata line
// here stay in lockstep.
export const MEMORY_TYPE_LABELS: Record<string, string> = {
  explicit_fact: "fact",
  response_preference: "preference",
  ongoing_interest: "interest",
  confirmed_instruction: "instruction",
  inferred_preference: "suggested",
  artifact_reference: "file reference",
};

interface Props {
  memory: Memory;
  onEdit: (memory: Memory) => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  /** Optional. When wired, the source line resolves the source
   *  conversation id to its title via this lookup. Falls back to
   *  "another conversation" when the id isn't in the user's
   *  current conversation list. */
  resolveConversationTitle?: (id: string) => string | undefined;
}

export function MemoryCard({
  memory,
  onEdit,
  onDelete,
  onTogglePin,
  resolveConversationTitle,
}: Props) {
  const typeLabel =
    MEMORY_TYPE_LABELS[memory.memory_type] ?? memory.memory_type;
  const metaParts = [
    memory.category || null,
    typeLabel,
    `saved ${timeAgo(memory.created_at)}`,
  ].filter(Boolean) as string[];

  const sourceLabel = renderSource(memory, resolveConversationTitle);
  const usageLabel = renderUsage(memory);

  return (
    <div
      className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-3 transition-colors hover:border-border-default"
      data-pinned={memory.is_pinned ? "true" : undefined}
    >
      <div className="flex items-start gap-2">
        {memory.is_pinned ? (
          <Pin
            className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-tertiary"
            aria-label="Pinned"
          />
        ) : null}
        <p className="min-w-0 flex-1 text-sm text-text-primary">{memory.content}</p>
      </div>

      <p className="mt-1 text-xs text-text-tertiary">{metaParts.join(" · ")}</p>

      {sourceLabel ? (
        <p className="mt-0.5 text-xs text-text-muted">
          Source: <span className="text-text-tertiary">{sourceLabel}</span>
        </p>
      ) : null}

      {usageLabel ? (
        <p className="mt-0.5 text-xs text-text-muted">{usageLabel}</p>
      ) : null}

      <div className="mt-2 flex items-center justify-end gap-1">
        <CardAction
          label="Edit"
          icon={<Pencil className="h-3.5 w-3.5" />}
          onClick={() => onEdit(memory)}
        />
        <CardAction
          label={memory.is_pinned ? "Unpin" : "Pin"}
          icon={
            memory.is_pinned ? (
              <PinOff className="h-3.5 w-3.5" />
            ) : (
              <Pin className="h-3.5 w-3.5" />
            )
          }
          onClick={() => onTogglePin(memory.id, !memory.is_pinned)}
        />
        <CardAction
          label="Delete"
          icon={<X className="h-4 w-4" />}
          onClick={() => onDelete(memory.id)}
          danger
        />
      </div>
    </div>
  );
}

function CardAction({
  label,
  icon,
  onClick,
  danger,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors ${
        danger
          ? "text-text-tertiary hover:bg-status-danger/10 hover:text-status-danger"
          : "text-text-tertiary hover:bg-bg-elevated hover:text-text-primary"
      }`}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

// renderSource — collapses the (source_type, source_conversation_id)
// pair into a single line the user can parse. Conversation titles
// resolve through the optional lookup; otherwise we describe the
// origin in plain English.
function renderSource(
  m: Memory,
  resolveConversationTitle?: (id: string) => string | undefined,
): string | null {
  if (m.source_conversation_id && resolveConversationTitle) {
    const title = resolveConversationTitle(m.source_conversation_id);
    if (title) return `"${title}" conversation`;
  }
  switch (m.source_type) {
    case "user_explicit":
      return m.source_conversation_id ? "saved from a conversation" : "direct input";
    case "assistant_inferred":
      return "AI detected from conversation";
    case "conversation_summary":
      return "conversation summary";
    case "artifact_event":
      return "file event";
    default:
      return null;
  }
}

function renderUsage(m: Memory): string | null {
  if (m.use_count <= 0) return null;
  const used = `Used ${m.use_count} ${m.use_count === 1 ? "time" : "times"}`;
  if (m.last_used_at) {
    return `${used} · last used ${timeAgo(m.last_used_at)}`;
  }
  return used;
}
