// P2 (Phase 6a defect round) — a screenshot tool card must show its image
// INLINE once finished, without the user having to expand the card. A
// screenshot tool whose card shows no image is the clearest "I can't tell what
// it did". Before the fix the <img> lived only inside the expand-gated body.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ToolActivity } from "@/lib/types";

import { ToolCard } from "./ToolCard";

afterEach(cleanup);

const IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

function activity(extra: Partial<ToolActivity> = {}): ToolActivity {
  return {
    id: "call_1",
    tool: "computer_screenshot",
    phase: "finished",
    success: true,
    durationMs: 87,
    resultPreview: "Screenshot of the current screen.",
    imageDataUri: IMG,
    ...extra,
  };
}

describe("ToolCard screenshot thumbnail", () => {
  it("shows the screenshot inline on a finished card WITHOUT expanding", () => {
    render(<ToolCard activity={activity()} />);
    // No click on the card header — the image must be visible as-is.
    const img = screen.getByAltText("tool screenshot") as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.getAttribute("src")).toBe(IMG);
  });

  it("does NOT show the image while the tool is still running", () => {
    render(<ToolCard activity={activity({ phase: "started", success: undefined })} />);
    expect(screen.queryByAltText("tool screenshot")).toBeNull();
  });

  it("renders a rich card (not the thin fallback) for an image-only activity", () => {
    // Only an image, no argsSummary/resultPreview/etc. — must still render the
    // card body (data-testid tool-card), not degrade to the one-line row.
    render(
      <ToolCard
        activity={{
          id: "call_2",
          tool: "browser_screenshot",
          phase: "finished",
          success: true,
          imageDataUri: IMG,
        }}
      />,
    );
    expect(screen.getByTestId("tool-card")).toBeInTheDocument();
    expect(screen.getByAltText("tool screenshot")).toBeInTheDocument();
  });
});
