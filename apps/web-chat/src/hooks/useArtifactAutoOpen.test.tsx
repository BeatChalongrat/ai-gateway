import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { useArtifactAutoOpen } from "./useArtifactAutoOpen";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import type { FileAttachment, Message } from "@/lib/types";
const file = { id: "file", filename: "report.docx" } as FileAttachment;
const message = (files: FileAttachment[]) => [{ id: "message", role: "assistant", files, content: "", status: "completed", created_at: "now" }] as Message[];
beforeEach(() => useSidePaneStore.setState({ pane: null, files: [] })); afterEach(cleanup);
it("opens fresh assistant output but never historic attachments", () => {
  const hook = renderHook(({ messages }) => useArtifactAutoOpen("conversation", messages, true), { initialProps: { messages: message([file]) } });
  expect(useSidePaneStore.getState().pane).toBeNull();
  const next = { ...file, id: "next" }; hook.rerender({ messages: message([file, next]) });
  expect(useSidePaneStore.getState().pane).toEqual({ kind: "preview", file: next });
});
it("adds revisions as replacement tabs without taking focus from an active pane", () => {
  const hook = renderHook(({ messages }) => useArtifactAutoOpen("conversation", messages, true), { initialProps: { messages: message([file]) } });
  useSidePaneStore.setState({ pane: { kind: "workspace" }, files: [file] });
  const revision = { ...file, id: "revision", parent_file_id: file.id }; hook.rerender({ messages: message([file, revision]) });
  expect(useSidePaneStore.getState().pane?.kind).toBe("workspace"); expect(useSidePaneStore.getState().files).toEqual([revision]);
});
it("ignores older paginated history and notifies the editor about a new proposal for an existing file", () => {
  const initial = message([file]).map(m => ({ ...m, created_at: "2026-11-13T10:00:00Z" }));
  const hook = renderHook(({ messages }) => useArtifactAutoOpen("conversation", messages, true), { initialProps: { messages: initial } });
  const older = { ...initial[0], id: "older", created_at: "2026-11-12T10:00:00Z", files: [{ ...file, id: "historic" }] };
  hook.rerender({ messages: [older, ...initial] }); expect(useSidePaneStore.getState().pane).toBeNull();
  let delivered = ""; const listener = (e: Event) => { delivered = (e as CustomEvent<string>).detail; };
  window.addEventListener("sran-artifact-delivered", listener);
  hook.rerender({ messages: [...initial, { ...initial[0], id: "proposal", created_at: "2026-11-13T11:00:00Z" }] });
  expect(delivered).toBe(file.id); window.removeEventListener("sran-artifact-delivered", listener);
});
