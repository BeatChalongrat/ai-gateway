// FIX131 — how a failed execute call becomes the card's reason.
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { persistedPlanFailure, planFailureFromError } from "@/lib/planFailure";
import type { PlanOutcome } from "@/lib/types";

describe("FIX131 — planFailureFromError", () => {
  it("reads the server's reason; an older server or the network is the generic failure", () => {
    expect(planFailureFromError(new ApiError("X", "m", 422, { reason: "source_damaged", source_filename: "a.pdf" })))
      .toEqual({ reason: "source_damaged", sourceFilename: "a.pdf" });
    expect(planFailureFromError(new ApiError("NOT_FOUND", "source file not found", 404))).toEqual({ reason: "source_not_found" });
    expect(planFailureFromError(new ApiError("X", "m", 502, { reason: "made_up" }))).toEqual({ reason: "internal", sourceFilename: undefined });
    expect(planFailureFromError(new TypeError("Failed to fetch"))).toEqual({ reason: "internal" });
  });
});

describe("FIX131 — persistedPlanFailure", () => {
  const o = (over: Partial<PlanOutcome>): PlanOutcome => ({
    fence: "doc-plan", format: "docx", output_filename: "a.docx", status: "failed", reason: "generator_failed", at: "", ...over,
  });
  it("matches the card by fence and output filename (both absent counts)", () => {
    expect(persistedPlanFailure([o({})], "doc-plan", "a.docx")).toEqual({ reason: "generator_failed", sourceFilename: undefined });
    expect(persistedPlanFailure([o({})], "pptx-plan", "a.docx")).toBeUndefined();
    expect(persistedPlanFailure([o({})], "doc-plan", "b.docx")).toBeUndefined();
    expect(persistedPlanFailure([o({ output_filename: undefined })], "doc-plan", undefined)).toBeTruthy();
    expect(persistedPlanFailure(undefined, "doc-plan", "a.docx")).toBeUndefined();
  });
});
