import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VideoJobsPanel } from "./VideoJobsPanel";
import { api } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
vi.mock("@/lib/api", () => ({ api: { listVideoJobs: vi.fn(), confirmMediaJob: vi.fn(), cancelMediaJob: vi.fn(), mediaJobOutputObjectUrl: vi.fn() } }));
const pending = { job_id: "job", model: "model", status: "pending_confirm", estimated_cost_usd: "1.20", created_at: "2026-11-13" };
beforeEach(() => { vi.resetAllMocks(); useI18nStore.setState({ locale: "en" }); vi.mocked(api.listVideoJobs).mockResolvedValue({ jobs: [pending] }); });
afterEach(cleanup);

it("loads durable jobs but never approves them on render", async () => {
  render(<VideoJobsPanel conversationId="conversation" />);
  await screen.findByText("Confirm cost and generate");
  expect(api.listVideoJobs).toHaveBeenCalledWith("conversation");
  expect(api.confirmMediaJob).not.toHaveBeenCalled();
  // A render must not reset the polling effect and create a request loop.
  expect(api.listVideoJobs).toHaveBeenCalledTimes(1);
});
it("approves only after a click and removes the approval button", async () => {
  vi.mocked(api.confirmMediaJob).mockResolvedValue({ ...pending, status: "queued" });
  render(<VideoJobsPanel conversationId="conversation" />);
  fireEvent.click(await screen.findByText("Confirm cost and generate"));
  await waitFor(() => expect(api.confirmMediaJob).toHaveBeenCalledWith("job"));
  await waitFor(() => expect(screen.queryByText("Confirm cost and generate")).toBeNull());
});
it("cancels pending work without approving it", async () => {
  vi.mocked(api.cancelMediaJob).mockResolvedValue({ ...pending, status: "cancelled" });
  render(<VideoJobsPanel conversationId="conversation" />);
  fireEvent.click(await screen.findByText("Cancel job"));
  await waitFor(() => expect(api.cancelMediaJob).toHaveBeenCalledWith("job"));
  expect(api.confirmMediaJob).not.toHaveBeenCalled();
});
