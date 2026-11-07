// FIX132 F18 — error toasts vanished in ~2 s and were not announced.
// NIGHT3 f18: "night3-too-big.pdf: ไฟล์ใหญ่เกิน 200 MB" was on screen from 0
// to 2.0 s and gone by 2.25 s, rendered in a region mounted together with
// its text (screen readers often skip those). Now:
//   · an error toast stays 10 s, has a close button, and is announced
//     through an always-mounted role="alert" region;
//   · an ordinary toast stays long enough to read (≥ 4 s, longer for long
//     text) and is announced through an always-mounted role="status" region.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidebarStore", () => ({ useSidebarStore: (sel: (s: { collapsed: boolean }) => unknown) => sel({ collapsed: false }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { ToastViewport, toast, toastError, useToastStore } from "./Toast";

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", { writable: true, value: (q: string) => ({ matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {} }) });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  useI18nStore.setState({ locale: "th" });
  useToastStore.setState({ message: null, action: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useI18nStore.setState({ locale: "en" });
});

const TOO_BIG = "night3-too-big.pdf: ไฟล์ใหญ่เกิน 200 MB";

describe("FIX132 F18 — errors stay and are announced", () => {
  it("an error toast is still there after 8 s, gone after 10 s, and was announced as an alert", () => {
    render(<ToastViewport />);
    // The alert region exists BEFORE the error, so the text is announced.
    const alert = screen.getByRole("alert");
    expect(alert).toBeEmptyDOMElement();
    act(() => toastError(TOO_BIG));
    expect(alert).toHaveTextContent(TOO_BIG);
    act(() => void vi.advanceTimersByTime(8000));
    expect(screen.getByTestId("toast")).toHaveTextContent(TOO_BIG);
    act(() => void vi.advanceTimersByTime(2100));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("an error toast can be closed at once", () => {
    render(<ToastViewport />);
    act(() => toastError(TOO_BIG));
    fireEvent.click(screen.getByRole("button", { name: dictionaries.th["toast.close"] }));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("an ordinary toast stays at least 4 s (was 2.5 s) and is announced politely", () => {
    render(<ToastViewport />);
    const status = screen.getByRole("status");
    act(() => toast("คัดลอกแล้ว"));
    expect(status).toHaveTextContent("คัดลอกแล้ว");
    act(() => void vi.advanceTimersByTime(3900));
    expect(screen.getByTestId("toast")).toHaveTextContent("คัดลอกแล้ว");
    act(() => void vi.advanceTimersByTime(300));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("a long ordinary toast gets reading time", () => {
    render(<ToastViewport />);
    const long = "ก".repeat(90);
    act(() => toast(long));
    act(() => void vi.advanceTimersByTime(5500));
    expect(screen.getByTestId("toast")).toBeInTheDocument();
  });
});
