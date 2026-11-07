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

export type SlashActionId = "image" | "video" | "new" | "shortcuts" | "skill";

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
  /** FIX33 B6 — a skill template: plain-text label/hint (user content, not
   *  dictionary keys), the text to drop into the composer, the skill id
   *  (React key). Still "a shortcut into an existing flow": the skill
   *  activates on send exactly as if the user typed its trigger. */
  label?: string;
  hint?: string;
  insertText?: string;
  skillId?: string;
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
  // FIX33 B6: letters of any script (a Thai skill name is a valid keyword)
  // plus dashes — still never a space, so prose stays prose.
  const m = /^\/([\p{L}\p{M}\p{N}-]*)$/u.exec(text);
  return m ? m[1].toLowerCase() : null;
}

/** Commands matching the typed token, in registry order. An empty query
 *  lists everything; an unknown one lists nothing (the menu then shows
 *  its empty state rather than silently disappearing). */
export function filterSlashCommands(query: string, extra: readonly SlashCommand[] = []): SlashCommand[] {
  const q = query.toLowerCase();
  const all = extra.length ? [...SLASH_COMMANDS, ...extra] : SLASH_COMMANDS;
  if (q === "") return all;
  return all.filter(
    (c) =>
      c.keyword.startsWith(q) || (c.aliases ?? []).some((a) => a.startsWith(q)),
  );
}

/** True when the composer should show the slash menu for this input. */
export function shouldShowSlashMenu(text: string): boolean {
  return slashQuery(text) !== null;
}

// FIX84 Part A row 2 — a slash command is offered exactly where the toolbar
// button it stands for is offered. The composer's 🎨 / 🎬 buttons already
// read the catalogue's capability answer (FIX62/64/65: provider-aware
// detection, the chat floor, "a key being present is not a key being
// usable"); this function applies the SAME answers to the menu instead of
// keeping a second list. Three outcomes, mirroring the buttons:
//   * usable   → listed and runnable;
//   * disabled → listed with the reason (the feature exists on this
//                deployment, the SELECTED model cannot drive it — the 🎬
//                rule from FIX47.6 Part C); picking it toasts the reason
//                and leaves the composer text alone;
//   * hidden   → not listed (nothing on this deployment can do it — the
//                FIX11/FIX34.1 rule: no affordance for what nothing can do;
//                video is also hidden on the desktop host, like its button).
// A model that gains the capability later simply flips the flags on the
// next catalogue load — there is nothing here to update.
export interface SlashAvailability {
  canGenerateImages: boolean;
  imageFeatureAvailable: boolean;
  canGenerateVideos: boolean;
  videoFeatureAvailable: boolean;
  hosted: boolean;
}

export type SlashOffer = SlashCommand & {
  /** Present when the command is listed but cannot run right now:
   *  the dictionary key of the reason shown in the menu and toasted on pick. */
  disabledReasonKey?: DictKey;
};

export function applySlashAvailability(
  commands: readonly SlashCommand[],
  a: SlashAvailability,
): SlashOffer[] {
  const out: SlashOffer[] = [];
  for (const cmd of commands) {
    if (cmd.id === "image") {
      if (a.canGenerateImages) out.push(cmd);
      else if (a.imageFeatureAvailable) out.push({ ...cmd, disabledReasonKey: "input.imageMode.needsTools" });
      continue;
    }
    if (cmd.id === "video") {
      if (a.hosted || !a.videoFeatureAvailable) continue;
      if (a.canGenerateVideos) out.push(cmd);
      else out.push({ ...cmd, disabledReasonKey: "input.videoMode.needsTools" });
      continue;
    }
    out.push(cmd);
  }
  return out;
}
