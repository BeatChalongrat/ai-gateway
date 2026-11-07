// AM10 F — what the dictionary's own guard could not see.
//
// dict.test.ts walks `modules` to say WHICH area a mismatch came from and to
// catch a key claimed twice. An area composed into the dictionary but left
// out of that list (fallback was) is read by neither check. And a few strings never went through a dictionary at all: option
// labels, a tooltip, a fallback sentence. A string the lint cannot see is
// pinned here by its source.

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ApiError, getApiErrorMessage } from "../api";

import { en, modules, th } from "./dict";

const ROOT = path.resolve(__dirname, "..", "..");
const read = (rel: string) => fs.readFileSync(path.resolve(ROOT, rel), "utf8");

describe("every area module is inside the per-module checks", () => {
  it("lists fallback with the rest", () => {
    expect(Object.keys(modules)).toContain("fallback");
  });

  it("the modules together hold exactly the composed dictionary — none is composed but unlisted", () => {
    const fromModules = Object.values(modules).flatMap((m) => Object.keys(m.en));
    expect([...fromModules].sort()).toEqual(Object.keys(en).sort());
  });

  it.each(Object.entries(modules))("module '%s' has the same keys in English and Thai", (_name, mod) => {
    expect(Object.keys(mod.th).sort()).toEqual(Object.keys(mod.en).sort());
  });

  it("no key is claimed by two modules", () => {
    const owner = new Map<string, string>();
    const twice: string[] = [];
    for (const [name, mod] of Object.entries(modules)) {
      for (const key of Object.keys(mod.en)) {
        if (owner.has(key)) twice.push(`${key} (${owner.get(key)} and ${name})`);
        owner.set(key, name);
      }
    }
    expect(twice).toEqual([]);
  });

  it("fallback stays inside its own prefix", () => {
    expect(Object.keys(modules.fallback.en).filter((k) => !k.startsWith("fallback."))).toEqual([]);
  });
});

describe("the sign-in hint", () => {
  it("is a neutral hint, not a demo address", () => {
    expect(en["dash.login.identifierPlaceholder"]).toBe("Username or e-mail");
    expect(th["dash.login.identifierPlaceholder"]).not.toMatch(/[A-Za-z@]/);
  });

  it("no dictionary value shows an example.com address", () => {
    const demo = Object.entries({ ...en }).concat(Object.entries({ ...th })).filter(([, v]) => /example\.com/i.test(v));
    expect(demo.map(([k]) => k)).toEqual([]);
  });
});

describe("the ending this client adds to a caller's sentence", () => {
  const tTh = (key: "state.errorTimedOut" | "state.errorNetwork") => th[key];
  const tEn = (key: "state.errorTimedOut" | "state.errorNetwork") => en[key];

  it("a request that timed out or never left the browser is said in the page's language", () => {
    const timedOut = new ApiError(0, { error: { message: "Request timed out" } });
    expect(getApiErrorMessage(timedOut, "บันทึกไม่สำเร็จ", tTh)).toBe(`บันทึกไม่สำเร็จ: ${th["state.errorTimedOut"]}`);
    expect(getApiErrorMessage(timedOut, "Save failed", tEn)).toBe("Save failed: request timed out");
    expect(getApiErrorMessage(new TypeError("fetch failed"), "บันทึกไม่สำเร็จ", tTh)).toBe(`บันทึกไม่สำเร็จ: ${th["state.errorNetwork"]}`);
  });

  it("a sentence the gateway wrote is shown as received", () => {
    const refused = new ApiError(400, { error: { message: "provider returned HTTP 401: invalid key" } });
    expect(getApiErrorMessage(refused, "บันทึกไม่สำเร็จ", tTh)).toBe("provider returned HTTP 401: invalid key");
  });
});

describe("source pins — English a person reads that never went through t()", () => {
  const PINS: Array<{ file: string; forbidden: string[] }> = [
    {
      file: "app/tenants/page.tsx",
      forbidden: ['<option value="global">Global</option>', ">active</option>", ">suspended</option>", ">deleted</option>", "{row.status}"],
    },
    {
      file: "app/projects/_components/ProjectDetailModal.tsx",
      forbidden: ['<option value="admin">Admin</option>', '<option value="member">Member</option>', '<option value="viewer">Viewer</option>'],
    },
    { file: "components/ThemeToggle.tsx", forbidden: ["`Switch to ${"] },
    { file: "app/models/page.tsx", forbidden: ['"Save failed"', '"save failed"'] },
    { file: "app/routing/page.tsx", forbidden: ['model || "unknown"'] },
    { file: "app/ai-review/page.tsx", forbidden: [">{p}</button>"] },
    // a date the page words itself follows the page's language, not a fixed English one
    { file: "app/audit-log/page.tsx", forbidden: ['"en-US"'] },
    { file: "app/usage/page.tsx", forbidden: ['"en-US"'] },
    { file: "app/portal-users/page.tsx", forbidden: ['"en-US"'] },
  ];
  it.each(PINS)("$file", ({ file, forbidden }) => {
    const src = read(file);
    for (const f of forbidden) expect(src.includes(f), `${file} still has ${f}`).toBe(false);
  });

  it("the words those places now use exist in both languages", () => {
    const keys = [
      "tenancy.region.global",
      "tenancy.status.active",
      "tenancy.status.suspended",
      "tenancy.role.admin",
      "tenancy.role.member",
      "tenancy.role.viewer",
      "app.theme.toDark",
      "app.theme.toLight",
      "infra.routing.modelUnknown",
      "infra.media.saveFailed",
      "infra.models.saveFailed",
      "obs.air.providerCustom",
      "state.errorTimedOut",
      "state.errorNetwork",
    ];
    for (const k of keys) {
      expect((en as Record<string, string>)[k], `en.${k}`).toBeTruthy();
      expect((th as Record<string, string>)[k], `th.${k}`).toBeTruthy();
      expect((th as Record<string, string>)[k], `th.${k} is still English`).not.toBe((en as Record<string, string>)[k]);
    }
  });
});
