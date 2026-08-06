// Phase 8 Task 2 — run_python code+output card.
//
// Covers the four states the user can actually land on (running,
// completed, failed, timed out), the in-place update from "running" to
// terminal, and the EN/TH strings. The store-side upsert (one card per
// tool_call id) is pinned in chatStore.codeExec.test.ts.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { CodeRun } from "@/lib/types";

import { CodeRunCard } from "./CodeRunCard";

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

function run(extra: Partial<CodeRun> = {}): CodeRun {
  return {
    id: "call_1",
    status: "completed",
    code: "print(6 * 7)",
    stdout: "42\n",
    exit_code: 0,
    duration_ms: 1234,
    ...extra,
  };
}

describe("CodeRunCard", () => {
  it("shows the code and a running label while the sandbox job is in flight", () => {
    render(<CodeRunCard run={run({ status: "running", stdout: undefined })} />);

    expect(screen.getByText("Running code…")).toBeInTheDocument();
    expect(screen.getByText("print(6 * 7)")).toBeInTheDocument();
    // No output section yet — an empty "Output" heading would read as
    // "the code produced nothing".
    expect(screen.queryByText("Output")).toBeNull();
  });

  it("shows stdout and the duration once the run completes", () => {
    render(<CodeRunCard run={run()} />);

    expect(screen.getByText("Ran code")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText(/42/)).toBeInTheDocument();
  });

  it("renders stderr and the failed header when the code raised", () => {
    render(
      <CodeRunCard
        run={run({
          status: "error",
          stdout: "",
          stderr: "ZeroDivisionError: division by zero",
          exit_code: 1,
        })}
      />,
    );

    expect(screen.getByText("Code failed")).toBeInTheDocument();
    expect(
      screen.getByText("ZeroDivisionError: division by zero"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("code-run-card")).toHaveAttribute(
      "data-status",
      "error",
    );
  });

  it("says so when the run timed out, and when it printed nothing", () => {
    const { unmount } = render(
      <CodeRunCard
        run={run({ status: "error", stdout: "", timed_out: true })}
      />,
    );
    expect(
      screen.getByText("The code timed out and was stopped."),
    ).toBeInTheDocument();
    unmount();

    render(<CodeRunCard run={run({ stdout: "" })} />);
    expect(screen.getByText("No output")).toBeInTheDocument();
  });

  it("flags truncated output instead of silently showing a partial result", () => {
    render(<CodeRunCard run={run({ truncated: true })} />);
    expect(
      screen.getByText("Output was shortened for display."),
    ).toBeInTheDocument();
  });

  it("re-expands when a collapsed card starts running again", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(<CodeRunCard run={run()} />);
      // Auto-collapse after the finish beat.
      act(() => {
        vi.advanceTimersByTime(1500);
      });
      expect(screen.queryByText("print(6 * 7)")).toBeNull();

      rerender(
        <CodeRunCard run={run({ status: "running", code: "print('again')" })} />,
      );
      expect(screen.getByText("print('again')")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("copies the code to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<CodeRunCard run={run()} />);
    fireEvent.click(screen.getByLabelText("Copy code"));

    expect(writeText).toHaveBeenCalledWith("print(6 * 7)");
  });

  it("localizes to Thai", () => {
    act(() => {
      useI18nStore.setState({ locale: "th" });
    });
    render(<CodeRunCard run={run({ status: "running" })} />);
    expect(screen.getByText("กำลังรันโค้ด…")).toBeInTheDocument();
  });
});
