// FIX28 §2 — a bubble rendered during a turn (no onEdit) must show "Edit
// message" once the turn ends and the row passes the handler; the memo used to
// swallow that render (Edit only appeared after a reload / re-entering).
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageBubble } from "./MessageBubble";
import type { Message } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));


const user: Message = { id: "u1", role: "user", content: "1 บวก 1", status: "completed", created_at: "2026-08-27T00:00:00Z" } as Message;

describe("MessageBubble edit affordance after a turn", () => {
  it("shows Edit once onEdit arrives on a re-render (memo compares handler presence)", () => {
    const { rerender } = render(<MessageBubble convId="c1" message={user} showFeedback={false} showRegenerate={false} onRegenerate={() => {}} />);
    expect(screen.queryByRole("button", { name: /Edit message|แก้ไขข้อความ/ })).toBeNull();
    rerender(<MessageBubble convId="c1" message={user} showFeedback={false} showRegenerate={false} onRegenerate={() => {}} onEdit={() => {}} />);
    expect(screen.getByRole("button", { name: /Edit message|แก้ไขข้อความ/ })).toBeTruthy();
  });
});
