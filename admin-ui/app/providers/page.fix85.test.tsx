// FIX85 — an endpoint belongs to the key. The providers page shows, per
// key, which box it talks to (its own endpoint, or the provider default),
// lets one key be pointed at its own box, and keeps the provider-level
// value visibly a DEFAULT. Before: one endpoint per provider, two keys for
// two boxes looked identical in this table.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post, put, patch, del } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  api: { get, post, put, patch, delete: del },
  getApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}));
// One stable object: the page keys an effect on `data`, so a fresh object
// per render would re-run it forever.
const PROVIDERS = {
  data: { items: [{ provider_name: "sranai", enabled: true, models: ["qwen3.8-27b-fp8"], key_count: 2, has_valid_key: true }] },
  loading: false,
  error: null,
  refetch: () => {},
};
vi.mock("../../lib/useApi", () => ({ useApi: () => PROVIDERS }));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import ProvidersPage from "./page";

const BOX_A = "http://thaillm.example.invalid/api/v1";
const BOX_B = "http://gateway.example.invalid/v1";

const key = (over: Record<string, unknown>) => ({
  id: "k",
  provider_name: "sranai",
  label: "key",
  key_masked: "••••abcd",
  is_active: true,
  last_tested_at: "2026-09-08T00:00:00Z",
  last_test_status: "valid",
  last_test_latency_ms: 42,
  created_at: "2026-09-08T00:00:00Z",
  base_url: null,
  effective_base_url: BOX_A,
  endpoint_source: "provider",
  last_tested_base_url: BOX_A,
  models_served: 0,
  ...over,
});

const KEYS = [
  key({ id: "k-typhoon", label: "Typhoon", key_masked: "••••7yph", models_served: 5 }),
  key({
    id: "k-admin",
    label: "admin",
    key_masked: "••••adm1",
    base_url: BOX_B,
    effective_base_url: BOX_B,
    endpoint_source: "key",
    last_tested_base_url: BOX_B,
    last_test_status: "invalid",
    models_served: 0,
  }),
];

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <ProvidersPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/key/status"))
      return {
        provider_name: "sranai", configured: true, total_keys: 1, valid_keys: 1, source: "provider_keys",
        unusable_keys: 1, stale_keys: 0, invalid_keys: 1, base_url: BOX_A, endpoint_configured: true,
      };
    if (path.endsWith("/keys")) return { items: KEYS };
    throw new Error(`unexpected GET ${path}`);
  });
  patch.mockResolvedValue({ changed: true, staled: true, previous: BOX_B, current: "http://box-c/v1" });
  put.mockResolvedValue({ base_url: BOX_A, previous: BOX_A, keys: KEYS });
});
afterEach(cleanup);

async function openPanel() {
  renderPage();
  fireEvent.click(await screen.findByText("Sran AI"));
  await screen.findByTestId("key-row-Typhoon");
}

describe("providers page — an endpoint per key (FIX85)", () => {
  it("shows two keys pointing at two different endpoints, each labelled own/default", async () => {
    await openPanel();
    const typhoon = within(screen.getByTestId("key-row-Typhoon")).getByTestId("key-endpoint");
    const admin = within(screen.getByTestId("key-row-admin")).getByTestId("key-endpoint");
    expect(typhoon.textContent).toContain(BOX_A);
    expect(typhoon.textContent).toMatch(/default|ค่าเริ่มต้น/);
    expect(admin.textContent).toContain(BOX_B);
    expect(admin.textContent).toMatch(/own|ของตัวเอง/);
    // a key on the default is not broken
    expect(typhoon.textContent).not.toMatch(/no endpoint|ยังไม่มี endpoint/);
  });

  it("an invalid verdict says which box said no (Part 6)", async () => {
    await openPanel();
    const row = screen.getByTestId("key-row-admin");
    expect(row.textContent).toMatch(/invalid|ใช้ไม่ได้/);
    expect(row.textContent).toMatch(/test failed at|ทดสอบไม่ผ่านที่/);
    expect(row.textContent).toContain(BOX_B);
    // and the card counts the case, not just "unusable"
    expect(screen.getAllByText(/1 invalid|ใช้ไม่ได้ 1/).length).toBeGreaterThan(0);
  });

  it("the provider-level endpoint is presented as a default, with its own Save", async () => {
    await openPanel();
    const block = screen.getByTestId("default-endpoint");
    expect(block.textContent).toMatch(/Default endpoint|Endpoint เริ่มต้น/);
    expect(block.textContent).toMatch(/no endpoint of their own|ไม่มี endpoint ของตัวเอง/);
    const input = within(block).getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe(BOX_A);
    fireEvent.change(input, { target: { value: "http://box-a2.example.invalid/v1" } });
    fireEvent.click(within(block).getByRole("button"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("/admin/providers/sranai/base_url", { base_url: "http://box-a2.example.invalid/v1" }));
    expect(patch).not.toHaveBeenCalled();
  });

  it("editing one key's endpoint patches that key only", async () => {
    await openPanel();
    fireEvent.click(screen.getByLabelText(/Edit endpoint admin|แก้ endpoint admin/));
    const input = screen.getByLabelText(/^Edit endpoint$|^แก้ endpoint$/) as HTMLInputElement;
    expect(input.value).toBe(BOX_B);
    fireEvent.change(input, { target: { value: "http://box-c.example.invalid/v1" } });
    fireEvent.click(within(screen.getByTestId("key-row-admin")).getByText(/^Save$|^บันทึก$/));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("/admin/providers/sranai/keys/k-admin", { base_url: "http://box-c.example.invalid/v1" }),
    );
    expect(put).not.toHaveBeenCalled();
    // the toast says the key now waits on a Test, not that it is broken
    await screen.findByText(/withheld from requests until you press Test|จนกว่าจะกด Test/);
  });

  it("adding a key sends its own endpoint only when one was typed; the default is not rewritten", async () => {
    post.mockResolvedValue({ id: "k-new", label: "new", valid: true, latency_ms: 10, model_sync: null });
    await openPanel();
    const keyInput = () => document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(keyInput(), { target: { value: "sk-new-key-0123456789abcdef" } });
    fireEvent.click(screen.getByText(/Add & validate|เพิ่มและตรวจสอบ/));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toMatchObject({ api_key: "sk-new-key-0123456789abcdef", base_url: undefined });
    expect(put).not.toHaveBeenCalled();

    post.mockClear();
    fireEvent.change(keyInput(), { target: { value: "sk-new-key2-0123456789abcdef" } });
    fireEvent.change(screen.getByLabelText(/Endpoint \(this key's box\)|Endpoint \(เครื่องของคีย์นี้\)/), {
      target: { value: "http://box-d.example.invalid/v1" },
    });
    fireEvent.click(screen.getByText(/Add & validate|เพิ่มและตรวจสอบ/));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toMatchObject({ base_url: "http://box-d.example.invalid/v1" });
    expect(put).not.toHaveBeenCalled();
  });
});
