// FIX65 — one failed image turn must not poison the chat (desktop path).
//
// Measured before the fix: the admin turns a model's vision switch off
// mid-session; the host's model list still says "vision", the gateway
// refuses the image turn (model_no_vision_support), the image stays in
// run.messages, and every later turn dies the same way. Pinned here:
//   • a model the list says is blind gets NO image blocks (they become a
//     note the model can read) and the page is told;
//   • on the refusal with a stale list, the list is re-fetched and the
//     turn is resent once without the images;
//   • the same run retries at most once, and never re-sends a turn that
//     already showed text.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { IMAGE_NOT_SENT_NOTE, stripImageBlocks } from "../src/main/attachments";
import type { GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

const IMAGE_TURN = (): GatewayMessage[] => [
  { role: "system", content: "sys" },
  { role: "user", content: [{ type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } }, { type: "text", text: "what colour?" }] },
];

describe("FIX65 — stripImageBlocks", () => {
  it("replaces every image with the note, keeps the text, leaves other messages alone", () => {
    const msgs = IMAGE_TURN();
    msgs.push({ role: "assistant", content: "I cannot see it." });
    expect(stripImageBlocks(msgs)).toBe(1);
    expect(typeof msgs[1]!.content).toBe("string");
    expect(msgs[1]!.content).toContain(IMAGE_NOT_SENT_NOTE);
    expect(msgs[1]!.content).toContain("what colour?");
    expect(msgs[0]!.content).toBe("sys");
    expect(msgs[2]!.content).toBe("I cannot see it.");
    expect(stripImageBlocks(msgs)).toBe(0);
  });
});

function gateway(opts: { blindFromStart: boolean; blindAfterRefresh: boolean }) {
  const requests: { images: number }[] = [];
  let listCalls = 0;
  return {
    requests,
    listCalls: () => listCalls,
    async listModels() {
      listCalls++;
      const vision = listCalls === 1 ? !opts.blindFromStart : !opts.blindAfterRefresh;
      return new Map([["qwen3.8-27b-fp8", { tools: true, vision }]]);
    },
    async *streamCompletion(req: { messages: GatewayMessage[] }): AsyncGenerator<Ev> {
      const images = req.messages.reduce((n, m) => n + (Array.isArray(m.content) ? (m.content as { type: string }[]).filter((b) => b.type === "image_url").length : 0), 0);
      requests.push({ images });
      if (images > 0) {
        yield { kind: "error", code: "model_no_vision_support", message: "The selected model cannot read images." };
        return;
      }
      yield { kind: "content_delta", delta: "OK" };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

async function run(gw: ReturnType<typeof gateway>) {
  lines.length = 0;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix65-"));
  const events: { event: string; data: Record<string, unknown> }[] = [];
  const bridge = { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => events.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never, gw as never, {} as never, {} as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    log,
  );
  await (co as unknown as { refreshAcceptedModels: (s: AbortSignal) => Promise<void> }).refreshAcceptedModels(new AbortController().signal);
  const messages = IMAGE_TURN();
  let text = "";
  const outcome = await (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
    conversationId: "conv-1", model: "qwen3.8-27b-fp8", messages, signal: new AbortController().signal,
    maxIterations: 4, maxMs: 10_000, tools: [], parentToolCallId: null, onText: (t: string) => { text += t; },
  });
  return { outcome, text, events, gw, messages };
}

describe("FIX65 — desktop never sends images to a model the list says is blind", () => {
  it("a blind model per the list: images stripped before the first request, page told, turn answers", async () => {
    const r = await run(gateway({ blindFromStart: true, blindAfterRefresh: true }));
    expect(r.outcome).toBe("done");
    expect(r.text).toBe("OK");
    expect(r.gw.requests).toEqual([{ images: 0 }]);
    expect(r.events.some((e) => e.event === "image_notice")).toBe(true);
    expect(r.events.some((e) => e.event === "error")).toBe(false);
    expect(lines.some((l) => /cannot read images per the catalogue/.test(l))).toBe(true);
  });

  it("a stale list: the refusal triggers a re-fetch and one resend without the images", async () => {
    const r = await run(gateway({ blindFromStart: false, blindAfterRefresh: true }));
    expect(r.outcome).toBe("done");
    expect(r.text).toBe("OK");
    expect(r.gw.requests).toEqual([{ images: 1 }, { images: 0 }]);
    expect(r.gw.listCalls()).toBe(2);
    expect(r.events.some((e) => e.event === "error")).toBe(false);
    expect(r.events.some((e) => e.event === "image_notice")).toBe(true);
    expect(lines.some((l) => /resending conv=conv-1 without 1 image block/.test(l))).toBe(true);
  });

  it("the fresh list still says the model sees: the error is surfaced, nothing resent", async () => {
    const r = await run(gateway({ blindFromStart: false, blindAfterRefresh: false }));
    expect(r.outcome).toBe("error");
    expect(r.gw.requests).toEqual([{ images: 1 }]);
    expect(r.events.some((e) => e.event === "error" && e.data.code === "model_no_vision_support")).toBe(true);
  });
});
