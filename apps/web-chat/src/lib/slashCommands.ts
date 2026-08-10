// Phase 10 Task 2 — slash quick actions.
//
// A slash command is a KEYBOARD SHORTCUT INTO AN EXISTING FLOW, never a
// new capability: every entry here maps to something the composer or the
// app already does (image mode, video mode, new chat, the shortcuts
// sheet). That is deliberate — a command that "does something special"
// would need its own server contract, and there is none.
//
// Trigger rule: the menu opens only when the WHOLE input is a slash
// token — "/" possibly followed by letters, nothing before it. So a "/"
// typed mid-sentence ("and/or", "24/7", a URL path) never hijacks the
// composer. Once the user types a space the token is over and the menu
// closes, so "/image of a cat" is ordinary text.

import type { DictKey } from "@/lib/i18n/dict";

export type SlashActionId = "image" | "video" | "new" | "shortcuts";

export interface SlashCommand {
  id: SlashActionId;
  /** Typed form, without the leading slash. */
  keyword: string;
  /** Dictionary key for the label. */
  labelKey: DictKey;
  /** Dictionary key for the one-line hint. */
  hintKey: DictKey;
  /** Aliases so /img and /pic find the image action too. */
  aliases?: string[];
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: "image",
    keyword: "image",
    labelKey: "slash.image",
    hintKey: "slash.image.hint",
    aliases: ["img", "pic"],
  },
  {
    id: "video",
    keyword: "video",
    labelKey: "slash.video",
    hintKey: "slash.video.hint",
    aliases: ["vid"],
  },
  { id: "new", keyword: "new", labelKey: "slash.new", hintKey: "slash.new.hint" },
  {
    id: "shortcuts",
    keyword: "shortcuts",
    labelKey: "slash.shortcuts",
    hintKey: "slash.shortcuts.hint",
    aliases: ["keys", "help"],
  },
];

/** The typed slash token, or null when the input isn't one.
 *  "" (empty query) means a bare "/" — show everything. */
export function slashQuery(text: string): string | null {
  // No trim(): leading whitespace means the user is writing prose that
  // happens to contain a slash, not invoking a command.
  const m = /^\/([a-zA-Z]*)$/.exec(text);
  return m ? m[1].toLowerCase() : null;
}

/** Commands matching the typed token, in registry order. An empty query
 *  lists everything; an unknown one lists nothing (the menu then shows
 *  its empty state rather than silently disappearing). */
export function filterSlashCommands(query: string): SlashCommand[] {
  const q = query.toLowerCase();
  if (q === "") return SLASH_COMMANDS;
  return SLASH_COMMANDS.filter(
    (c) =>
      c.keyword.startsWith(q) || (c.aliases ?? []).some((a) => a.startsWith(q)),
  );
}

/** True when the composer should show the slash menu for this input. */
export function shouldShowSlashMenu(text: string): boolean {
  return slashQuery(text) !== null;
}
