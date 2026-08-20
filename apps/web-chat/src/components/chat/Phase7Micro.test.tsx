// Phase 7 — micro-UX fixes: upload progress %, per-message timestamp,
// IME composition guard, per-conversation scroll memory.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { MAX_FILE_SIZE_BYTES } from "@/lib/fileUtil";
import { shouldSendOnEnter } from "@/lib/keyboard";

import { FilePreviewCard } from "./FilePreviewCard";
import { formatMessageTime } from "./MessageBubble";

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

// ─── Task 1: upload progress ────────────────────────────────────

function pendingFile(status: string, progress: number) {
  return {
    id: "p1",
    file: new File(["x".repeat(10)], "report.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }),
    status,
    progress,
  } as unknown as Parameters<typeof FilePreviewCard>[0]["pending"];
}

describe("upload progress", () => {
  it("shows the measured percentage while uploading", () => {
    render(
      <FilePreviewCard pending={pendingFile("uploading", 42)} onRemove={() => {}} />,
    );
    const bar = screen.getByTestId("upload-progress");
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("42%")).toBeInTheDocument();
  });

  it("clamps and rounds out-of-range values", () => {
    render(
      <FilePreviewCard
        pending={pendingFile("uploading", 130.7)}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByTestId("upload-progress")).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
  });

  it("shows no progress overlay once the upload finished", () => {
    render(
      <FilePreviewCard pending={pendingFile("ready", 100)} onRemove={() => {}} />,
    );
    expect(screen.queryByTestId("upload-progress")).toBeNull();
  });

  it("client size limit matches the chat-edge default (200 MiB)", () => {
    // Guards the FE/BE drift the audit found (FE 25 MiB vs BE 200 MiB).
    expect(MAX_FILE_SIZE_BYTES).toBe(209715200);
  });
});

// ─── Task 2: message timestamp ──────────────────────────────────

describe("formatMessageTime", () => {
  const now = new Date("2026-08-06T15:00:00+07:00");

  it("shows time only for a message sent today", () => {
    const out = formatMessageTime("2026-08-06T07:32:00Z", "en", now);
    // 07:32 UTC = 14:32 in Asia/Bangkok (test runner's zone), and the
    // helper always renders in the host zone.
    expect(out).not.toBeNull();
    expect(out!.short).toMatch(/^\d{2}:\d{2}$/);
    expect(out!.short).not.toMatch(/\s/);
  });

  it("adds the date for older messages", () => {
    const out = formatMessageTime("2026-08-01T07:32:00Z", "en", now);
    expect(out!.short).toMatch(/\w+.*\d{2}:\d{2}/);
    expect(out!.short.length).toBeGreaterThan(5);
  });

  it("formats per locale and always exposes the full timestamp", () => {
    const en = formatMessageTime("2026-08-01T07:32:00Z", "en", now);
    const th = formatMessageTime("2026-08-01T07:32:00Z", "th", now);
    expect(en!.short).not.toBe(th!.short); // month name differs
    expect(en!.full.length).toBeGreaterThan(0);
    expect(th!.full.length).toBeGreaterThan(0);
  });

  it("returns null for missing/garbage timestamps instead of crashing", () => {
    expect(formatMessageTime(undefined, "en", now)).toBeNull();
    expect(formatMessageTime("not-a-date", "en", now)).toBeNull();
  });
});

// ─── Task 3: IME composition guard ──────────────────────────────

describe("Enter-to-send IME guard", () => {
  it("sends on a plain Enter", () => {
    expect(shouldSendOnEnter({ key: "Enter", shiftKey: false })).toBe(true);
  });

  it("does NOT send while an IME is composing (isComposing)", () => {
    expect(
      shouldSendOnEnter({ key: "Enter", shiftKey: false, isComposing: true }),
    ).toBe(false);
  });

  it("does NOT send on the legacy keyCode 229 IME signal", () => {
    expect(
      shouldSendOnEnter({ key: "Enter", shiftKey: false, keyCode: 229 }),
    ).toBe(false);
  });

  it("Shift+Enter still inserts a newline instead of sending", () => {
    expect(shouldSendOnEnter({ key: "Enter", shiftKey: true })).toBe(false);
  });

  it("ordinary typing never sends", () => {
    expect(shouldSendOnEnter({ key: "ก", shiftKey: false })).toBe(false);
    expect(shouldSendOnEnter({ key: "a", shiftKey: false })).toBe(false);
  });

  it("Thai typing (no composition) still sends on Enter — the primary flow", () => {
    // Kedmanee never sets isComposing, so this must stay true.
    expect(
      shouldSendOnEnter({
        key: "Enter",
        shiftKey: false,
        isComposing: false,
        keyCode: 13,
      }),
    ).toBe(true);
  });
});
