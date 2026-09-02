// FIX34 — pins for Part D (the cheatsheet lists every new binding, with
// Claude's alternative key) and Part E (no tool-name row on the landing
// page; the task chips stay).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "beat@example.com" } }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { EmptyState } from "./EmptyState";
import { SHORTCUTS, ShortcutsDialog, renderKey } from "./ShortcutsDialog";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("ShortcutsDialog (FIX34 Part D)", () => {
  it("lists the seven new bindings and shows Ctrl+Shift+O beside Ctrl+Shift+N", () => {
    const labels = SHORTCUTS.map((r) => r.labelKey);
    for (const k of ["shortcuts.copyLast", "shortcuts.editLast", "shortcuts.stop", "shortcuts.focusInput", "shortcuts.deleteConversation", "shortcuts.voice", "shortcuts.settings"]) {
      expect(labels).toContain(k);
    }
    const newChat = SHORTCUTS.find((r) => r.labelKey === "shortcuts.newChat")!;
    expect(newChat.keys).toEqual(["Mod", "Shift", "N"]);
    expect(newChat.altKeys).toEqual(["Mod", "Shift", "O"]);
    render(<ShortcutsDialog open onClose={() => {}} />);
    for (const row of SHORTCUTS) expect(screen.getByText(dictionaries.en[row.labelKey])).toBeTruthy();
    const kbds = Array.from(document.querySelectorAll("kbd")).map((k) => k.textContent);
    expect(kbds).toContain("O");
    expect(kbds).toContain("↑");
    expect(kbds).toContain("Esc");
    expect(kbds).toContain("Backspace");
    expect(renderKey("Backspace", true)).toBe("⌫");
    expect(renderKey("Up", false)).toBe("↑");
  });

  it("every dictionary label the rows use exists in both languages", () => {
    for (const row of SHORTCUTS) {
      expect(dictionaries.en[row.labelKey]).toBeTruthy();
      expect(dictionaries.th[row.labelKey]).toBeTruthy();
    }
  });
});

describe("EmptyState (FIX34 Part E)", () => {
  it("keeps the Write / Learn / Code / Analyze task chips and has NO skills row", () => {
    render(<EmptyState onPrompt={() => {}} />);
    for (const label of ["Write", "Learn", "Code", "Analyze"]) expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByTestId("empty-skills")).toBeNull();
    expect(screen.queryByText(/your skills/i)).toBeNull();
  });
});
