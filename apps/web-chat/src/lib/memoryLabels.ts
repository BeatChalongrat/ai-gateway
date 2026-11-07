// FIX130 A3c — how the Memory page names a memory: when it was saved (an
// absolute date as well as "3 days ago"), what kind it is (category in the
// user's language), and which chat it came from. Pure, so the rules are
// pinned by memoryLabels.test.ts rather than by reading JSX.

import type { DictKey, Locale } from "./i18n/dict";
import type { Memory } from "./types";

// Short month names. Written out rather than taken from Intl so the page
// shows the same text in every browser and in the desktop's webview (a
// small-ICU build prints "Sep" for th-TH) and so the Buddhist-era year is
// not left to a calendar option some engines ignore.
const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "23 ก.ย. 2569" (Thai, Buddhist era) or "23 Sep 2026", in the viewer's own
 *  time zone. Empty for a timestamp that does not parse. */
export function formatMemoryDate(iso: string, locale: Locale): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const d = new Date(ms);
  if (locale === "th") return `${d.getDate()} ${TH_MONTHS[d.getMonth()]} ${d.getFullYear() + 543}`;
  return `${d.getDate()} ${EN_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** Category strings the extractor, the save_memory tool and the add form
 *  use. Anything else is shown as written — it is the user's own label or
 *  a model's, and a wrong translation would be worse than none. */
export const MEMORY_CATEGORY_KEYS: Record<string, DictKey> = {
  identity: "memory.category.identity",
  personal: "memory.category.personal",
  family: "memory.category.family",
  work: "memory.category.work",
  job: "memory.category.work",
  role: "memory.category.work",
  organization: "memory.category.work",
  location: "memory.category.location",
  health: "memory.category.health",
  diet: "memory.category.diet",
  food: "memory.category.diet",
  language: "memory.category.language",
  response_style: "memory.category.response_style",
  style: "memory.category.response_style",
  format: "memory.category.response_style",
  coding: "memory.category.coding",
  workflow: "memory.category.workflow",
  project: "memory.category.project",
  interest: "memory.category.interest",
  hobby: "memory.category.interest",
  preference: "memory.category.preference",
  schedule: "memory.category.schedule",
  education: "memory.category.education",
};

export function memoryCategoryLabel(
  category: string | null | undefined,
  t: (key: DictKey) => string,
): string | null {
  const raw = (category ?? "").trim();
  if (!raw) return null;
  const key = MEMORY_CATEGORY_KEYS[raw.toLowerCase()];
  return key ? t(key) : raw;
}

export type MemoryOrigin =
  | { kind: "chat"; conversationId: string; title: string | null }
  | { kind: "deleted" }
  | { kind: "manual" }
  | { kind: "none" };

/** Which chat a memory came from. The title comes from the server (the list
 *  endpoint resolves it); an older server that does not send it falls back
 *  to the sidebar's copy of the chat, then to an untitled link. A memory
 *  the user typed on this page has no chat: "added by you". */
export function memoryOriginOf(
  m: Pick<Memory, "source_type" | "source_conversation_id" | "source_conversation_title" | "source_conversation_deleted">,
  resolveTitle?: (id: string) => string | undefined,
): MemoryOrigin {
  const convId = m.source_conversation_id;
  if (convId) {
    if (m.source_conversation_deleted) return { kind: "deleted" };
    const title = m.source_conversation_title || resolveTitle?.(convId) || null;
    return { kind: "chat", conversationId: convId, title };
  }
  if (m.source_type === "user_explicit") return { kind: "manual" };
  return { kind: "none" };
}
