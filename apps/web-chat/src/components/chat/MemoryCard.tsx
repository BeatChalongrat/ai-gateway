"use client";

// MemoryCard — one row of the Memory Settings list.
//
// Shows:
//   - Pin glyph (if pinned) + content text (the main line)
//   - Metadata line: category (in the user's language) · type · how it
//     was saved (at the user's request / suggested by AI)
//   - When line (FIX130 A3c): an absolute date ("23 ก.ย. 2569") and the
//     relative time — "3 days ago" alone could not be told apart from
//     another memory saved the same week
//   - Source line: which chat it came from — the chat's title as a link
//     (resolved by the server), "a deleted chat", or "added by you"
//   - Usage line (only when use_count > 0)
//   - Action buttons: Edit, Pin/Unpin, Delete
//
// Visual language matches the rest of the chat surface: muted
// monochrome (no per-type accent colours), the CSS-variable
// tokens (text-text-primary / -secondary / -tertiary / -muted),
// border-border-subtle / border-border for grouping.

import Link from "next/link";
import { Pencil, Pin, PinOff, X } from "lucide-react";

import { timeAgo } from "@/components/workspace/workspaceUtil";
import { useI18nStore, useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import { formatMemoryDate, memoryCategoryLabel, memoryOriginOf } from "@/lib/memoryLabels";
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
  /** Optional. A fallback for a server that does not send
   *  source_conversation_title yet: resolves the source conversation id
   *  through the sidebar's loaded list. */
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
    memoryCategoryLabel(memory.category, t),
    typeLabel,
    howSaved(memory, t),
  ].filter(Boolean) as string[];

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

      <p className="mt-1 text-xs text-text-muted" data-testid="memory-meta">
        {metaParts.join(" · ")}
      </p>
      <MemoryWhen memory={memory} labelKey="memory.card.savedOn" />
      <MemoryOrigin memory={memory} resolveConversationTitle={resolveConversationTitle} />

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

// howSaved — the trust tier in words: saved because the user asked, or
// suggested by AI (and, if it is in the saved list, confirmed by the user).
function howSaved(
  m: Memory,
  t: (key: DictKey, vars?: Record<string, string>) => string,
): string | null {
  switch (m.source_type) {
    case "user_explicit":
      return m.source_conversation_id ? t("memory.card.source.userExplicitConv") : null;
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

/** FIX130 A3c — "บันทึกเมื่อ 23 ก.ย. 2569 (3 วันที่แล้ว)". Shared with
 *  PendingPreferenceCard (labelKey memory.card.suggestedOn). */
export function MemoryWhen({ memory, labelKey }: { memory: Memory; labelKey: "memory.card.savedOn" | "memory.card.suggestedOn" }) {
  const t = useT();
  const locale = useI18nStore((s) => s.locale);
  const date = formatMemoryDate(memory.created_at, locale);
  if (!date) return null;
  return (
    <p className="mt-0.5 text-xs text-text-muted" data-testid="memory-when">
      <time dateTime={memory.created_at}>
        {t(labelKey, { date, ago: timeAgo(memory.created_at) })}
      </time>
    </p>
  );
}

/** FIX130 A3c — which chat the memory came from: its title as a link,
 *  "a deleted chat", or "added by you". Shared with PendingPreferenceCard. */
export function MemoryOrigin({
  memory,
  resolveConversationTitle,
}: {
  memory: Memory;
  resolveConversationTitle?: (id: string) => string | undefined;
}) {
  const t = useT();
  const origin = memoryOriginOf(memory, resolveConversationTitle);
  if (origin.kind === "none") return null;
  return (
    <p className="mt-0.5 text-xs text-text-muted" data-testid="memory-origin">
      {t("memory.card.sourceLabel")}{" "}
      {origin.kind === "chat" ? (
        <Link
          href={`/chat/${origin.conversationId}`}
          className="text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
        >
          {origin.title ?? t("memory.card.origin.untitledChat")}
        </Link>
      ) : origin.kind === "deleted" ? (
        <span>{t("memory.card.origin.deletedChat")}</span>
      ) : (
        <span>{t("memory.card.origin.manual")}</span>
      )}
    </p>
  );
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
