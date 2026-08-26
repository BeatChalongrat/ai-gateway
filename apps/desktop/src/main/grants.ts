// Folder grants (E4 §1 #4) — the host's list of folders the user granted,
// kept in userData/grants.json and sent with EVERY sidecar call. The sidecar
// re-validates each entry (FolderGrantValidator) and refuses a call without
// the list; this file is the host's half: the same validation rules, applied
// when a grant is added, so a bad folder never enters the list.
// No 'electron' import (the native picker is in index.ts); unit-testable.

import fs from "node:fs";
import path from "node:path";

export type GrantRejection = "Ok" | "NotAbsolute" | "Invalid" | "NotADirectory" | "DriveRoot" | "SystemFolder";

export interface GrantRules {
  /** Folders that may never be granted (the WPF app's and this app's state, Windows, Program Files, System). */
  forbiddenRoots: readonly string[];
}

function norm(p: string): string {
  return path.win32.normalize(p).replace(/[\\/]+$/, "");
}
function within(a: string, root: string): boolean {
  const x = norm(a).toLowerCase();
  const r = norm(root).toLowerCase();
  return x === r || x.startsWith(r + "\\");
}

export function validateGrant(raw: string, rules: GrantRules, exists: (p: string) => boolean = (p) => fs.existsSync(p) && fs.statSync(p).isDirectory()): { verdict: GrantRejection; canonical: string | null } {
  const value = (raw ?? "").trim();
  if (/^[A-Za-z]:[\\/]*$/.test(value)) return { verdict: "DriveRoot", canonical: null };
  if (!value || !path.win32.isAbsolute(value)) return { verdict: "NotAbsolute", canonical: null };
  if (/[<>"|?*]/.test(value)) return { verdict: "Invalid", canonical: null };
  const canonical = norm(value);
  if (/^[A-Za-z]:$/.test(canonical) || /^[A-Za-z]:\\?$/.test(canonical)) return { verdict: "DriveRoot", canonical: null };
  for (const forbidden of rules.forbiddenRoots) {
    if (forbidden && within(canonical, forbidden)) return { verdict: "SystemFolder", canonical: null };
  }
  if (!exists(canonical)) return { verdict: "NotADirectory", canonical: null };
  return { verdict: "Ok", canonical };
}

export class GrantStore {
  private grants: string[] = [];
  constructor(private readonly filePath: string, private readonly rules: GrantRules) {
    try {
      if (fs.existsSync(filePath)) {
        const raw = JSON.parse(fs.readFileSync(filePath, "utf8")) as { grants?: unknown };
        const list = Array.isArray(raw.grants) ? raw.grants.filter((g): g is string => typeof g === "string") : [];
        // Re-validate on load: a hand-edited file cannot smuggle a forbidden root.
        this.grants = list.map((g) => validateGrant(g, rules)).filter((v) => v.verdict === "Ok").map((v) => v.canonical!);
      }
    } catch {
      this.grants = [];
    }
  }
  /** A snapshot — what travels with every call. */
  list(): readonly string[] {
    return [...this.grants];
  }
  add(raw: string): { verdict: GrantRejection; canonical: string | null } {
    const v = validateGrant(raw, this.rules);
    if (v.verdict !== "Ok") return v;
    if (!this.grants.some((g) => g.toLowerCase() === v.canonical!.toLowerCase())) {
      this.grants.push(v.canonical!);
      this.save();
    }
    return v;
  }
  remove(raw: string): boolean {
    const before = this.grants.length;
    const target = norm(raw).toLowerCase();
    this.grants = this.grants.filter((g) => g.toLowerCase() !== target);
    if (this.grants.length !== before) this.save();
    return this.grants.length !== before;
  }
  private save(): void {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify({ grants: this.grants }, null, 2));
  }
}

/** The forbidden roots on this machine — mirrors FolderGrantValidator.ForbiddenRoots plus this app's own data folder. */
export function defaultForbiddenRoots(env: NodeJS.ProcessEnv, ownDataDir: string): string[] {
  const appData = env["APPDATA"] ?? "";
  const localAppData = env["LOCALAPPDATA"] ?? "";
  const roots = [
    appData && path.win32.join(appData, "SRAN Desktop"),
    localAppData && path.win32.join(localAppData, "SRAN Desktop"),
    ownDataDir,
    env["SystemRoot"] ?? env["WINDIR"] ?? "",
    env["ProgramFiles"] ?? "",
    env["ProgramFiles(x86)"] ?? "",
    (env["SystemRoot"] ?? env["WINDIR"]) ? path.win32.join(env["SystemRoot"] ?? env["WINDIR"]!, "System32") : "",
  ];
  return roots.filter((r): r is string => !!r);
}
