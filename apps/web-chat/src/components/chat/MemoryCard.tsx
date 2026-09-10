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
// border-border-subtle / border-border for grouping.

import { Pencil, Pin, PinOff, X } from "lucide-react";

import { timeAgo } from "@/components/workspace/workspaceUtil";
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import type { Memory } from "@/lib/types";

// Maps the memory_type enum the API exposes to the i18n dict key
// for its lowercase noun form ("fact", "preference"…). Used by the
// metadata line; the dialog dropdown uses the parallel
// `memory.form.opt.*` keys for capitalised labels.
export const MEMORY_TYPE_DICT_KEYS: Record<string, DictKey> = {
  explicit_fact: "memory.type.fact",
  response_preference: "memory.type.preference",
  ongoing_interest: "memory.type.interest",
  confirmed_instruction: "memory.type.instruction",
  inferred_preference: "memory.type.suggested",
  artifact_reference: "memory.type.fileReference",
};

interface Props {
  memory: Memory;
  onEdit: (memory: Memory) => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  /** Optional. When wired, the source line resolves the source
   *  conversation id to its title via this lookup. Falls back to
   *  "another conversation" inside the card when the id isn't in
   *  the user's current conversation list. */
  resolveConversationTitle?: (id: string) => string | undefined;
}

export function MemoryCard({
  memory,
  onEdit,
  onDelete,
  onTogglePin,
  resolveConversationTitle,
}: Props) {
  const t = useT();
  const typeKey = MEMORY_TYPE_DICT_KEYS[memory.memory_type];
  const typeLabel = typeKey ? t(typeKey) : memory.memory_type;
  const metaParts = [
    memory.category || null,
    typeLabel,
    t("memory.card.savedAgo", { time: timeAgo(memory.created_at) }),
  ].filter(Boolean) as string[];

  const sourceLabel = renderSource(memory, t, resolveConversationTitle);
  const usageLabel = renderUsage(memory, t);

  return (
    <div
      className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-3 transition-colors hover:border-border"
      data-pinned={memory.is_pinned ? "true" : undefined}
    >
      <div className="flex items-start gap-2">
        {memory.is_pinned ? (
          <Pin
            className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-muted"
            aria-label={t("memory.card.pinned")}
          />
        ) : null}
        <p className="min-w-0 flex-1 text-sm text-text-primary">{memory.content}</p>
      </div>

      <p className="mt-1 text-xs text-text-muted">{metaParts.join(" · ")}</p>

      {sourceLabel ? (
        <p className="mt-0.5 text-xs text-text-muted">
          {t("memory.card.sourceLabel")}{" "}
          <span className="text-text-muted">{sourceLabel}</span>
        </p>
      ) : null}

      {usageLabel ? (
        <p className="mt-0.5 text-xs text-text-muted">{usageLabel}</p>
      ) : null}

      <div className="mt-2 flex items-center justify-end gap-1">
        <CardAction
          label={t("memory.card.edit")}
          icon={<Pencil className="h-3.5 w-3.5" />}
          onClick={() => onEdit(memory)}
        />
        <CardAction
          label={memory.is_pinned ? t("memory.card.unpin") : t("memory.card.pin")}
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
          label={t("memory.card.delete")}
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
          ? "text-text-muted hover:bg-status-danger/10 hover:text-status-danger"
          : "text-text-muted hover:bg-bg-elevated hover:text-text-primary"
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
  t: (key: DictKey, vars?: Record<string, string>) => string,
  resolveConversationTitle?: (id: string) => string | undefined,
): string | null {
  if (m.source_conversation_id && resolveConversationTitle) {
    const title = resolveConversationTitle(m.source_conversation_id);
    if (title) return t("memory.card.source.conv", { title });
  }
  switch (m.source_type) {
    case "user_explicit":
      return m.source_conversation_id
        ? t("memory.card.source.userExplicitConv")
        : t("memory.card.source.userExplicitDirect");
    case "assistant_inferred":
      return t("memory.card.source.assistantInferred");
    case "conversation_summary":
      return t("memory.card.source.summary");
    case "artifact_event":
      return t("memory.card.source.artifact");
    default:
      return null;
  }
}

function renderUsage(
  m: Memory,
  t: (key: DictKey, vars?: Record<string, string>) => string,
): string | null {
  if (m.use_count <= 0) return null;
  const used =
    m.use_count === 1
      ? t("memory.card.usedOne")
      : t("memory.card.usedMany", { count: String(m.use_count) });
  if (m.last_used_at) {
    return `${used} · ${t("memory.card.lastUsed", {
      time: timeAgo(m.last_used_at),
    })}`;
  }
  return used;
}
