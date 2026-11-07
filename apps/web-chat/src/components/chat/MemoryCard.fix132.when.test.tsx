// FIX132 N11 — Memory-page dates older than a week said the date twice:
// "เสนอเมื่อ 10 ก.ย. 2569 (10/9/2569)" (NIGHT3). The "(… ago)" part falls
// back to a plain date after 7 days, so it only adds something within a
// week; after that the line is just the date.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import type { Memory } from "@/lib/types";

import { MemoryWhen } from "./MemoryCard";

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
const mem = (created_at: string) => ({ id: "m", content: "x", created_at }) as unknown as Memory;

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 N11 — the date is said once", () => {
  it("within a week: date + (n วันที่แล้ว)", () => {
    render(<MemoryWhen memory={mem(daysAgo(3))} labelKey="memory.card.suggestedOn" />);
    expect(screen.getByTestId("memory-when")).toHaveTextContent(/^เสนอเมื่อ .+ \(3 วันที่แล้ว\)$/);
  });
  it("older than a week: the date alone, no second date in brackets", () => {
    render(<MemoryWhen memory={mem(daysAgo(14))} labelKey="memory.card.suggestedOn" />);
    const text = screen.getByTestId("memory-when").textContent ?? "";
    expect(text).toMatch(/^เสนอเมื่อ \S/);
    expect(text).not.toMatch(/\(/);
  });
  it("same for saved memories", () => {
    render(<MemoryWhen memory={mem(daysAgo(30))} labelKey="memory.card.savedOn" />);
    expect(screen.getByTestId("memory-when").textContent ?? "").not.toMatch(/\(/);
  });
});
