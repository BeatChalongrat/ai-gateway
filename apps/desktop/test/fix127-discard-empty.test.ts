// FIX127 C3 — an attached-then-abandoned new chat is deleted through the host.
//
// On the web the new-chat page fires a keepalive DELETE on pagehide. Electron
// refuses that cross-origin request while the renderer is torn down, so the
// desktop left a 0-message conversation behind on every hard navigation (F2
// in R3). The page now asks the host (`conversation.discardEmpty`); the host
// deletes ONLY a conversation the server says has no messages and no turn in
// flight — the page's word is never enough to delete a chat.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { GrantStore } from "../src/main/grants";
import { PermissionStore } from "../src/main/permissions";
import { HOST_QUERY_OPS } from "../src/shared/bridge-protocol";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { Logger } from "../src/main/logger";

const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };

function build(serverCount: number | null) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix127c3-"));
  const results: Array<{ id: string; ok: boolean; data?: unknown; error?: string }> = [];
  const deleted: string[] = [];
  const bridge = {
    postTurnEvent: () => {},
    post: () => {},
    postHostResult: (id: string, ok: boolean, data?: unknown, error?: string) => results.push({ id, ok, data, error }),
    postHostError: () => {},
  };
  const chatEdge = {
    getConversation: async (id: string) => (serverCount === null ? null : { id, message_count: serverCount }),
    deleteConversation: async (id: string) => {
      deleted.push(id);
      return true;
    },
  };
  const co = new TurnCoordinator(
    bridge as never, {} as never, chatEdge as never, {} as never, {} as never, {} as never,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null },
    log,
  );
  return { co, results, deleted };
}

describe("FIX127 C3 — conversation.discardEmpty", () => {
  it("is a page op in the closed set", () => {
    expect(HOST_QUERY_OPS).toContain("conversation.discardEmpty");
  });

  it("deletes a conversation the server says has no messages", async () => {
    const { co, results, deleted } = build(0);
    await co.onHostQuery({ requestId: "r1", op: "conversation.discardEmpty", args: { conversationId: "c-empty" } });
    expect(deleted).toEqual(["c-empty"]);
    expect(results[0]).toMatchObject({ id: "r1", ok: true, data: { deleted: true } });
  });

  it("refuses when the server says the conversation has messages", async () => {
    const { co, results, deleted } = build(2);
    await co.onHostQuery({ requestId: "r2", op: "conversation.discardEmpty", args: { conversationId: "c-used" } });
    expect(deleted).toEqual([]);
    expect(results[0]).toMatchObject({ id: "r2", ok: false, error: "conversation-has-messages" });
  });

  it("a conversation the server no longer has is nothing to do", async () => {
    const { co, results, deleted } = build(null);
    await co.onHostQuery({ requestId: "r3", op: "conversation.discardEmpty", args: { conversationId: "c-gone" } });
    expect(deleted).toEqual([]);
    expect(results[0]).toMatchObject({ id: "r3", ok: true, data: { deleted: false, reason: "not-found" } });
  });

  it("refuses without a conversation id", async () => {
    const { co, results, deleted } = build(0);
    await co.onHostQuery({ requestId: "r4", op: "conversation.discardEmpty", args: {} });
    expect(deleted).toEqual([]);
    expect(results[0]).toMatchObject({ ok: false, error: "conversationId-missing" });
  });
});
