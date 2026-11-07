// FIX130 A2c — the header chip and its popover say what the chat really does
// with memory, in three states: on, switched off on an ordinary chat, and a
// temporary chat. NIGHT2: the chip read "ไม่บันทึกความจำ" (not saving) while
// the chat was still READING saved memories, and a temporary chat offered to
// turn memory on although the server now refuses that.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { memoryModeOf } from "@/lib/memoryMode";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MemoryIndicator } from "./MemoryIndicator";

const conv = (over: Partial<Conversation>): Conversation =>
  ({ id: "c1", title: "t", model_id: "m", message_count: 0, created_at: "2026-09-04T00:00:00Z", updated_at: "2026-09-04T00:00:00Z", ...over }) as Conversation;
const th = dictionaries.th;

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("memoryModeOf — one rule for every surface", () => {
  it("temporary beats the flag; off only when explicitly false; missing means on", () => {
    expect(memoryModeOf(conv({ is_temporary: true, memory_enabled: true }))).toBe("temporary");
    expect(memoryModeOf(conv({ is_temporary: true, memory_enabled: false }))).toBe("temporary");
    expect(memoryModeOf(conv({ memory_enabled: false }))).toBe("off");
    expect(memoryModeOf(conv({ memory_enabled: true }))).toBe("on");
    expect(memoryModeOf(conv({}))).toBe("on");
    expect(memoryModeOf(undefined)).toBe("on");
  });
});

describe("MemoryIndicator — three honest states", () => {
  it("on: chip ความจำ; the popover says the chat uses saved memory and can save or suggest", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: true })] });
    render(<MemoryIndicator conversationId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(th["memory.indicator.on"]) }));
    expect(screen.getByText(th["memory.popover.onHint"])).toBeTruthy();
    expect(th["memory.popover.onHint"]).toMatch(/ใช้ความจำ/);
    expect(screen.getByRole("menuitem", { name: th["memory.popover.toggleOff"] })).toBeTruthy();
  });

  it("off: chip ปิดความจำ; the popover says the chat neither uses nor saves memory, and offers to turn it on", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: false })] });
    render(<MemoryIndicator conversationId="c1" />);
    expect(th["memory.indicator.off"]).toBe("ปิดความจำ");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(th["memory.indicator.off"]) }));
    expect(th["memory.popover.offHint"]).toMatch(/ไม่ใช้และไม่บันทึกความจำ/);
    expect(screen.getByText(th["memory.popover.offHint"])).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: th["memory.popover.toggleOn"] })).toBeTruthy();
  });

  it("temporary: chip แชทชั่วคราว; no way to turn memory on, only the settings link", () => {
    useChatStore.setState({ conversations: [conv({ memory_enabled: false, is_temporary: true })] });
    render(<MemoryIndicator conversationId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(th["memory.indicator.temporary"]) }));
    expect(screen.getByText(th["memory.popover.temporaryHint"])).toBeTruthy();
    expect(th["memory.popover.temporaryHint"]).toMatch(/ไม่ใช้และไม่บันทึกความจำ/);
    expect(screen.queryByRole("menuitem", { name: th["memory.popover.toggleOn"] })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: th["memory.popover.toggleOff"] })).toBeNull();
    expect(screen.getByRole("menuitem", { name: th["memory.openSettings"] })).toBeTruthy();
  });
});
