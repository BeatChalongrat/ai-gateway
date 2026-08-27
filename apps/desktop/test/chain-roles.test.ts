// FIX28 §5.1 — pins the role sequence the desktop sends after three turns.
import { describe, expect, it } from "vitest";

import { appendAssistantReply, roleSequence } from "../src/main/chain";
import type { GatewayMessage } from "../src/main/gateway-client";

function turn(chain: GatewayMessage[], user: string, assistant: string): GatewayMessage[] {
  const messages: GatewayMessage[] = [{ role: "system", content: "sys" }, ...chain, { role: "user", content: user }];
  appendAssistantReply(messages, assistant);
  return messages.slice(1); // what saveLocalHistory persists (everything after the system message)
}

describe("desktop chain role sequence", () => {
  it("three turns → system, user, assistant, user, assistant, user, assistant", () => {
    let chain: GatewayMessage[] = [];
    chain = turn(chain, "1 บวก 1", "2");
    chain = turn(chain, "แล้ว 2 บวก 2", "4");
    chain = turn(chain, "ตอบแรกคืออะไร", "2 ครับ");
    expect(roleSequence([{ role: "system", content: "sys" }, ...chain])).toEqual([
      "system", "user", "assistant", "user", "assistant", "user", "assistant",
    ]);
    expect(chain[1]).toEqual({ role: "assistant", content: "2" });
  });

  it("a tool chain keeps its shape: user, assistant(tool_calls), tool, assistant(final)", () => {
    const messages: GatewayMessage[] = [
      { role: "system", content: "sys" },
      { role: "user", content: "list files" },
      { role: "assistant", content: null, tool_calls: [{ id: "c1", type: "function", function: { name: "list_dir", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "c1", name: "list_dir", content: "a.txt" },
    ];
    appendAssistantReply(messages, "There is one file: a.txt");
    expect(roleSequence(messages)).toEqual(["system", "user", "assistant", "tool", "assistant"]);
  });

  it("an empty answer (cancelled turn) appends nothing", () => {
    const messages: GatewayMessage[] = [{ role: "system", content: "sys" }, { role: "user", content: "hi" }];
    appendAssistantReply(messages, "   ");
    expect(roleSequence(messages)).toEqual(["system", "user"]);
  });
});
