import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ModelEntry } from "@/lib/types";

const mocks = vi.hoisted(() => ({ config: vi.fn(), submit: vi.fn(), confirm: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { getVideoConfig: mocks.config, submitVideoGen: mocks.submit, confirmMediaJob: mocks.confirm }, ApiError: class extends Error {} }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { role: "user" } }) }));
vi.mock("@/lib/i18n", () => ({ useT: () => (key: string) => key, tOutside: (key: string) => key }));
import { VideoGenForm } from "./VideoGenForm";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});
const model = { id: "configured-video" } as ModelEntry;

describe("video readiness controls", () => {
  it("waits for a user click before confirming a premium job on the server", async () => {
    mocks.config.mockResolvedValue({ enabled: true, reason: "", supported_controls: { duration_sec: [1, 5] } });
    mocks.submit.mockResolvedValue({ job_id: "premium", status: "pending_confirm", estimated_cost_usd: "$2", balance_after_usd: "$3", requires_confirmation: true });
    mocks.confirm.mockResolvedValue({ job_id: "premium", status: "queued" });
    render(<VideoGenForm model={model} />);
    fireEvent.change(screen.getByPlaceholderText("media.video.placeholder"), { target: { value: "demo" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "media.generateVideo" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "media.generateVideo" }));
    await screen.findByRole("dialog");
    expect(mocks.confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "media.confirm.confirm" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith("premium"));
  });
  it("does not allow submission before capability is known", () => {
    mocks.config.mockReturnValue(new Promise(() => {}));
    render(<VideoGenForm model={model} />);
    fireEvent.change(screen.getByPlaceholderText("media.video.placeholder"), { target: { value: "demo" } });
    expect((screen.getByRole("button", { name: "media.generateVideo" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("media.videoChecking");
  });
  it("shows an unavailable reason and keeps submit disabled", async () => {
    mocks.config.mockResolvedValue({ enabled: false, reason: "No provider key", supported_controls: { duration_sec: [1, 5] } });
    render(<VideoGenForm model={model} />);
    await screen.findByText("No provider key");
    expect((screen.getByRole("button", { name: "media.generateVideo" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("uses the server duration limit and submits only supported controls", async () => {
    mocks.config.mockResolvedValue({ enabled: true, reason: "", supported_controls: { duration_sec: [1, 3] } });
    mocks.submit.mockResolvedValue({ job_id: "job", status: "queued", estimated_cost_usd: "$0.01", balance_after_usd: "$1", requires_confirmation: false });
    render(<VideoGenForm model={model} />);
    await waitFor(() => expect((screen.getByRole("slider") as HTMLInputElement).max).toBe("3"));
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("media.video.placeholder"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: "media.generateVideo" }));
    await waitFor(() => expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({ prompt: "demo", duration_sec: 3, resolution: "auto", has_audio: false })));
  });
});
