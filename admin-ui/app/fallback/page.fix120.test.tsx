// FIX120 — the Fallback page: the counts say how many models have a chain
// and how many do not; the kill switch is a confirmed PUT that takes effect
// without a deploy; an override is a validated PUT / DELETE.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, put, del } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), del: vi.fn() }));

vi.mock("../../lib/api", () => ({
  api: { get, put, post: vi.fn(), patch: vi.fn(), delete: del },
  ApiError: class extends Error {},
  getApiErrorMessage: (e: unknown, fallback: string) => (e instanceof Error && e.message) || fallback,
}));

const POLICY = {
  version: 1,
  contract_sha256: "x".repeat(64),
  kill_switch: { setting_key: "fallback_enabled", enabled: true, stored_value: null, updated_at: null },
  limits: {
    non_stream: { max_candidates: 2, attempt_timeout_s: 10, total_budget_s: 25 },
    stream: { max_candidates: 2, total_budget_s: null },
  },
  deployment_override: null,
  tenant_overrides: [],
  effective: {
    limits: {
      non_stream: { max_candidates: 2, attempt_timeout_s: 10, total_budget_s: 25 },
      stream: { max_candidates: 2, total_budget_s: null },
    },
    breaker: { enabled: false, allowed_fails: 3, window_s: 60, cooldown_s: 60 },
    cost_cap: { enabled: false, max_cost_ratio_vs_requested: null, tenant_daily_usd: null },
    opt_in: { context_window_fallback: false, content_policy_fallback: false },
  },
};
const CHAINS = {
  summary: { total: 3, with_chain: 1, without_chain: 2 },
  kill_switch_enabled: true,
  models: [
    { provider: "sranai", model: "qwen3.8-27b-fp8", has_chain: false, chain: [], source: "none" },
    { provider: "openai", model: "gpt-5.6-luna", has_chain: false, chain: [], source: "none" },
    { provider: "deepseek", model: "deepseek-v4-pro", has_chain: true, chain: [{ provider: "openrouter", model: "openai/gpt-4o" }, { provider: "openai", model: "gpt-4o-mini" }], source: "curated" },
  ],
};

let policyState = POLICY;
let chainsState = CHAINS;
vi.mock("../../lib/useApi", () => ({
  useApi: (path: string) => ({
    data: path.includes("/chains") ? chainsState : policyState,
    loading: false,
    error: null,
    refetch: () => {},
  }),
}));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import FallbackPage from "./page";

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <FallbackPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  policyState = POLICY;
  chainsState = CHAINS;
  get.mockReset();
  put.mockReset();
  del.mockReset();
});
afterEach(cleanup);

describe("FIX120 — the Fallback page", () => {
  it("shows the counts and each model's chain or 'no chain'", () => {
    renderPage();
    expect(screen.getByTestId("stat-total").textContent).toBe("3");
    expect(screen.getByTestId("stat-with").textContent).toBe("1");
    expect(screen.getByTestId("stat-without").textContent).toBe("2");
    expect(screen.getByTestId("chain-deepseek-v4-pro").textContent).toBe("openrouter:openai/gpt-4o  →  openai:gpt-4o-mini");
    expect(screen.getByTestId("chain-qwen3.8-27b-fp8").textContent).not.toBe("");
    expect(screen.getByTestId("chain-qwen3.8-27b-fp8").textContent).not.toContain("→");
    expect(screen.getByTestId("kill-switch").getAttribute("data-enabled")).toBe("true");
  });

  it("the kill switch is a confirmed PUT", async () => {
    put.mockResolvedValue({ setting_key: "fallback_enabled", enabled: false });
    renderPage();
    fireEvent.click(screen.getByTestId("kill-switch-toggle"));
    // the confirm modal's danger button carries the same label as the toggle;
    // the modal renders after the toggle, so it is the last one
    await waitFor(() => expect(document.querySelectorAll("button.sran-btn-danger").length).toBeGreaterThan(0));
    const dangers = document.querySelectorAll("button.sran-btn-danger");
    fireEvent.click(dangers[dangers.length - 1]);
    await waitFor(() => expect(put).toHaveBeenCalledWith("/admin/fallback/kill-switch", { enabled: false }));
  });

  it("an OFF switch is shown as OFF and the button turns it back on", async () => {
    policyState = { ...POLICY, kill_switch: { ...POLICY.kill_switch, enabled: false, stored_value: "false" } };
    put.mockResolvedValue({ enabled: true });
    renderPage();
    expect(screen.getByTestId("kill-switch").getAttribute("data-enabled")).toBe("false");
    fireEvent.click(screen.getByTestId("kill-switch-toggle"));
    // the confirm modal's danger button carries the same label as the toggle;
    // the modal renders after the toggle, so it is the last one
    await waitFor(() => expect(document.querySelectorAll("button.sran-btn-danger").length).toBeGreaterThan(0));
    const dangers = document.querySelectorAll("button.sran-btn-danger");
    fireEvent.click(dangers[dangers.length - 1]);
    await waitFor(() => expect(put).toHaveBeenCalledWith("/admin/fallback/kill-switch", { enabled: true }));
  });

  it("a deployment override is PUT as parsed JSON, and bad JSON never reaches the server", async () => {
    put.mockResolvedValue({});
    renderPage();
    const editor = screen.getByTestId("override-deployment");
    const area = within(editor).getByRole("textbox");
    fireEvent.change(area, { target: { value: "{not json" } });
    fireEvent.click(within(editor).getAllByRole("button")[0]);
    await waitFor(() => expect(put).not.toHaveBeenCalled());
    fireEvent.change(area, { target: { value: '{"chains":{"curated":{"qwen3.8-27b-fp8":[{"provider":"sranai","model":"qwen3.6-35b-a3b"}]}}}' } });
    fireEvent.click(within(editor).getAllByRole("button")[0]);
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/admin/fallback/overrides/deployment", {
        override: { chains: { curated: { "qwen3.8-27b-fp8": [{ provider: "sranai", model: "qwen3.6-35b-a3b" }] } } },
      }),
    );
  });

  it("a tenant override is addressed by tenant id", async () => {
    put.mockResolvedValue({});
    renderPage();
    fireEvent.change(screen.getByLabelText(/tenant/i), { target: { value: "t-acme" } });
    const editor = await screen.findByTestId("override-tenant");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: '{"allowed_providers":["sranai"]}' } });
    fireEvent.click(within(editor).getAllByRole("button")[0]);
    await waitFor(() => expect(put).toHaveBeenCalledWith("/admin/fallback/overrides/tenant/t-acme", { override: { allowed_providers: ["sranai"] } }));
  });
});
