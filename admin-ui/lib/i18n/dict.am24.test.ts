// AM24 Part 5 — the Usage page tells a role that may not see usage so in the page's language. AM11 saw it on
// live with a viewer-role account: the gateway's own sentence for a 403 ("Insufficient permissions for admin
// operations.") on a Thai screen.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { en as observabilityEn, th as observabilityTh } from "./observability";

const page = readFileSync(join(__dirname, "..", "..", "app", "usage", "page.tsx"), "utf8");

describe("the Usage page's refusal", () => {
  it("answers a 403 with its own words in both of its loads", () => {
    const refusals = page.match(/err instanceof ApiError && err\.status === 403 \? t\("obs\.usage\.noPermission"\)/g) ?? [];
    expect(refusals.length).toBe(2);
  });

  it("says it in both languages", () => {
    expect(observabilityEn["obs.usage.noPermission"]).toMatch(/project admin/);
    expect(observabilityTh["obs.usage.noPermission"]).toMatch(/[฀-๿]/);
    expect(observabilityTh["obs.usage.noPermission"]).not.toMatch(/Insufficient/);
  });
});
