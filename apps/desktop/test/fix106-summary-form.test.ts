// FIX106 — the desktop's copy of the summary request matches chat-edge's: it
// names the conversation's language, cuts each message at chat-edge's 500
// UTF-8 bytes on a character boundary, and renders a list value instead of
// dropping the summary. (Byte-level agreement: state-contract.test.ts. The
// verbatim form was measured and not shipped: scripts/probes/summary-recall.)
// FIX111: no desktop code sends this request any more — the server
// summarises (server-summariser.ts); the body builder stays for the probe.
import { describe, expect, it } from "vitest";

import {
  buildExtractionUserBody,
  conversationLanguageName,
  detectLanguage,
  emptyState,
  renderStateBlock,
} from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";

describe("FIX106 — the desktop's summary request", () => {
  it("names the conversation's language, as chat-edge does (the desktop body had no language line)", () => {
    const thai: GatewayMessage[] = [{ role: "user", content: "ช่วยวางแผนย้ายระบบหน่อย" }, { role: "assistant", content: "Sure" }];
    expect(buildExtractionUserBody(null, thai, "")).toContain("This conversation is in Thai. Write every value in the JSON in Thai");
    expect(conversationLanguageName([{ role: "user", content: "plan the move" }])).toBe("English");
    expect(detectLanguage("こんにちは")).toBe("ja");
    expect(detectLanguage("안녕")).toBe("ko");
    expect(detectLanguage("你好")).toBe("zh");
  });

  it("cuts a message at 500 UTF-8 bytes on a character boundary — 166 Thai characters, not 500", () => {
    const thai = buildExtractionUserBody(null, [{ role: "user", content: "ก".repeat(600) }], "");
    expect(thai.match(/ก/g)).toHaveLength(166);
    const emoji = buildExtractionUserBody(null, [{ role: "user", content: "😀".repeat(200) }], "");
    expect(Array.from(emoji.match(/\[user\] (.*)\.\.\. \[truncated\]/u)![1]!)).toHaveLength(125);
    expect(buildExtractionUserBody(null, [{ role: "user", content: "a".repeat(500) }], "")).not.toContain("[truncated]");
  });

  it("renders a summary value that is a list instead of dropping it", () => {
    const block = renderStateBlock({ ...emptyState("c"), structured_summary: { key_context: ["37 servers", "- step 3 moves the bucket"], progress: "agreed" } });
    expect(block).toContain("Key context: - 37 servers\n- step 3 moves the bucket");
    expect(block).toContain("Progress: agreed");
  });
});
