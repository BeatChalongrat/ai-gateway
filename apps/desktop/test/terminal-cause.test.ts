// FIX118 — the desktop host's half of "how a turn ended".
//
// Pins: (1) the closed set to docs/contracts/terminal_cause.json, the same
// file chat-edge and the web page pin themselves to; (2) every code the
// host mints to a code the web mapper declares (apps/web-chat/src/lib/
// errorMessages.ts) and, where it is a gateway code, to the catalogue
// (backend/app/core/error_catalog.py) with the same sentence and verdict;
// (3) the classifier and the normaliser, case by case; (4) the gateway
// client's stream reader: EOF with nothing said is an error, a throw is a
// catalogue code with the raw text under details, `retryable` is read.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

import {
  CATALOGUE_MESSAGES,
  HOST_CODE_MAP,
  HOST_DECLARED_CODES,
  TERMINAL_CAUSES,
  TRANSPORT_CODES,
  classifyStreamFailure,
  endingForOutcome,
  eofWithoutTerminal,
  isTerminalCause,
  normalizeHostError,
} from "../src/main/terminal-cause";
import { GatewayClient, parseChunk } from "../src/main/gateway-client";
import type { Logger } from "../src/main/logger";

const REPO = path.join(__dirname, "..", "..", "..");
const CONTRACT = path.join(REPO, "docs", "contracts", "terminal_cause.json");
const CATALOGUE = path.join(REPO, "backend", "app", "core", "error_catalog.py");
const WEB_MAPPER = path.join(REPO, "apps", "web-chat", "src", "lib", "errorMessages.ts");

