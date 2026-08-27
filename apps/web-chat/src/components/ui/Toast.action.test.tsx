// FIX27 §2.2 — a toast can carry one action (the "Switch to <model>" button).
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToastViewport, toast, useToastStore } from "./Toast";

Object.defineProperty(window, "matchMedia", { writable: true, value: (q: string) => ({ matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {} }) });

vi.mock("@/stores/sidebarStore", () => ({ useSidebarStore: (sel: (s: { collapsed: boolean }) => unknown) => sel({ collapsed: false }) }));

describe("toast action", () => {
  it("renders the action button, runs it on click and clears the toast", () => {
    render(<ToastViewport />);
    const onClick = vi.fn();
    act(() => toast("This model can't read images", { label: "Switch to GPT 5.6 Terra", onClick }));
    const btn = screen.getByRole("button", { name: "Switch to GPT 5.6 Terra" });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(useToastStore.getState().message).toBeNull();
  });

  it("a plain toast has no button", () => {
    render(<ToastViewport />);
    act(() => toast("saved"));
    expect(screen.queryByRole("button")).toBeNull();
  });
});
