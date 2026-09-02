#!/usr/bin/env node
// FIX43 D2 — three divider treatments for the tasks page, both themes, on the
// LIVE desktop page (DOM edit, no rebuild) so the owner can pick by eye.
//
//   node scripts/fix43/divider-variants.js
//
//   keep    — as shipped: `border-t border-border/60`. That class does not
//             exist in the built CSS (Tailwind drops opacity modifiers on
//             var() colours), so the line is preflight's #e5e7eb.
//   subtle  — `border-t border-border-subtle`: the token the /60 was reaching
//             for (0.6 × 12 % ≈ 8 % white / 6 % black).
//   space   — no line; the section keeps its top margin and the heading does
//             the separating (the Settings page's own convention).
"use strict";
const path = require("node:path");
const { chromium } = require(path.join(__dirname, "..", "..", "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(__dirname, "..", "..", "docs", "fix43");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const VARIANTS = {
  keep: "mt-12 border-t border-border/60 pt-10",
  subtle: "mt-12 border-t border-border-subtle pt-10",
  space: "mt-14 pt-0",
};

(async () => {
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const page = browser.contexts().flatMap((c) => c.pages()).find((p) => /localhost:3002/.test(p.url()));
  try {
    for (const theme of ["dark", "light"]) {
      await page.goto("http://localhost:3002/chat/tasks", { waitUntil: "load" });
      await page.evaluate((t) => localStorage.setItem("sran-chat-theme", t), theme);
      await page.reload({ waitUntil: "load" });
      await wait(1800);
      for (const [name, cls] of Object.entries(VARIANTS)) {
        const color = await page.evaluate((c) => {
          const el = [...document.querySelectorAll("section")].find((s) => /border-t|mt-1[24]/.test(s.className));
          el.className = c;
          document.querySelectorAll(".overflow-y-auto").forEach((x) => x.scrollTo(0, 340));
          return getComputedStyle(el).borderTopColor + " / " + getComputedStyle(el).borderTopWidth;
        }, cls);
        await wait(250);
        const p = path.join(OUT, `D2-divider-${name}-${theme}.png`);
        await page.screenshot({ path: p });
        console.log(name.padEnd(7), theme.padEnd(6), color, "→", path.relative(process.cwd(), p));
      }
    }
    await page.evaluate(() => localStorage.setItem("sran-chat-theme", "dark"));
    await page.reload();
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
