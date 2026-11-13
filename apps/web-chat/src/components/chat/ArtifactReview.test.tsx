import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import type { ArtifactWork } from "@/lib/artifactWorkspace";
import { ArtifactReview } from "./ArtifactReview";
vi.mock("@/lib/api", () => ({ api: { controlArtifactWork: vi.fn(), getArtifactWork: vi.fn() } }));
beforeEach(() => { vi.resetAllMocks(); useI18nStore.setState({ locale: "en" }); }); afterEach(cleanup);
const work: ArtifactWork = { id: "work", file_id: "file", status: "ready", stage: "ready", updated_at: "now", changes: ["a", "b"].map(id => ({ id, before: { id, value: "Before " + id, style: {} }, after: { id, value: "After " + id, style: {} } })) };
it("accepts only selected changes and returns the saved file after explicit action", async () => {
  const saved = vi.fn(); const file = { id: "revision" };
  vi.mocked(api.controlArtifactWork).mockResolvedValue({ file } as never);
  render(<ArtifactReview work={work} onWork={vi.fn()} onSaved={saved} onDismiss={vi.fn()} />);
  expect(api.controlArtifactWork).not.toHaveBeenCalled(); fireEvent.click(screen.getAllByRole("checkbox")[1]);
  fireEvent.click(screen.getByText("Accept 1 changes")); await waitFor(() => expect(saved).toHaveBeenCalledWith(file));
  expect(api.controlArtifactWork).toHaveBeenCalledWith("work", "accept", ["a"]);
});
it("retries a failed task without creating another proposal", async () => {
  vi.mocked(api.controlArtifactWork).mockResolvedValue({}); vi.mocked(api.getArtifactWork).mockResolvedValue({ work: { ...work, status: "running" } });
  const update = vi.fn(); render(<ArtifactReview work={{ ...work, status: "failed", error: "mock interruption" }} onWork={update} onSaved={vi.fn()} onDismiss={vi.fn()} />);
  fireEvent.click(screen.getByText("Retry failed stage")); await waitFor(() => expect(update).toHaveBeenCalled()); expect(api.controlArtifactWork).toHaveBeenCalledWith("work", "retry", []);
});
