// Upgrading an installed agent (AM2 phase G): what a new version changes,
// grouped the way a person checks it, and whether the upgrade took.
//
// The server runs the new version's self-test on this install and switches
// it only when that run passes and meets the package's own expectations.
// The run can read "succeeded" a moment before the switch (the runner
// finishes the run, then switches), and a passing run whose expectations
// miss leaves the install where it was — so the outcome is read from the
// install's version afterwards, never from the run status alone.

import type { VersionChange } from "../types";

export const CHANGE_GROUPS = ["manifest", "capability", "setting", "instructions"] as const;
export type ChangeGroup = (typeof CHANGE_GROUPS)[number];

export function groupChanges(changes: VersionChange[] | null | undefined): { what: ChangeGroup | string; items: VersionChange[] }[] {
  const by = new Map<string, VersionChange[]>();
  for (const c of changes ?? []) {
    const list = by.get(c.what) ?? [];
    list.push(c);
    by.set(c.what, list);
  }
  const known = CHANGE_GROUPS.filter((g) => by.has(g)).map((g) => ({ what: g, items: by.get(g)! }));
  const other = [...by.keys()].filter((k) => !(CHANGE_GROUPS as readonly string[]).includes(k)).map((k) => ({ what: k, items: by.get(k)! }));
  return [...known, ...other];
}

/** An instructions line as a diff line: "- removed" / "+ added". */
export function diffLine(c: VersionChange): { sign: "-" | "+"; text: string } {
  return c.after !== undefined && c.after !== null && (c.before === undefined || c.before === null || c.before === "")
    ? { sign: "+", text: c.after }
    : { sign: "-", text: c.before ?? "" };
}

export const TERMINAL = new Set(["succeeded", "failed", "cancelled", "awaiting_input"]);

export type UpgradeOutcome = { kind: "upgraded"; version: string } | { kind: "kept"; version: string; tried: string };

/** Taken or not, from the version the install is on after its self-test. */
export function upgradeOutcome(target: string, versionAfter: string): UpgradeOutcome {
  return versionAfter === target ? { kind: "upgraded", version: versionAfter } : { kind: "kept", version: versionAfter, tried: target };
}
