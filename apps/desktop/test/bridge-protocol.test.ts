// PINNING TEST — the bridge vocabulary (E3 §1, criterion 10).
//
// Pins BY NAME: exactly four turn-inbound types, exactly four turn-outbound
// types, one control pair (host.query / host.result), a closed host.query op
// set that contains session.logout (E3 §2); the envelope gate (object,
// nonce, closed type); and turn.submit's refusal of path-shaped handles.
// A rename of any member fails this file.
//
// LIMITS — what this test cannot see:
//   * The dispatch in main/bridge.ts (the switch with no default branch) is
//     pinned only by a source-text check below. It cannot see a handler that
//     forwards an unknown type somewhere else at runtime.
//   * The senderFrame comparison is exercised in test/ipc-guard.test.ts on
//     the pure function; that the bridge calls it on EVERY channel is a
//     source-text check here (`guardedOn` wraps every `ipcMain.on`).
//   * Nothing here runs a renderer. The live probes (--probe-e3 reject,
//     containment) are the runtime half.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import {
  CONTROL_INBOUND_TYPES,
  CONTROL_OUTBOUND_TYPES,
  E3_SUPPORTED_OPS,
  HOST_QUERY_OPS,
  TURN_INBOUND_TYPES,
  TURN_OUTBOUND_TYPES,
  isOpaqueHandle,
  parseEnvelope,
  validateHostQuery,
  validateTurnSubmit,
  validateUiReady,
} from "../src/shared/bridge-protocol";

const BRIDGE_TS = path.resolve(__dirname, "..", "src", "main", "bridge.ts");

describe("closed vocabulary — by name", () => {
  it("exactly four turn-inbound members", () => {
    expect([...TURN_INBOUND_TYPES]).toEqual(["turn.submit", "turn.cancel", "consent.respond", "ui.ready"]);
  });
  it("exactly four turn-outbound members", () => {
    expect([...TURN_OUTBOUND_TYPES]).toEqual(["turn.event", "consent.request", "session.changed", "host.error"]);
  });
  it("one control pair, no more", () => {
    expect([...CONTROL_INBOUND_TYPES]).toEqual(["host.query"]);
    expect([...CONTROL_OUTBOUND_TYPES]).toEqual(["host.result"]);
  });
  it("host.query ops are closed and carry logout (E3 §2)", () => {
    expect(HOST_QUERY_OPS).toContain("session.logout");
    expect(HOST_QUERY_OPS).toContain("diag.reportRendererError");
    expect(new Set(HOST_QUERY_OPS).size).toBe(HOST_QUERY_OPS.length);
    for (const op of E3_SUPPORTED_OPS) expect(HOST_QUERY_OPS).toContain(op);
    expect(validateHostQuery({ requestId: "q1", op: "shell.exec", args: {} })).toMatchObject({ ok: false, reason: "unknown-op:shell.exec" });
  });
});

describe("envelope gate", () => {
  it("requires an object, the exact nonce, and a known type", () => {
    expect(parseEnvelope("string", "n")).toMatchObject({ ok: false, reason: "not-an-object" });
    expect(parseEnvelope({ nonce: "n", type: "ui.ready" }, null)).toMatchObject({ ok: false, reason: "no-nonce-for-this-page" });
    expect(parseEnvelope({ type: "ui.ready" }, "n")).toMatchObject({ ok: false, reason: "nonce-mismatch" });
    expect(parseEnvelope({ nonce: "wrong", type: "ui.ready" }, "n")).toMatchObject({ ok: false, reason: "nonce-mismatch" });
    expect(parseEnvelope({ nonce: "n" }, "n")).toMatchObject({ ok: false, reason: "type-missing" });
    expect(parseEnvelope({ nonce: "n", type: "turn.frobnicate" }, "n")).toMatchObject({ ok: false, reason: "unknown-type:turn.frobnicate" });
    expect(parseEnvelope({ nonce: "n", type: "turn.submit", payload: {} }, "n")).toMatchObject({ ok: true, type: "turn.submit" });
    expect(parseEnvelope({ nonce: "n", type: "host.query", payload: {} }, "n")).toMatchObject({ ok: true, type: "host.query" });
  });
  it("outbound names are never accepted inbound", () => {
    for (const t of [...TURN_OUTBOUND_TYPES, ...CONTROL_OUTBOUND_TYPES]) {
      expect(parseEnvelope({ nonce: "n", type: t }, "n")).toMatchObject({ ok: false });
    }
  });
});

