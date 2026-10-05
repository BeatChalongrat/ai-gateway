#!/usr/bin/env node
// AM10 — the agent icons as one set: every package's own icon.svg, painted the
// way the apps paint it (the file is only a shape — a mask — and the colour is
// the package's accent for the theme), at 32 px and at full size, light and dark.
//
//   node scripts/probes/AM10/icons.js            → results/icons/sheet.png (+ light.png, dark.png)
//
// It reads the packages in the checkout, so it shows what the next runner
// image will register. No network, no stack.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const PKGS = path.join(ROOT, "backend", "agent_market", "packages");
const OUT = path.join(__dirname, "results", "icons");
const pw = () => require(path.join(ROOT, "apps", "web-chat", "node_modules", "playwright-core"));

function agents() {
  const out = [];
  for (const dir of fs.readdirSync(PKGS).sort()) {
    const mf = path.join(PKGS, dir, "agent.json");
    if (!fs.existsSync(mf)) continue;
    const m = JSON.parse(fs.readFileSync(mf, "utf8"));
    if (!m.icon_file) throw new Error(`${dir}: no icon_file`);
    out.push({ name: m.short_name || m.name, id: m.id, accent: m.accent, svg: fs.readFileSync(path.join(PKGS, dir, m.icon_file), "utf8").trim() });
  }
  return out;
}

// the same paint as apps/web-chat …/agent-market/ui.tsx AgentTile and admin-ui's
function tile(a, theme, size) {
  const colour = a.accent[theme];
  const mask = `url('data:image/svg+xml,${encodeURIComponent(a.svg).replace(/'/g, "%27")}')`;
  const glyph = Math.round(size * 0.58);
  const radius = size >= 40 ? 12 : 8;
  return `<span class="tile" style="width:${size}px;height:${size}px;border-radius:${radius}px;background:${colour}24">
    <span style="width:${glyph}px;height:${glyph}px;background:${colour};-webkit-mask:${mask} center/contain no-repeat;mask:${mask} center/contain no-repeat"></span></span>`;
}

function panel(list, theme) {
  const bg = theme === "light" ? "#ffffff" : "#17181c";
  const fg = theme === "light" ? "#1c1d21" : "#ececf1";
  const sub = theme === "light" ? "#6b6f76" : "#9da1ab";
  const cell = (a) => `<div class="cell">${tile(a, theme, 32)}${tile(a, theme, 96)}
      <div class="name" style="color:${fg}">${a.name}</div><div class="id" style="color:${sub}">${a.id} · ${a.accent[theme]}</div></div>`;
  return `<section id="${theme}" style="background:${bg}"><h2 style="color:${sub}">${theme} · 32 px and 96 px</h2><div class="row">${list.map(cell).join("")}</div></section>`;
}

(async () => {
  const list = agents();
  fs.mkdirSync(OUT, { recursive: true });
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;font-family:'Segoe UI','Leelawadee UI',sans-serif}
    section{padding:28px 32px} h2{font-size:12px;font-weight:500;margin:0 0 18px;text-transform:uppercase;letter-spacing:.06em}
    .row{display:flex;gap:28px} .cell{width:190px}
    .tile{display:inline-flex;align-items:center;justify-content:center;vertical-align:bottom;margin-right:14px}
    .name{font-size:13px;font-weight:600;margin-top:12px} .id{font-size:11px;margin-top:2px}
  </style>${panel(list, "light")}${panel(list, "dark")}`;
  const browser = await pw().chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1180, height: 520 }, deviceScaleFactor: 2 });
  await page.setContent(html);
  await page.screenshot({ path: path.join(OUT, "sheet.png"), fullPage: true });
  for (const theme of ["light", "dark"]) await page.locator(`#${theme}`).screenshot({ path: path.join(OUT, `${theme}.png`) });
  await browser.close();
  console.log(`${list.length} icons → ${path.relative(ROOT, path.join(OUT, "sheet.png"))}`);
})().catch((e) => { console.error(e); process.exit(1); });
