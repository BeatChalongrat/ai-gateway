// E2 — deep-link and intent parsing. Pins the ticket shape (ported from
// OAuthDeepLink.cs: hex 32–128, the "connector" exclusion, the length cap),
// the scheme binding, and that intents are bare "<scheme>://signin|cancel".
//
// LIMITS: pure parsing only. Whether a parsed ticket is REDEEMABLE depends on
// the in-memory nonce in SessionController.handleDeepLink — an unsolicited
// ticket is dropped there, and that path is exercised live, not here.
import { describe, expect, it } from "vitest";
import { MAX_PREFILL_CHARS, filePathOf, parseAppLink, parseIntent, parseLoginTicket, prefillOf } from "../src/main/deep-link";

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

// ── FIX34 Part B: app links (Claude's shape: scheme + web host + web route) ──
describe("parseAppLink", () => {
  const HOST = "localhost:3002";
  it("parses /new, /new?q=, /chat/{id}, /project/{id} on the app scheme + web host", () => {
    expect(parseAppLink("sran://localhost:3002/new", "sran", HOST)).toEqual({ kind: "new", q: null, file: null });
    expect(parseAppLink("sran://localhost:3002/new/", "sran", HOST)).toEqual({ kind: "new", q: null, file: null });
    expect(parseAppLink("sran://localhost:3002/new?q=hello%20world", "sran", HOST)).toEqual({ kind: "new", q: "hello world", file: null });
    expect(parseAppLink("sran://localhost:3002/new?q=%E0%B8%AA%E0%B8%A7%E0%B8%B1%E0%B8%AA%E0%B8%94%E0%B8%B5", "sran", HOST)).toEqual({ kind: "new", q: "สวัสดี", file: null });
    expect(parseAppLink("sran://localhost:3002/chat/conv_01HZX-abc", "sran", HOST)).toEqual({ kind: "chat", id: "conv_01HZX-abc" });
    expect(parseAppLink("sran://localhost:3002/project/p1", "sran", HOST)).toEqual({ kind: "project", id: "p1" });
    // case-insensitive scheme + host (the OS may upper-case either)
    expect(parseAppLink("SRAN://LOCALHOST:3002/new", "sran", HOST)).toEqual({ kind: "new", q: null, file: null });
  });

  it("carries the Explorer-menu file path (raw backslashes, spaces) and drops an implausible one", () => {
    expect(parseAppLink("sran://localhost:3002/new?file=C:\\Users\\Beat Beat\\Desktop\\a b.txt", "sran", HOST)).toEqual({
      kind: "new", q: null, file: "C:\\Users\\Beat Beat\\Desktop\\a b.txt",
    });
    expect(parseAppLink("sran://localhost:3002/new?file=..%5C..%5Cetc%5Cpasswd", "sran", HOST)).toEqual({ kind: "new", q: null, file: null });
    expect(parseAppLink("sran://localhost:3002/new?file=%2Fetc%2Fpasswd", "sran", HOST)).toEqual({ kind: "new", q: null, file: null });
  });

  it("refuses the wrong scheme, the wrong host, the oauth host, fragments, extra segments, bad ids", () => {
    expect(parseAppLink("sran-dev://localhost:3002/new", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://evil.example/new?q=x", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://oauth/new", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/new#frag", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/new/extra", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/chat", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/chat/a/b", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/chat/..%2F..", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/chat/" + "x".repeat(129), "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/settings", "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/", "sran", HOST)).toBeNull();
    expect(parseAppLink("http://localhost:3002/new", "sran", HOST)).toBeNull();
    expect(parseAppLink("not a url", "sran", HOST)).toBeNull();
    expect(parseAppLink("", "sran", HOST)).toBeNull();
    expect(parseAppLink(null, "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://localhost:3002/new?q=" + "a".repeat(40_000), "sran", HOST)).toBeNull(); // over the argv cap
  });

  it("the OAuth callback and intents are NOT app links (and vice versa)", () => {
    expect(parseAppLink(`sran://oauth/callback?ticket=${T}`, "sran", HOST)).toBeNull();
    expect(parseAppLink("sran://signin", "sran", HOST)).toBeNull();
    expect(parseLoginTicket("sran://localhost:3002/new?q=x", "sran")).toBeNull();
    expect(parseIntent("sran://localhost:3002/new", "sran")).toBeNull();
    // the switch to sran:// keeps the OAuth shapes working unchanged
    expect(parseLoginTicket(`sran://oauth/callback?ticket=${T}`, "sran")).toBe(T);
    expect(parseIntent("sran://signin", "sran")).toBe("signin");
    expect(parseIntent("sran://cancel", "sran")).toBe("cancel");
  });
});

describe("prefillOf (q) — prefilled, never sent, cut at 14,000", () => {
  it("cuts at MAX_PREFILL_CHARS code points, not bytes", () => {
    expect(MAX_PREFILL_CHARS).toBe(14_000);
    expect(prefillOf("a".repeat(20_000))!.length).toBe(14_000);
    const thai = "ก".repeat(15_000);
    expect(Array.from(prefillOf(thai)!).length).toBe(14_000);
    expect(prefillOf("a".repeat(14_000))!.length).toBe(14_000);
  });
  it("keeps newlines/tabs, strips other control characters, empties to null", () => {
    expect(prefillOf("line1\nline2\tx")).toBe("line1\nline2\tx");
    expect(prefillOf("a\u0000b\u001bc")).toBe("abc");
    expect(prefillOf("   ")).toBeNull();
    expect(prefillOf("")).toBeNull();
    expect(prefillOf(null)).toBeNull();
  });
});

describe("filePathOf — deny-by-shape", () => {
  it("accepts an absolute Windows file path only", () => {
    expect(filePathOf("C:\\Users\\x\\report.pdf")).toBe("C:\\Users\\x\\report.pdf");
    expect(filePathOf("d:\\a\\b c\\ไฟล์.txt")).toBe("d:\\a\\b c\\ไฟล์.txt");
    expect(filePathOf("C:\\Users\\x\\..\\y\\a.txt")).toBeNull();
    expect(filePathOf("C:\\Users\\x\\")).toBeNull();
    expect(filePathOf("\\\\server\\share\\a.txt")).toBeNull();
    expect(filePathOf("/etc/passwd")).toBeNull();
    expect(filePathOf("C:report.pdf")).toBeNull();
    expect(filePathOf("C:\\a*.txt")).toBeNull();
    expect(filePathOf("C:\\a\u0000.txt")).toBeNull();
    expect(filePathOf("C:\\" + "a".repeat(2000))).toBeNull();
    expect(filePathOf(null)).toBeNull();
  });
});
