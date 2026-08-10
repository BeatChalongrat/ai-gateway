// modelStore default-selection tests (UX polish #3): a browser that has
// NEVER picked a model starts in Auto mode; a browser with a persisted
// explicit choice keeps it. The store derives its initial state at
// module-load time from localStorage, so each test resets modules and
// imports the store fresh.

import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    models: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number) {
      super(`http ${status}`);
      this.status = status;
    }
  },
}));

const CATALOG = {
  models: [
    { id: "m-fast", allowed: true },
    { id: "m-smart", allowed: true },
  ],
};

async function freshStore() {
  vi.resetModules();
  return await import("./modelStore");
}

beforeEach(() => {
  window.localStorage.clear();
  mockApi.models.mockReset();
  mockApi.models.mockResolvedValue(CATALOG);
});

describe("fresh browser (no persisted choice)", () => {
  it("boots in Auto mode with the auto sentinel selected", async () => {
    const { useModelStore, AUTO_MODEL_ID } = await freshStore();
    const s = useModelStore.getState();
    expect(s.autoMode).toBe(true);
    expect(s.selectedModelId).toBe(AUTO_MODEL_ID);
  });

  it("stays on Auto after the catalog loads (not allowed[0])", async () => {
    const { useModelStore, AUTO_MODEL_ID } = await freshStore();
    await useModelStore.getState().loadModels();
    const s = useModelStore.getState();
    expect(s.selectedModelId).toBe(AUTO_MODEL_ID);
    expect(s.autoMode).toBe(true);
    expect(s.models.map((m) => m.id)).toEqual(["m-fast", "m-smart"]);
  });
});

describe("persisted explicit choice is never overridden", () => {
  it("keeps the stored model at boot and after load", async () => {
    window.localStorage.setItem("sran-webchat-selected-model", "m-smart");
    window.localStorage.setItem("sran-webchat-auto-mode", "0");
    const { useModelStore } = await freshStore();
    expect(useModelStore.getState().autoMode).toBe(false);
    expect(useModelStore.getState().selectedModelId).toBe("m-smart");

    await useModelStore.getState().loadModels();
    expect(useModelStore.getState().selectedModelId).toBe("m-smart");
    expect(useModelStore.getState().autoMode).toBe(false);
  });

  it("legacy persist shape (selected model, no auto flag) is respected", async () => {
    // Browsers from before AUTO_KEY existed have only the selection —
    // that is still an explicit choice, not a fresh browser.
    window.localStorage.setItem("sran-webchat-selected-model", "m-fast");
    const { useModelStore } = await freshStore();
    expect(useModelStore.getState().autoMode).toBe(false);
    expect(useModelStore.getState().selectedModelId).toBe("m-fast");
  });

  it("persisted Auto choice still loads as Auto", async () => {
    window.localStorage.setItem("sran-webchat-auto-mode", "1");
    const { useModelStore, AUTO_MODEL_ID } = await freshStore();
    await useModelStore.getState().loadModels();
    expect(useModelStore.getState().selectedModelId).toBe(AUTO_MODEL_ID);
  });
});

describe("switching away and back", () => {
  it("selectModel persists and disables auto; selectAuto restores it", async () => {
    const { useModelStore, AUTO_MODEL_ID } = await freshStore();
    await useModelStore.getState().loadModels();

    useModelStore.getState().selectModel("m-smart");
    expect(useModelStore.getState().autoMode).toBe(false);
    expect(window.localStorage.getItem("sran-webchat-auto-mode")).toBe("0");

    useModelStore.getState().selectAuto();
    expect(useModelStore.getState().selectedModelId).toBe(AUTO_MODEL_ID);
    expect(window.localStorage.getItem("sran-webchat-auto-mode")).toBe("1");
  });
});
