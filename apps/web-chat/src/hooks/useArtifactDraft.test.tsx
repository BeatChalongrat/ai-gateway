import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useArtifactDraft } from "./useArtifactDraft";
import { EMPTY_ARTIFACT_DRAFT } from "@/lib/artifactWorkspace";

vi.mock("@/lib/api", () => ({ api: { getArtifactDraft: vi.fn(), saveArtifactDraft: vi.fn(), deleteArtifactDraft: vi.fn() } }));
const body = (text: string) => ({ ...EMPTY_ARTIFACT_DRAFT, changes: { p: { id: "p", value: text, style: {} } } });
beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); useAuthStore.setState({ user: { user_id: "user", tenant_id: "tenant", email: "test@example.test", role: "user" } }); vi.mocked(api.getArtifactDraft).mockResolvedValue({ draft: null }); vi.mocked(api.saveArtifactDraft).mockImplementation(async (_id, d) => ({ draft: { ...d, revision: d.revision + 1 } })); });
afterEach(cleanup);

it("recovers saved drafts after remount and supports undo/redo without publishing files", async () => {
  const first = renderHook(() => useArtifactDraft("file", "sha")); await waitFor(() => expect(first.result.current.ready).toBe(true));
  act(() => first.result.current.update(() => body("updated"))); expect(first.result.current.undoCount).toBe(1);
  act(() => first.result.current.undo()); expect(first.result.current.value.changes).toEqual({});
  act(() => first.result.current.redo()); expect(first.result.current.value.changes.p.value).toBe("updated");
  await act(() => first.result.current.flush()); expect(first.result.current.status).toBe("saved"); first.unmount();
  const second = renderHook(() => useArtifactDraft("file", "sha")); await waitFor(() => expect(second.result.current.value.changes.p?.value).toBe("updated"));
  expect(api.saveArtifactDraft).toHaveBeenCalledWith("file", expect.objectContaining({ revision: 0, sha256: "sha" }));
});

it("never overwrites a newer server draft with a stale local revision", async () => {
  localStorage.setItem("sran-artifact-draft:tenant:user:file", JSON.stringify({ revision: 1, sha256: "sha", body: body("local"), updated_at: "2030-01-01T00:00:00Z" }));
  vi.mocked(api.getArtifactDraft).mockResolvedValue({ draft: { revision: 2, sha256: "sha", body: body("remote"), updated_at: "2026-01-01T00:00:00Z" } });
  const hook = renderHook(() => useArtifactDraft("file", "sha")); await waitFor(() => expect(hook.result.current.status).toBe("conflict"));
  await act(() => hook.result.current.flush()); expect(api.saveArtifactDraft).not.toHaveBeenCalled(); expect(hook.result.current.exportRecovery()).toContain("local");
  await act(() => hook.result.current.reloadRemote()); expect(hook.result.current.value.changes.p.value).toBe("remote");
});

it("isolates local drafts by user and refuses drafts for a different source hash", async () => {
  localStorage.setItem("sran-artifact-draft:tenant:other:file", JSON.stringify({ revision: 1, sha256: "sha", body: body("secret") }));
  const hook = renderHook(() => useArtifactDraft("file", "sha")); await waitFor(() => expect(hook.result.current.ready).toBe(true)); expect(hook.result.current.value.changes).toEqual({}); hook.unmount();
  vi.mocked(api.getArtifactDraft).mockResolvedValue({ draft: { revision: 1, sha256: "old", body: body("stale") } });
  const stale = renderHook(() => useArtifactDraft("file", "sha")); await waitFor(() => expect(stale.result.current.status).toBe("conflict")); expect(stale.result.current.value.changes).toEqual({}); expect(stale.result.current.exportRecovery()).toContain("stale");
});
