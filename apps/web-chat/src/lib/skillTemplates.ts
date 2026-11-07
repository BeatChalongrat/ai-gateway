// FIX33 B6 — the user's skills as STARTER TEMPLATES: cards on the empty
// state and entries in the "/" menu. Until now "/" was only a shortcut into
// four fixed flows (slashCommands.ts) and a new chat offered four generic
// chips; a user who wrote a "weekly report" skill had to remember its trigger
// word. Now the skill IS the template: picking it drops its trigger phrase
// into the composer so the skill activates on send.
//
// Web reads chat-edge's /skills (WebSkill); the desktop reads the host's
// sidecar-backed list (SkillDto). Both map to one shape here.

import { api, type WebSkill } from "./api";
import { hostApi, type SkillDto } from "./hostApi";
import type { SlashCommand } from "./slashCommands";

export interface SkillTemplate {
  id: string;
  name: string;
  description: string;
  /** What lands in the composer when the template is picked. */
  insertText: string;
}

/** The composer text that activates a skill: its first trigger, else its name. */
export function skillInsertText(s: { name: string; triggers?: string[] | null }): string {
  const trigger = (s.triggers ?? []).map((t) => t.trim()).find(Boolean);
  return trigger ? `${trigger} ` : `${s.name.trim()}: `;
}

/** "/weekly-report" — lower-case, spaces and punctuation collapsed to dashes. */
export function skillKeyword(name: string): string {
  const k = name
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}]+/gu, "-") // \p{M}: Thai vowels/tone marks are combining marks
    .replace(/^-+|-+$/g, "");
  return k || "skill";
}

export function templatesFromWeb(skills: readonly WebSkill[]): SkillTemplate[] {
  return skills.filter((s) => s.enabled).map((s) => ({ id: s.id, name: s.name, description: s.description ?? "", insertText: skillInsertText(s) }));
}

export function templatesFromHost(skills: readonly SkillDto[]): SkillTemplate[] {
  return skills.filter((s) => s.isEnabled).map((s) => ({ id: s.id, name: s.name, description: s.description ?? "", insertText: skillInsertText(s) }));
}

/** Enabled skills as templates; any failure = no templates (never an error in the composer). */
export async function loadSkillTemplates(hosted: boolean): Promise<SkillTemplate[]> {
  try {
    if (hosted) return templatesFromHost(await hostApi.listSkills());
    return templatesFromWeb((await api.listSkills()).skills);
  } catch {
    return [];
  }
}

/** The "/" menu entries for the templates (after the fixed commands). */
export function skillSlashCommands(templates: readonly SkillTemplate[]): SlashCommand[] {
  return templates.map((s) => ({
    id: "skill",
    keyword: skillKeyword(s.name),
    labelKey: "slash.skill.hint",
    hintKey: "slash.skill.hint",
    label: s.name,
    hint: s.description || undefined,
    insertText: s.insertText,
    skillId: s.id,
  }));
}
