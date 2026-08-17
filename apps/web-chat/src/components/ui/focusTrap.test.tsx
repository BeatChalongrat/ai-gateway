// B2 — dialog focus trapping (WCAG 2.4.3 / 2.1.2). Pins, for the shared
// Modal and one hand-rolled dialog (ShortcutsDialog):
//   1. opening moves focus INTO the dialog,
//   2. Tab / Shift+Tab wrap between first and last focusable,
//   3. closing restores focus to the previously focused element.

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import { ShortcutsDialog } from "../chat/ShortcutsDialog";
import { Modal } from "./Modal";

let trigger: HTMLButtonElement;

beforeEach(() => {
  // jsdom has no matchMedia; Modal uses it to pick desktop vs sheet mode.
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
  useI18nStore.setState({ locale: "en" });
  trigger = document.createElement("button");
  trigger.textContent = "open";
  document.body.appendChild(trigger);
  trigger.focus();
});

afterEach(() => {
  cleanup();
  trigger.remove();
  vi.unstubAllGlobals();
});

function focusablesIn(root: ParentNode): HTMLElement[] {
  return Array.from(
    root.querySelectorAll<HTMLElement>("button, input, a[href], [tabindex]"),
  );
}

describe("Modal focus trap", () => {
  function renderModal(open: boolean) {
    return (
      <Modal open={open} onClose={() => {}} title="Test dialog">
        <input data-testid="field" />
        <button data-testid="ok">OK</button>
      </Modal>
    );
  }

  it("moves focus into the dialog on open", () => {
    const { baseElement } = render(renderModal(true));
    const dialog = baseElement.querySelector('[role="dialog"]')!;
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(trigger);
  });

  it("wraps Tab at the last focusable and Shift+Tab at the first", () => {
    const { baseElement } = render(renderModal(true));
    const dialog = baseElement.querySelector('[role="dialog"]')!;
    const items = focusablesIn(dialog);
    const first = items[0];
    const last = items[items.length - 1];

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("restores focus to the trigger on close", () => {
    const { rerender } = render(renderModal(true));
    expect(document.activeElement).not.toBe(trigger);
    rerender(renderModal(false));
    expect(document.activeElement).toBe(trigger);
  });
});

describe("ShortcutsDialog focus trap (hand-rolled dialog)", () => {
  it("moves focus in on open and restores it on close", () => {
    const { baseElement, rerender } = render(
      <ShortcutsDialog open onClose={() => {}} />,
    );
    const dialog = baseElement.querySelector('[role="dialog"]')!;
    expect(dialog.contains(document.activeElement)).toBe(true);

    rerender(<ShortcutsDialog open={false} onClose={() => {}} />);
    expect(document.activeElement).toBe(trigger);
  });

  it("wraps Tab inside the dialog", () => {
    const { baseElement } = render(<ShortcutsDialog open onClose={() => {}} />);
    const dialog = baseElement.querySelector('[role="dialog"]')!;
    const items = focusablesIn(dialog);
    expect(items.length).toBeGreaterThan(0);
    const first = items[0];
    const last = items[items.length - 1];

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });
});
