// FIX106 — the summary is ONE contract across two trees.
//
// chat-edge's testdata/state_contract.json records, for fixed inputs, the exact
// extraction request body and the exact <conversation_state> block chat-edge
// produces (state_contract_test.go checks Go against it). This test checks the
// desktop against the same file. Before FIX106 the two bodies already differed:
// no language line here, a different cut-off unit, a different key order.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { type ConversationStateRecord, buildExtractionUserBody, emptyState, renderStateBlock } from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";

const FILE = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "testdata", "state_contract.json");

interface ContractState {
  current_goal?: string | null;
  structured_summary?: Record<string, unknown>;
  confirmed_decisions?: Record<string, unknown>[];
  active_artifacts?: Record<string, unknown>[];
  open_tasks?: Record<string, unknown>[];
  rejected_approaches?: Record<string, unknown>[];
  unresolved_questions?: Record<string, unknown>[];
}
interface Contract {
  bodies: { name: string; instructions: string; existing: ContractState | null; messages: { role: "user" | "assistant"; content: string }[]; body: string }[];
  renders: { name: string; state: ContractState; block: string }[];
}

const contract = JSON.parse(fs.readFileSync(FILE, "utf8")) as Contract;

function record(s: ContractState | null): ConversationStateRecord | null {
  if (!s) return null;
  return {
    ...emptyState("c"),
    current_goal: s.current_goal ?? null,
    structured_summary: s.structured_summary ?? {},
    confirmed_decisions: s.confirmed_decisions ?? [],
    active_artifacts: s.active_artifacts ?? [],
    open_tasks: s.open_tasks ?? [],
    rejected_approaches: s.rejected_approaches ?? [],
    unresolved_questions: s.unresolved_questions ?? [],
  };
}

describe("FIX106 — the desktop writes the summary request chat-edge writes", () => {
  it("the contract file has recorded outputs to compare against", () => {
    expect(contract.bodies.length).toBeGreaterThanOrEqual(2);
    expect(contract.renders.length).toBeGreaterThanOrEqual(2);
  });
  for (const b of contract.bodies) {
    it(`body: ${b.name}`, () => {
      expect(b.body, "run chat-edge's TestFIX106_StateContract with UPDATE_STATE_CONTRACT=1").not.toBe("");
      const messages: GatewayMessage[] = b.messages.map((m) => ({ role: m.role, content: m.content }));
      expect(buildExtractionUserBody(record(b.existing), messages, b.instructions)).toBe(b.body);
    });
  }
});

describe("FIX106 — the desktop renders the summary block chat-edge renders", () => {
  for (const r of contract.renders) {
    it(`block: ${r.name}`, () => {
      expect(r.block).not.toBe("");
      expect(renderStateBlock(record(r.state)!)).toBe(r.block);
    });
  }
});