interface Contract {
  causes: string[];
  transport_codes: Record<string, { code: string; retryable: boolean }>;
}
const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8")) as Contract;
const hasCatalogue = fs.existsSync(CATALOGUE);
const catalogue = new Map<string, { message: string; retryable: boolean }>();
if (hasCatalogue) {
  for (const m of fs.readFileSync(CATALOGUE, "utf8").matchAll(/ErrorInfo\("([a-z_]+)",\s*"([^"]*)",\s*(True|False)\)/g)) {
    catalogue.set(m[1], { message: m[2], retryable: m[3] === "True" });
  }
}
const webDeclared = new Set<string>();
if (fs.existsSync(WEB_MAPPER)) {
  const src = fs.readFileSync(WEB_MAPPER, "utf8");
  const table = src.slice(src.indexOf("const ERROR_CODE_KEYS"), src.indexOf("/** The dictionary key for a code"));
  for (const m of table.matchAll(/^\s{2}([A-Za-z_]+):\s*"error\./gm)) webDeclared.add(m[1]);
}

describe("FIX118 — one closed set of terminal causes", () => {
  it("matches docs/contracts/terminal_cause.json exactly, in order", () => {
    expect([...TERMINAL_CAUSES]).toEqual(contract.causes);
    for (const c of contract.causes) expect(isTerminalCause(c)).toBe(true);
    expect(isTerminalCause("failed")).toBe(false);
    expect(isTerminalCause(undefined)).toBe(false);
  });

  it("mints the same transport codes the contract names", () => {
    for (const [k, v] of Object.entries(contract.transport_codes)) {
      expect(TRANSPORT_CODES[k as keyof typeof TRANSPORT_CODES]).toEqual(v);
    }
  });

  it.skipIf(!hasCatalogue)("every gateway code the host mints is a catalogue code with the catalogue's sentence and verdict", () => {
    for (const { code, retryable } of Object.values(TRANSPORT_CODES)) {
      if (code === "GENERATION_INTERRUPTED") continue; // chat-edge's own resume code
      const entry = catalogue.get(code);
      expect(entry, code).toBeDefined();
      expect(entry!.retryable).toBe(retryable);
      expect(CATALOGUE_MESSAGES[code]).toBe(entry!.message);
    }
  });

  it.skipIf(webDeclared.size === 0)("every code the host can put on the wire is one the web mapper declares", () => {
    const minted = new Set<string>([...Object.values(TRANSPORT_CODES).map((t) => t.code), ...Object.values(HOST_CODE_MAP).map((m) => m.code), ...HOST_DECLARED_CODES]);
    for (const code of minted) expect(webDeclared.has(code), `${code} is not in ERROR_CODE_KEYS`).toBe(true);
    // ...and the ones it used to mint are NOT (that is why they are mapped).
    for (const host of Object.keys(HOST_CODE_MAP)) expect(webDeclared.has(host), host).toBe(false);
  });
});

describe("FIX118 — classifying what the host observes", () => {
  it("EOF with nothing said is stream_interrupted / eof_without_terminal, retryable", () => {
    expect(eofWithoutTerminal()).toEqual({ code: "stream_interrupted", message: CATALOGUE_MESSAGES["stream_interrupted"], retryable: true, cause: "eof_without_terminal" });
  });

  it("a transport throw is stream_interrupted / disconnected with the raw text under details, never as the message", () => {
    const f = classifyStreamFailure(new Error("fetch failed"), { stalled: false });
    expect(f).toMatchObject({ code: "stream_interrupted", cause: "disconnected", retryable: true, details: { transport: "fetch failed" } });
    expect(f.message).toBe(CATALOGUE_MESSAGES["stream_interrupted"]);
    expect(f.message).not.toContain("fetch failed");
  });

  it("the idle watchdog keeps FIX80's declared code stream_stalled with cause timeout", () => {
    const f = classifyStreamFailure(new Error("stream_stalled"), { stalled: true, idleSeconds: 60 });
    expect(f).toMatchObject({ code: "stream_stalled", cause: "timeout", retryable: true });
    expect(f.message).toContain("60s");
  });

  it("host-only codes are rewritten to catalogue codes and keep the original under details", () => {
    const cases: [string, string, string][] = [
      ["network", "stream_interrupted", "disconnected"],
      ["host_exception", "unclassified_error", "error"],
      ["no_result", "unclassified_error", "error"],
      ["generation_interrupted", "GENERATION_INTERRUPTED", "interrupted"],
    ];
    for (const [host, code, cause] of cases) {
      const { frame, mapped } = normalizeHostError({ code: host, message: "raw host sentence" });
      expect(mapped).toBe(true);
      expect(frame.code).toBe(code);
      expect(frame.cause).toBe(cause);
      expect(frame.message).toBe(CATALOGUE_MESSAGES[code]);
      expect(frame.details).toMatchObject({ host_code: host, host_message: "raw host sentence" });
      expect(typeof frame.retryable).toBe("boolean");
    }
    expect(normalizeHostError({ code: "host_exception", message: "x" }).frame.retryable).toBe(false);
  });

  it("a gateway code passes through untouched (verdict and payload kept)", () => {
    const { frame, mapped } = normalizeHostError({ code: "provider_rate_limited", message: "slow down", retryable: true, details: { status: 429 } });
    expect(mapped).toBe(false);
    expect(frame).toEqual({ code: "provider_rate_limited", message: "slow down", retryable: true, details: { status: 429 }, cause: "error" });
  });

  it("the ending a run's outcome implies", () => {
    expect(endingForOutcome("done", null)).toEqual({ status: "completed", cause: "completed" });
    expect(endingForOutcome("paused", null)).toEqual({ status: "completed", cause: "completed" });
    expect(endingForOutcome("cancelled", null)).toEqual({ status: "cancelled", cause: "stopped_by_user" });
    expect(endingForOutcome("error", null)).toEqual({ status: "error", cause: "error" });
    expect(endingForOutcome("error", { cause: "eof_without_terminal", errorCode: "stream_interrupted" })).toEqual({ status: "error", cause: "eof_without_terminal", errorCode: "stream_interrupted" });
  });
});

describe("FIX118 — the gateway client's stream reader", () => {
  const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
  const keyStore = { load: () => "k", save: () => {}, clear: () => {} };

  function sseResponse(body: string): Response {
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }
  async function collect(res: Response | (() => Promise<Response>)) {
    const fetchImpl = typeof res === "function" ? res : async () => res;
    const orig = globalThis.fetch;
    globalThis.fetch = fetchImpl as typeof fetch;
    try {
      const gw = new GatewayClient("http://gw.test", () => "t", keyStore, log);
      const out: unknown[] = [];
      for await (const ev of gw.streamCompletion({ model: "m", messages: [{ role: "user", content: "hi" }] }, new AbortController().signal)) out.push(ev);
      return out as { kind: string; code?: string; cause?: string; retryable?: boolean; details?: Record<string, unknown>; delta?: string }[];
    } finally {
      globalThis.fetch = orig;
    }
  }

  it("EOF after deltas with no [DONE], no finish_reason and no error frame ends on stream_interrupted / eof_without_terminal — not done", async () => {
    const evs = await collect(sseResponse('data: {"choices":[{"delta":{"content":"half an "},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}\n\n'));
    expect(evs.map((e) => e.kind)).toEqual(["content_delta", "content_delta", "error"]);
    expect(evs[2]).toMatchObject({ code: "stream_interrupted", cause: "eof_without_terminal", retryable: true });
  });

  it("a finish_reason with the [DONE] line missing is still done", async () => {
    const evs = await collect(sseResponse('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'));
    expect(evs.map((e) => e.kind)).toEqual(["content_delta", "done"]);
  });

  it("[DONE] is done, as before", async () => {
    const evs = await collect(sseResponse('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'));
    expect(evs.map((e) => e.kind)).toEqual(["content_delta", "done"]);
  });

  it("a body that tears mid-read is stream_interrupted / disconnected with the raw text in details", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"a"},"finish_reason":null}]}\n\n'));
        c.error(new Error("terminated"));
      },
    });
    const evs = await collect(new Response(body, { status: 200 }));
    // Whether the delta before the tear is delivered depends on the stream
    // implementation; the ending does not.
    const last = evs[evs.length - 1];
    expect(last.kind).toBe("error");
    expect(last).toMatchObject({ code: "stream_interrupted", cause: "disconnected", retryable: true, details: { transport: "terminated" } });
    expect(last.code).not.toBe("network");
  });

  it("an in-stream error frame keeps the gateway's code, verdict and payload", () => {
    const evs = parseChunk('{"error":{"code":"provider_rate_limited","message":"slow down","retryable":true,"details":{"status":429}}}');
    expect(evs).toEqual([{ kind: "error", code: "provider_rate_limited", message: "slow down", retryable: true, details: { status: 429 } }]);
    const bare = parseChunk('{"error":{"code":"provider_error","message":"x"}}');
    expect(bare).toEqual([{ kind: "error", code: "provider_error", message: "x" }]);
  });
});