describe("turn.submit: handles are opaque, never paths", () => {
  const base = { conversationId: "c1", text: "hi", modelId: null };
  it("accepts opaque handles", () => {
    expect(validateTurnSubmit({ ...base, attachmentHandles: ["file_01HZX", "a-b_C9"] })).toMatchObject({ ok: true });
    expect(validateTurnSubmit({ ...base })).toMatchObject({ ok: true, value: { attachmentHandles: [] } });
  });
  it("refuses every path/URI shape", () => {
    for (const bad of ["C:\\Windows\\win.ini", "/etc/passwd", "../x", "..\\x", "file:///C:/x", "http://evil/x", "a/b", "a b", "x.txt", "", "a".repeat(129)]) {
      expect(isOpaqueHandle(bad), bad).toBe(false);
      expect(validateTurnSubmit({ ...base, attachmentHandles: [bad] }), bad).toMatchObject({ ok: false, reason: "attachmentHandle-path-shaped" });
    }
    expect(validateTurnSubmit({ ...base, attachmentHandles: "C:\\x" })).toMatchObject({ ok: false, reason: "attachmentHandles-not-array" });
    expect(validateTurnSubmit({ ...base, attachmentHandles: [1] })).toMatchObject({ ok: false, reason: "attachmentHandle-not-string" });
  });
  it("refuses missing/oversize fields", () => {
    expect(validateTurnSubmit(null)).toMatchObject({ ok: false });
    expect(validateTurnSubmit({ text: "x" })).toMatchObject({ ok: false, reason: "conversationId-missing" });
    expect(validateTurnSubmit({ conversationId: "c" })).toMatchObject({ ok: false, reason: "text-missing" });
    expect(validateTurnSubmit({ ...base, modelId: 5 })).toMatchObject({ ok: false, reason: "modelId-not-string" });
    expect(validateTurnSubmit({ ...base, modelId: "x".repeat(129) })).toMatchObject({ ok: false, reason: "modelId-too-long" });
    expect(validateTurnSubmit({ ...base, text: "x".repeat(200_001) })).toMatchObject({ ok: false, reason: "text-too-long" });
  });
  // FIX9 Part B: modelId and imageMode are carried on the existing turn message.
  it("carries modelId and imageMode as fields (not a new message type)", () => {
    expect(validateTurnSubmit({ ...base, modelId: "gpt-4o", imageMode: true })).toMatchObject({ ok: true, value: { modelId: "gpt-4o", imageMode: true } });
    expect(validateTurnSubmit({ ...base })).toMatchObject({ ok: true, value: { imageMode: false } }); // defaults off
    expect(validateTurnSubmit({ ...base, imageMode: "yes" })).toMatchObject({ ok: true, value: { imageMode: false } }); // only strict true enables it
  });
  // FIX11 §3: editMessageId is carried on the existing turn message (fork the
  // conversation server-side), NOT a new message type. Defaults null.
  it("carries editMessageId as a field, defaulting null, rejecting non-strings", () => {
    expect(validateTurnSubmit({ ...base, editMessageId: "m_01HZX" })).toMatchObject({ ok: true, value: { editMessageId: "m_01HZX" } });
    expect(validateTurnSubmit({ ...base })).toMatchObject({ ok: true, value: { editMessageId: null } }); // default: not an edit
    expect(validateTurnSubmit({ ...base, editMessageId: null })).toMatchObject({ ok: true, value: { editMessageId: null } });
    expect(validateTurnSubmit({ ...base, editMessageId: 5 })).toMatchObject({ ok: false, reason: "editMessageId-not-string" });
    expect(validateTurnSubmit({ ...base, editMessageId: "x".repeat(129) })).toMatchObject({ ok: false, reason: "editMessageId-too-long" });
  });
  it("ui.ready: lastSeq must be a non-negative integer", () => {
    expect(validateUiReady({ conversationId: "c", lastSeq: 42 })).toEqual({ ok: true, value: { conversationId: "c", lastSeq: 42 } });
    expect(validateUiReady({})).toEqual({ ok: true, value: { conversationId: null, lastSeq: 0 } });
    expect(validateUiReady({ lastSeq: -1 })).toMatchObject({ ok: false });
    expect(validateUiReady({ lastSeq: 1.5 })).toMatchObject({ ok: false });
  });
});

describe("main/bridge.ts wiring (source text, see LIMITS)", () => {
  const src = fs.readFileSync(BRIDGE_TS, "utf8");
  it("every ipcMain.on goes through guardedOn, which calls isTrustedSender", () => {
    const onCalls = src.match(/ipcMain\.on\(/g) ?? [];
    expect(onCalls.length).toBe(1); // the one inside guardedOn
    expect(src).toMatch(/private guardedOn\(/);
    expect(src).toMatch(/isTrustedSender\(\s*\{ frameUrl: frame\?\.url, isMainFrame: frame !== null && frame === event\.sender\.mainFrame \}/);
    expect(src).not.toMatch(/ipcMain\.(handle|once|handleOnce)\(/);
  });
  it("the inbound switch has no default branch", () => {
    const sw = src.slice(src.indexOf("switch (envelope.type)"));
    const body = sw.slice(0, sw.indexOf("private accept("));
    expect(body).not.toMatch(/default\s*:/);
  });
  it("outbound goes only to the top frame via webContents.send", () => {
    expect(src).toMatch(/wc\.send\(IPC_CHANNELS\.outbound/);
    expect(src).not.toMatch(/\.frames\b|framesInSubtree|webFrameMain/);
  });
});
