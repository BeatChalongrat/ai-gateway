// FIX48 — one-off: what does the chat DOM actually expose? The first UX pass
// used guessed selectors and produced harness failures that would have read as
// product failures. This dumps the real hooks so ux_web.js can target them.
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const USER = process.env.PROBE_USER || "f1848000-0000-4000-8000-000000000048";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c",
    `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "fix48@local", type: "portal", token_use: "access",
    tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "th-TH" });
  await ctx.addCookies([{ name: "portal_access_token", value: token(), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);

  const before = await page.evaluate(() => {
    const attrs = new Set();
    document.querySelectorAll("*").forEach((e) => [...e.attributes].forEach((a) => { if (/^data-/.test(a.name)) attrs.add(a.name + "=" + (a.value || "").slice(0, 20)); }));
    return { dataAttrs: [...attrs].slice(0, 50) };
  }).catch(() => ({ dataAttrs: [] }));
  console.log("IDLE data-attrs:", JSON.stringify(before.dataAttrs, null, 1));

  const box = page.locator("textarea, [contenteditable=true]").first();
  await box.click();
  await box.fill("นับ 1 ถึง 20 ช้า ๆ");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(2500);

  const during = await page.evaluate(() => {
    const attrs = new Set();
    document.querySelectorAll("*").forEach((e) => [...e.attributes].forEach((a) => { if (/^data-/.test(a.name)) attrs.add(a.name); }));
    const buttons = [...document.querySelectorAll("button")].map((b) => ({
      aria: b.getAttribute("aria-label"), title: b.getAttribute("title"),
      text: (b.textContent || "").trim().slice(0, 28),
    })).filter((b) => b.aria || b.title || b.text);
    return { attrs: [...attrs], buttons: buttons.slice(0, 40) };
  });
  console.log("\nSTREAMING data-attrs:", JSON.stringify(during.attrs));
  console.log("\nSTREAMING buttons:", JSON.stringify(during.buttons, null, 1).slice(0, 3000));

  await page.waitForTimeout(15000);
  const after = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll("button")].map((b) => ({
      aria: b.getAttribute("aria-label"), title: b.getAttribute("title"),
      text: (b.textContent || "").trim().slice(0, 28),
    })).filter((b) => b.aria || b.title || b.text);
    return { buttons: buttons.slice(0, 50), bodyTail: document.body.innerText.slice(-400) };
  });
  console.log("\nSETTLED buttons:", JSON.stringify(after.buttons, null, 1).slice(0, 3500));
  console.log("\nbody tail:", after.bodyTail);
  await browser.close();
})();
