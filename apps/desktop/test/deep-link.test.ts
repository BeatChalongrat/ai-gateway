// E2 — deep-link and intent parsing. Pins the ticket shape (ported from
// OAuthDeepLink.cs: hex 32–128, the "connector" exclusion, the length cap),
// the scheme binding, and that intents are bare "<scheme>://signin|cancel".
//
// LIMITS: pure parsing only. Whether a parsed ticket is REDEEMABLE depends on
// the in-memory nonce in SessionController.handleDeepLink — an unsolicited
// ticket is dropped there, and that path is exercised live, not here.
import { describe, expect, it } from "vitest";
import { parseIntent, parseLoginTicket } from "../src/main/deep-link";

const T = "a".repeat(40);

describe("parseLoginTicket", () => {
  it("accepts the callback shape on the configured scheme only", () => {
    expect(parseLoginTicket(`sran-dev://oauth/callback?ticket=${T}`, "sran-dev")).toBe(T);
    expect(parseLoginTicket(`sran://oauth/callback?ticket=${T}`, "sran-dev")).toBeNull();
    expect(parseLoginTicket(`SRAN-DEV://oauth/callback?ticket=${T}`, "sran-dev")).toBe(T);
  });

  it("rejects wrong host/path, connector variant, bad tickets, oversize", () => {
    expect(parseLoginTicket(`sran-dev://oauth/other?ticket=${T}`, "sran-dev")).toBeNull();
    expect(parseLoginTicket(`sran-dev://x/callback?ticket=${T}`, "sran-dev")).toBeNull();
    expect(parseLoginTicket(`sran-dev://oauth/callback?ticket=${T}&connector=1`, "sran-dev")).toBeNull();
    expect(parseLoginTicket("sran-dev://oauth/callback?ticket=ZZZZ", "sran-dev")).toBeNull();
    expect(parseLoginTicket("sran-dev://oauth/callback?ticket=abc", "sran-dev")).toBeNull();
    expect(parseLoginTicket("sran-dev://oauth/callback", "sran-dev")).toBeNull();
    expect(parseLoginTicket(`sran-dev://oauth/callback?ticket=${"a".repeat(3000)}`, "sran-dev")).toBeNull();
    expect(parseLoginTicket("", "sran-dev")).toBeNull();
    expect(parseLoginTicket(null, "sran-dev")).toBeNull();
    expect(parseLoginTicket("not a url", "sran-dev")).toBeNull();
  });
});

describe("parseIntent", () => {
  it("recognises exactly signin and cancel on the configured scheme", () => {
    expect(parseIntent("sran-dev://signin", "sran-dev")).toBe("signin");
    expect(parseIntent("sran-dev://cancel", "sran-dev")).toBe("cancel");
    expect(parseIntent("sran-dev://signin/", "sran-dev")).toBe("signin");
  });

  it("refuses anything else: other scheme, query, path, unknown intent", () => {
    expect(parseIntent("sran://signin", "sran-dev")).toBeNull();
    expect(parseIntent("sran-dev://signin?x=1", "sran-dev")).toBeNull();
    expect(parseIntent("sran-dev://signin/extra", "sran-dev")).toBeNull();
    expect(parseIntent("sran-dev://logout", "sran-dev")).toBeNull();
    expect(parseIntent("http://localhost:3002/signin", "sran-dev")).toBeNull();
    expect(parseIntent(`sran-dev://oauth/callback?ticket=${T}`, "sran-dev")).toBeNull();
  });
});
