// FIX61 — a per-turn hint (image mode, quick-ask) must never become a
// SECOND system message.
//
// Found live on the self-hosted qwen3.8-27b-fp8: the desktop sent
// `[system, system, user, …]` for a chat with image mode on, and the
// provider answered 400 "System message must be at the beginning" — on
// that turn and on every later turn of the same chat, because the toggle
// stays on. A new chat (no hint) worked. Measured against the provider
// directly: `[system, system, user]` → 400; the same hint folded into the
// leading system message → 200. Consecutive user turns and an empty
// assistant turn were both accepted, so the history was never the cause.
import { describe, expect, it } from "vitest";
import { foldSystemHint } from "../src/main/turn-coordinator";
import type { GatewayMessage } from "../src/main/gateway-client";

const HINT = "The user has turned on image mode.";

describe("foldSystemHint", () => {
  it("appends the hint to the leading system message instead of adding a second one", () => {
    const messages: GatewayMessage[] = [
      { role: "system", content: "You are SRAN." },
      { role: "user", content: "hi" },
      { role: "assistant", content: "Hello." },
      { role: "user", content: "ทำรูปแมว" },
    ];
    foldSystemHint(messages, HINT);

    expect(messages.map((m) => m.role)).toEqual(["system", "user", "assistant", "user"]);
    expect(messages.filter((m) => m.role === "system")).toHaveLength(1);
    expect(messages[0].content).toBe(`You are SRAN.\n\n${HINT}`);
    // the conversation itself is untouched
    expect(messages[3]).toEqual({ role: "user", content: "ทำรูปแมว" });
  });

  it("puts one system message in FRONT when the chain has none — never after a user turn", () => {
    const messages: GatewayMessage[] = [{ role: "user", content: "hi" }];
    foldSystemHint(messages, HINT);

    expect(messages.map((m) => m.role)).toEqual(["system", "user"]);
    expect(messages[0].content).toBe(HINT);
  });

  it("folds two hints (image mode + quick ask) into the same leading message", () => {
    const messages: GatewayMessage[] = [
      { role: "system", content: "S" },
      { role: "user", content: "hi" },
    ];
    foldSystemHint(messages, "one");
    foldSystemHint(messages, "two");

    expect(messages.filter((m) => m.role === "system")).toHaveLength(1);
    expect(messages[0].content).toBe("S\n\none\n\ntwo");
    expect(messages).toHaveLength(2);
  });

  it("never leaves a system message at index 1 (the shape the provider rejects)", () => {
    const messages: GatewayMessage[] = [
      { role: "system", content: "S" },
      { role: "user", content: "hi" },
    ];
    foldSystemHint(messages, HINT);
    expect(messages[1].role).not.toBe("system");
  });
});
