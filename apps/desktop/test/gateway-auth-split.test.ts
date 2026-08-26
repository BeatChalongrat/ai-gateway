// FIX21 §2 — a 401 and a 403 are DIFFERENT failures and must be told apart at
// the REAL streaming branch (gateway-client.ts streamCompletion), not on a
// helper in isolation. The bug: both collapsed into "the gateway refused the
// agent key", and BOTH called invalidateKey() — so a DLP/guardrail 403 (a good
// key, a refused request) threw the key away and told the reader to go fix the
// key. That sent the owner down the wrong path for the better part of an hour.
//
// These pins drive a real 403 and a real 401 through streamCompletion with a
// stubbed global fetch and a spying KeyStore, and assert:
//   - 403 → code "gateway_forbidden", the backend's OWN reason surfaced, HTTP
//           403 kept in the text, and the key is NOT cleared.
//   - 401 → code "gateway_unauthorized", and the key IS cleared.
// The 403 body is the one captured live from the gateway for A1 (guardrail).
import { describe, expect, it, vi, afterEach } from "vitest";

import { GatewayClient, readErrorReason, type KeyStore } from "../src/main/gateway-client";
import type { Logger } from "../src/main/logger";

const GUARDRAIL_403_BODY = JSON.stringify({
  error: {
    code: "guardrail_blocked",
    message: "Request blocked by content guardrail",
    details: { rule: "jailbreak_dan" },
  },
  request_id: "5dd96719f358/yN0uPUXIJE-272808",
});

function makeLogger(): Logger {
  return {
    info: () => {}, warn: () => {}, error: () => {},
    filePath: null, attachFile: () => {}, lines: [],
  };
}

/** A KeyStore that always has a key, and records whether clear() was called. */
function makeKeyStore() {
  const state = { cleared: false };
  const store: KeyStore = {
    load: () => "sk-live-should-never-be-printed",
    save: () => {},
    clear: () => { state.cleared = true; },
  };
  return { store, state };
}

async function drain(gen: AsyncGenerator<unknown>): Promise<Array<Record<string, unknown>>> {
  const out: Array<Record<string, unknown>> = [];
  for await (const ev of gen) out.push(ev as Record<string, unknown>);
  return out;
}

afterEach(() => { vi.unstubAllGlobals(); });

function clientWithResponse(res: Response) {
  vi.stubGlobal("fetch", vi.fn(async () => res));
  const { store, state } = makeKeyStore();
  const client = new GatewayClient("http://gw.test", () => "hdr.eyJ.sig", store, makeLogger());
  return { client, state };
}

const REQUEST = { model: "gpt-5.6-terra", messages: [{ role: "user" as const, content: "hi" }] };

describe("FIX21 §2 — streamCompletion splits 401 and 403", () => {
  it("403 keeps the key, surfaces the backend reason, and keeps HTTP 403 in the text", async () => {
    const { client, state } = clientWithResponse(new Response(GUARDRAIL_403_BODY, { status: 403 }));
    const events = await drain(client.streamCompletion(REQUEST, new AbortController().signal));

    const err = events.find((e) => e.kind === "error");
    expect(err).toBeTruthy();
    expect(err!.code).toBe("gateway_forbidden");
    expect(err!.code).not.toBe("gateway_unauthorized");
    expect(err!.message).toContain("403");
    expect(err!.message).toContain("Request blocked by content guardrail");
    // The key is FINE on a 403 — it must not be thrown away.
    expect(state.cleared).toBe(false);
  });

  it("401 invalidates the key and tells the agent-key story", async () => {
    const { client, state } = clientWithResponse(new Response(JSON.stringify({ error: { message: "Invalid or missing API key" } }), { status: 401 }));
    const events = await drain(client.streamCompletion(REQUEST, new AbortController().signal));

    const err = events.find((e) => e.kind === "error");
    expect(err!.code).toBe("gateway_unauthorized");
    expect(err!.message).toContain("401");
    // A 401 IS a bad/expired key — it must be cleared so the next turn re-provisions.
    expect(state.cleared).toBe(true);
  });

  it("403 with a non-JSON body still does not clear the key and still names HTTP 403", async () => {
    const { client, state } = clientWithResponse(new Response("Forbidden", { status: 403 }));
    const events = await drain(client.streamCompletion(REQUEST, new AbortController().signal));
    const err = events.find((e) => e.kind === "error");
    expect(err!.code).toBe("gateway_forbidden");
    expect(err!.message).toContain("403");
    expect(state.cleared).toBe(false);
  });

  it("never puts a key fragment in the surfaced message", async () => {
    const { client } = clientWithResponse(new Response(GUARDRAIL_403_BODY, { status: 403 }));
    const events = await drain(client.streamCompletion(REQUEST, new AbortController().signal));
    const err = events.find((e) => e.kind === "error");
    expect(err!.message).not.toContain("sk-live");
  });
});

describe("FIX21 §2 — readErrorReason", () => {
  it("prefers the {error:{message}} envelope", async () => {
    expect(await readErrorReason(new Response(GUARDRAIL_403_BODY, { status: 403 }))).toBe("Request blocked by content guardrail");
  });
  it("falls back to raw text for a non-JSON body", async () => {
    expect(await readErrorReason(new Response("plain text reason", { status: 403 }))).toBe("plain text reason");
  });
  it("returns empty string for an empty body, never throws", async () => {
    expect(await readErrorReason(new Response("", { status: 403 }))).toBe("");
  });
});
