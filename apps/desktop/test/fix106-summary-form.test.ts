// FIX106 — the desktop's summary request now matches chat-edge's: it names the
// conversation's language, cuts each message at chat-edge's 500 UTF-8 bytes on a
// character boundary, asks with thinking off, and renders a list value instead
// of dropping the summary. (Byte-level agreement: state-contract.test.ts. The
// verbatim form was measured and not shipped: scripts/probes/summary-recall.)
import { describe, expect, it } from "vitest";

import { COMPACTION_EXTRACTION_PROMPT } from "../src/main/compaction-prompt";
import {
  GatewayStateExtractor,
  buildExtractionUserBody,
  conversationLanguageName,
  detectLanguage,
  emptyState,
  renderStateBlock,
} from "../src/main/conversation-state";
import type { CompletionRequest, GatewayMessage } from "../src/main/gateway-client";

const log = { info: () => {}, warn: () => {}, error: () => {}, attachFile: () => {} } as unknown as import("../src/main/logger").Logger;

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

  it("asks with thinking off and the shared prompt as the system message", async () => {
    let sent: CompletionRequest | null = null;
    const gateway = {
      async *streamCompletion(req: CompletionRequest) {
        sent = req;
        yield { kind: "content_delta" as const, delta: '{"current_goal":"g","structured_summary":{"key_context":"k"}}' };
        yield { kind: "done" as const, finish_reason: "stop" };
      },
    };
    const out = await new GatewayStateExtractor(gateway as never, log).extract({ conversationId: "c", model: "qwen3.8-27b-fp8", existing: null, messages: [{ role: "user", content: "hi" }], instructions: "", signal: new AbortController().signal });
    expect(out.ok).toBe(true);
    expect(sent!.reasoning).toEqual({ mode: "off", show_summary: false });
    expect(sent!.messages[0]!.content).toBe(COMPACTION_EXTRACTION_PROMPT);
  });

  it("renders a summary value that is a list instead of dropping it", () => {
    const block = renderStateBlock({ ...emptyState("c"), structured_summary: { key_context: ["37 servers", "- step 3 moves the bucket"], progress: "agreed" } });
    expect(block).toContain("Key context: - 37 servers\n- step 3 moves the bucket");
    expect(block).toContain("Progress: agreed");
  });
});
