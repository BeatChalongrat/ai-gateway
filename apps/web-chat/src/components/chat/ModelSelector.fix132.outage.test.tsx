// FIX132 F13 — during an outage the model picker blamed missing keys.
// NIGHT3 f13 (with /models held): the chat section spun on "กำลังโหลด…"
// forever while Video / Audio / TTS said "ไม่มีโมเดลให้เลือก — เพิ่มคีย์ผู้ให้
// บริการในหน้าแอดมิน" — telling a user to fix keys they cannot touch, for a
// fault that was the service's. Now a list that could not be loaded says so,
// in Thai, with a retry; the admin-key hint only shows when the list DID
// load and that kind really has no model.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({ mockApi: { models: vi.fn() } }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { AUTO_MODEL_ID, MODELS_LOAD_TIMEOUT, useModelStore } from "@/stores/modelStore";

import { ModelSelector } from "./ModelSelector";

const th = dictionaries.th;
const initial = useModelStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  useModelStore.setState({ ...initial, models: [], loaded: false, loading: false }, true);
  try {
    window.localStorage.clear();
  } catch {
    /* none */
  }
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useI18nStore.setState({ locale: "en" });
});

function openPicker() {
  render(<ModelSelector activeModelId={AUTO_MODEL_ID} onPick={vi.fn()} />);
  fireEvent.click(screen.getAllByRole("button")[0]);
}

describe("FIX132 F13 — the picker says the list could not load, not that keys are missing", () => {
  it("a /models that never answers: after the load deadline, 'can't load now' + retry; no admin-key hint anywhere", async () => {
    vi.useFakeTimers();
    mockApi.models.mockReturnValue(new Promise(() => {}));
    openPicker();
    expect(screen.getAllByText(th["common.loading"]).length).toBeGreaterThan(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(MODELS_LOAD_TIMEOUT.ms + 100);
    });
    expect(screen.getByText(th["model.loadFailed"])).toBeTruthy();
    expect(screen.queryByText(th["model.emptyKind"])).toBeNull();
    expect(document.body.textContent).not.toMatch(/เพิ่มคีย์ผู้ให้บริการ/);
    expect(screen.getAllByRole("button", { name: th["common.retry"] }).length).toBeGreaterThan(0);
  });

  it("a failed /models (5xx) with nothing cached: the same, and retry loads the list", async () => {
    mockApi.models.mockRejectedValueOnce(new Error("503"));
    openPicker();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(th["model.loadFailed"])).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/เพิ่มคีย์ผู้ให้บริการ/);
    mockApi.models.mockResolvedValueOnce({ models: [{ id: "deepseek-v4-flash", name: "Deepseek V4 Flash", provider: "deepseek", description: "", context_window: 1, capabilities: [], icon: "", category: "fast", allowed: true }] });
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: th["common.retry"] })[0]);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getAllByText("Deepseek V4 Flash").length).toBeGreaterThan(0);
    expect(screen.queryByText(th["model.loadFailed"])).toBeNull();
  });

  it("a list that DID load with no video model still gives the admin hint for that kind", async () => {
    mockApi.models.mockResolvedValueOnce({ models: [{ id: "deepseek-v4-flash", name: "Deepseek V4 Flash", provider: "deepseek", description: "", context_window: 1, capabilities: [], icon: "", category: "fast", allowed: true }] });
    openPicker();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getAllByText(th["model.emptyKind"]).length).toBeGreaterThan(0);
  });
});
