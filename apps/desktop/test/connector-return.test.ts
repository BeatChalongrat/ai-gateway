// A connector's consent flow ends in the system browser on a page that
// bounces to <scheme>://oauth/callback?connector=<id>&status=ok|error
// (backend/app/api/routes/portal_connectors.py). The app used to come forward
// and drop that link — whatever page it was on stayed, and nothing said the
// mailbox was connected. It now lands on its Connectors page, which says so.
//
// LIMITS: pure parsing and the URL it leads to, plus a pin of the wiring in
// index.ts (it imports 'electron'). That the window really loads the page is
// walked on the installed app, not here.
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { connectorsPageUrl, parseConnectorReturn, parseLoginTicket } from "../src/main/deep-link";

describe("parseConnectorReturn", () => {
  it("reads the connector, the outcome and its coarse reason", () => {
    expect(parseConnectorReturn("sran://oauth/callback?connector=mail&status=ok", "sran")).toEqual({ connector: "mail", status: "ok", reason: null });
    expect(parseConnectorReturn("sran://oauth/callback?connector=mail&status=error&reason=provider_denied", "sran")).toEqual({
      connector: "mail",
      status: "error",
      reason: "provider_denied",
    });
    expect(parseConnectorReturn("SRAN://oauth/callback?connector=gdrive&status=ok", "sran")?.connector).toBe("gdrive");
  });

  it("is bound to the app's own scheme, host and path", () => {
    expect(parseConnectorReturn("sran-dev://oauth/callback?connector=mail&status=ok", "sran")).toBeNull();
    expect(parseConnectorReturn("sran://oauth/other?connector=mail&status=ok", "sran")).toBeNull();
    expect(parseConnectorReturn("sran://localhost:3002/callback?connector=mail&status=ok", "sran")).toBeNull();
  });

  it("takes nothing that is not the shape the broker writes", () => {
    for (const bad of [
      "sran://oauth/callback?connector=mail", // no outcome
      "sran://oauth/callback?connector=mail&status=maybe",
      "sran://oauth/callback?connector=Mail%20Box&status=ok",
      "sran://oauth/callback?connector=../../x&status=ok",
      "sran://oauth/callback?connector=" + "a".repeat(41) + "&status=ok",
      "sran://oauth/callback?status=ok",
      "sran://oauth/callback?connector=mail&status=ok#frag",
      "sran://oauth/callback?connector=mail&status=ok&ticket=" + "a".repeat(40), // never both
      "sran://oauth/callback?connector=mail&status=ok&" + "x=".padEnd(3000, "y"),
      "",
      "not a url",
    ]) {
      expect(parseConnectorReturn(bad, "sran"), bad).toBeNull();
    }
    expect(parseConnectorReturn(null, "sran")).toBeNull();
  });

  it("keeps a reason only when it is a code, never text", () => {
    expect(parseConnectorReturn("sran://oauth/callback?connector=mail&status=error&reason=user%20said%20no", "sran")?.reason).toBeNull();
    expect(parseConnectorReturn("sran://oauth/callback?connector=mail&status=error&reason=<script>", "sran")?.reason).toBeNull();
  });

  it("is never mistaken for a sign-in ticket, nor a sign-in ticket for it", () => {
    expect(parseLoginTicket("sran://oauth/callback?connector=mail&status=ok", "sran")).toBeNull();
    expect(parseConnectorReturn("sran://oauth/callback?ticket=" + "a".repeat(40), "sran")).toBeNull();
  });
});

describe("where the app lands", () => {
  it("on its own Connectors page, told which connector and how it went", () => {
    expect(connectorsPageUrl("https://chat.example.test", { connector: "mail", status: "ok", reason: null })).toBe(
      "https://chat.example.test/chat/connectors?connected=mail&status=ok",
    );
    expect(connectorsPageUrl("http://localhost:3002", { connector: "mail", status: "error", reason: "provider_denied" })).toBe(
      "http://localhost:3002/chat/connectors?connected=mail&status=error&reason=provider_denied",
    );
  });

  it("the link handler navigates there (index.ts)", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "src", "main", "index.ts"), "utf8");
    const handler = src.slice(src.indexOf("function handleDeepLink("), src.indexOf("function deepLinkHost("));
    expect(handler).toContain("parseConnectorReturn(url, config.protocolScheme)");
    expect(handler).toContain("navigateMain(connectorsPageUrl(pageOrigin(), ");
    // before the app-link parser, which knows no "oauth" host and would drop it
    expect(handler.indexOf("parseConnectorReturn(")).toBeLessThan(handler.indexOf("parseAppLink("));
  });
});
