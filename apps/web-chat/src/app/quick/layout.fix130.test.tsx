// FIX130 C3.1 — a route whose session check gets no answer shows a Thai
// retry screen; it never bounces through the revoking logout, and Retry
// really checks again. (The quick layout runs the same bootstrap as
// app/chat/layout.tsx and is small enough to mount whole.)
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { redirect } = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("@/lib/auth", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  HOSTED_MODE: false,
  redirectToPortalLogin: redirect,
}));
vi.mock("@/stores/modelStore", () => ({
  useModelStore: { getState: () => ({ loaded: true, loadModels: vi.fn() }) },
}));

import { useI18nStore } from "@/lib/i18n";
import { useAuthStore } from "@/stores/authStore";

import QuickLayout from "./layout";

const initial = useAuthStore.getState();
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  useAuthStore.setState({ ...initial, user: null, ready: false }, true);
  useI18nStore.setState({ locale: "th" });
  redirect.mockReset();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX130 C3.1 — unreachable session check", () => {
  it("503 from /portal/me: a Thai retry screen, no login bounce (no logout)", async () => {
    fetchMock.mockResolvedValue(new Response("down", { status: 503 }));
    render(<QuickLayout><p>app</p></QuickLayout>);
    expect(await screen.findByTestId("session-unreachable")).toHaveTextContent("ตอนนี้เชื่อมต่อ SRAN Chat ไม่ได้");
    expect(screen.queryByText("app")).toBeNull();
    expect(redirect).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/portal/auth/logout"))).toBe(false);
  });

  it("Retry checks again, and a healthy answer opens the app", async () => {
    fetchMock.mockResolvedValueOnce(new Response("down", { status: 503 }));
    render(<QuickLayout><p>app</p></QuickLayout>);
    const retry = await screen.findByTestId("session-retry");
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "u1", tenant_id: "t1" }), { status: 200 }));
    await act(async () => {
      fireEvent.click(retry);
    });
    expect(await screen.findByText("app")).toBeInTheDocument();
    expect(redirect).not.toHaveBeenCalled();
  });

  it("a session the gateway refused still goes to the login page", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    render(<QuickLayout><p>app</p></QuickLayout>);
    await vi.waitFor(() => expect(redirect).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("session-unreachable")).toBeNull();
  });
});
