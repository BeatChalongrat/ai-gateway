// AM7 phase 4 — the chat app is for the people who use agents. Everything
// that affects anyone else (who may use Agent Market, budgets, spend,
// whether documents may leave, retention, publishing, the problems queue,
// other people's runs) lives in the admin app (admin-ui /agent-market). The
// chat app's Agent Market admin surface is gone — not hidden behind a role:
// no page, no route, no link, no publish button, no "open as admin" run
// view, no client method that reaches an admin endpoint. (The desktop app
// renders these same pages.)
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { amApi } from "./api";

const SRC = path.resolve(__dirname, "../..");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...sources(p));
    else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name)) out.push(p);
  }
  return out;
}

describe("the chat app has no Agent Market admin surface (AM7)", () => {
  it("has no admin page or route", () => {
    expect(fs.existsSync(path.join(SRC, "app", "chat", "agents", "admin"))).toBe(false);
    expect(fs.existsSync(path.join(SRC, "features", "agent-market", "admin"))).toBe(false);
  });

  it("has no client method that reaches an admin endpoint", () => {
    expect(Object.keys(amApi).filter((k) => /^admin/i.test(k))).toEqual([]);
  });

  it("no source links to, calls or renders any of it", () => {
    const refused = [
      /agent-market\/admin|\/chat\/agents\/admin/, // the page and its route
      /["'`]\/admin\//, // an admin endpoint
      /am-admin-link|am-detail-publish/, // the market's "จัดการ Agent Market" button, the agent page's publish button
      /asAdmin|as=admin|adminRunHref/, // someone else's run opened as an admin
    ];
    const hits: string[] = [];
    for (const f of [...sources(path.join(SRC, "features", "agent-market")), ...sources(path.join(SRC, "app", "chat", "agents"))]) {
      const text = fs.readFileSync(f, "utf8");
      for (const re of refused) {
        const m = re.exec(text);
        if (m) hits.push(`${path.relative(SRC, f)}: ${m[0]}`);
      }
    }
    expect(hits).toEqual([]);
  });
});
