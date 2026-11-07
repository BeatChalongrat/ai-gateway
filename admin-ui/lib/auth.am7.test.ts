// AM7 phase 2 — a scoped role, so the finance lead does not need IT.
// agent_publisher sees the dashboard and the Agent Market page, nothing
// else; an admin gets the Agent Market page beside the rest. (The nav is a
// soft gate: chat-edge enforces what each role may do on every endpoint.)

import { describe, expect, it } from "vitest";

import { ROLE_NAV } from "./auth";

describe("ROLE_NAV (AM7)", () => {
  it("gives agent_publisher the Agent Market page only (AM9)", () => {
    expect((ROLE_NAV as Record<string, string[]>).agent_publisher).toEqual(["/agent-market"]); // AM9: no dashboard of errors
  });

  it("gives an admin the Agent Market page", () => {
    expect(ROLE_NAV.admin).toContain("/agent-market");
  });

  it("never gives the Agent Market page to the other roles", () => {
    for (const role of ["viewer", "project_admin", "tenant_admin"] as const) {
      expect(ROLE_NAV[role]).not.toContain("/agent-market");
    }
  });
});
