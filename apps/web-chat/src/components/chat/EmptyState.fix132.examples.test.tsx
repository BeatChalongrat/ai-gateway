// FIX132 F26 — the starter chips inserted a stub: "เขียน" put "ช่วยร่าง " in
// the composer, and there were no example questions (NIGHT3 j1-03). Like
// Claude's and ChatGPT's starters, a chip now opens a few ready examples;
// picking one fills the composer (nothing is sent), and the list closes.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { EmptyState } from "./EmptyState";

const th = dictionaries.th;
beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F26 — a starter chip offers whole example questions", () => {
  it("เขียน opens three examples; picking one fills the composer with it, not a stub", () => {
    const onPrompt = vi.fn();
    render(<EmptyState onPrompt={onPrompt} />);
    const chip = screen.getByRole("button", { name: th["empty.chip.write"] });
    expect(chip).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(chip);
    expect(onPrompt).not.toHaveBeenCalled();
    expect(chip).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("list", { name: th["empty.chip.write"] });
    const examples = list.querySelectorAll("button");
    expect(examples).toHaveLength(3);
    fireEvent.click(examples[0]);
    expect(onPrompt).toHaveBeenCalledWith(th["empty.example.write.1"]);
    expect(th["empty.example.write.1"].trim().length).toBeGreaterThan(15);
    expect(screen.queryByRole("list", { name: th["empty.chip.write"] })).toBeNull();
  });

  it("every chip has three examples in both languages", () => {
    for (const kind of ["write", "learn", "code", "analyze"]) {
      for (const n of [1, 2, 3]) {
        const key = `empty.example.${kind}.${n}` as keyof typeof th;
        expect(th[key]?.length).toBeGreaterThan(10);
        expect(dictionaries.en[key]?.length).toBeGreaterThan(10);
      }
    }
  });

  it("another chip switches the list; the same chip closes it", () => {
    render(<EmptyState onPrompt={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: th["empty.chip.write"] }));
    fireEvent.click(screen.getByRole("button", { name: th["empty.chip.code"] }));
    expect(screen.getByRole("list", { name: th["empty.chip.code"] })).toBeTruthy();
    expect(screen.queryByRole("list", { name: th["empty.chip.write"] })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: th["empty.chip.code"] }));
    expect(screen.queryByRole("list")).toBeNull();
  });
});
