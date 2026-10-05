// AM10 D — the chat list follows a round. A chat is named for its round's
// period and counts its rounds; the list was read once and again only after
// a rename, a pin or a new chat, so after a person's first round the sidebar
// still said "แชทแรก · 0 รอบ" until the page was reloaded (the walk on the
// built app found it). The thread now says so when a round begins and when
// its files arrive — once each, not on every progress update.
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ThreadItem } from "../types";

const api = vi.hoisted(() => ({
  thread: vi.fn(),
  events: vi.fn(),
}));
vi.mock("../api", () => ({ amApi: api }));

import { onChatsChanged, roundMoved } from "../chats";
import { useThread } from "./useThread";

const item = (over: Partial<ThreadItem> & { id: string; kind: string }): ThreadItem =>
  ({ install_id: "in1", payload: {}, rev: 1, created_at: "2026-10-05T00:00:00Z", ...over }) as ThreadItem;

afterEach(cleanup);

describe("roundMoved", () => {
  it("is true once when a round begins and once when its files arrive", () => {
    const seen = new Set<string>();
    expect(roundMoved(seen, item({ id: "p1", kind: "progress", run_id: "r1" }))).toBe(true);
    expect(roundMoved(seen, item({ id: "p1", kind: "progress", run_id: "r1", rev: 9 }))).toBe(false); // a progress update
    expect(roundMoved(seen, item({ id: "o1", kind: "outputs", run_id: "r1" }))).toBe(true);
    expect(roundMoved(seen, item({ id: "o1", kind: "outputs", run_id: "r1", rev: 12 }))).toBe(false);
    expect(roundMoved(seen, item({ id: "o2", kind: "outputs", run_id: "r1" }))).toBe(true); // more files for the round
    expect(roundMoved(seen, item({ id: "t1", kind: "agent_text" }))).toBe(false);
    expect(roundMoved(seen, item({ id: "p2", kind: "progress", run_id: "r2" }))).toBe(true);
  });
});

describe("a thread tells the chat list when a round moves", () => {
  let heard: string[];
  let off: () => void;

  beforeEach(() => {
    heard = [];
    off = onChatsChanged((id) => heard.push(id));
    // the thread as loaded: one round already there
    api.thread.mockResolvedValue({
      items: [item({ id: "p0", kind: "progress", run_id: "r0", rev: 1 }), item({ id: "o0", kind: "outputs", run_id: "r0", rev: 2 })],
      cursor: 2,
    });
    api.events.mockReturnValue(new Promise(() => {})); // the stream stays open and silent
  });
  afterEach(() => off());

  it("says nothing for what was loaded, then once per round begun and per delivery", async () => {
    const { result } = renderHook(() => useThread("in1"));
    await waitFor(() => expect(result.current.status).toBe("live"));
    expect(heard).toEqual([]); // the list was read with the same rounds in it

    act(() => result.current.merge([item({ id: "p0", kind: "progress", run_id: "r0", rev: 3 })]));
    expect(heard).toEqual([]); // an update of a round already counted

    act(() => result.current.merge([item({ id: "p1", kind: "progress", run_id: "r1", rev: 4 })]));
    expect(heard).toEqual(["in1"]); // a round began: the chat counts one more
    act(() => result.current.merge([item({ id: "p1", kind: "progress", run_id: "r1", rev: 5 })]));
    act(() => result.current.merge([item({ id: "p1", kind: "progress", run_id: "r1", rev: 6 })]));
    expect(heard).toEqual(["in1"]); // its progress ticks say nothing

    act(() => result.current.merge([item({ id: "o1", kind: "outputs", run_id: "r1", rev: 7 })]));
    expect(heard).toEqual(["in1", "in1"]); // its files arrived: the chat takes the period's name
  });
});
