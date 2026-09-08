// FIX86 Part B — chat and portal must read "used" from the same source,
// and a change to one side that does not change the other must fail.
//
// The source is the account ledger: `/portal/me` → `credit_used`. The
// chat-edge handler copies it into `credits.used`; the badge's footer
// renders `credits.used`; the Portal's USED renders `credit_used`. The
// request-log roll-up (`total_credits_used` / `totals.credits_used`) is a
// different fact and may appear only under its own name. Cross-tree, in
// the FIX76/FIX78 drift-test pattern: this file reads the other trees.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(__dirname, "../../../..");
const read = (p: string) => readFileSync(resolve(ROOT, p), "utf8");

describe("one number, one source: the ledger's credit_used", () => {
  it("chat-edge maps credits.used from /portal/me and keeps the roll-up under its own field", () => {
    const go = read("services/chat-edge-api/internal/handler/usage.go");
    expect(go).toMatch(/Used:\s*me\.CreditUsed/);
    expect(go).toMatch(/Remaining:\s*me\.CreditBalance/);
    expect(go).toMatch(/TotalCreditsUsed:\s*totalCredits/);
    // the "0 credits → raw tokens" fallback is gone: a zero is a zero
    expect(go).not.toMatch(/totalCredits\s*=\s*summary\.Totals\.Tokens/);
    expect(go).not.toMatch(/credits\s*=\s*m\.Tokens/);
  });

  it("the chat popover's deducted figure is credits.used, and the roll-up is not shown as used", () => {
    const badge = read("apps/web-chat/src/components/chat/CreditBadge.tsx");
    expect(badge).toMatch(/const deducted = data\.credits\.used/);
    expect(badge).not.toMatch(/total_credits_used\s*\?\?\s*data\.credits\.used/);
    expect(badge).toMatch(/usage\.logTotal/);
  });

  it("the portal's USED and balance come from the same /portal/me fields", () => {
    const dash = read("portal/app/(app)/dashboard/page.tsx");
    const billing = read("portal/app/(app)/billing/page.tsx");
    expect(dash).toMatch(/user\?\.credit_used/);
    expect(dash).toMatch(/user\?\.credit_balance/);
    expect(billing).toMatch(/user\?\.credit_used/);
    expect(billing).toMatch(/user\?\.credit_balance/);
    // and the backend publishes them from the ledger columns, not a roll-up
    const me = read("backend/app/api/routes/portal_me.py");
    expect(me).toMatch(/"credit_used":\s*used/);
    expect(me).toMatch(/used = int\(user\.credit_used\)/);
  });

  // FIX88 Part A — and "full" has one source too: the stored ceiling
  // (portal_users.credit_quota), read by chat-edge and the portal alike.
  // A side that goes back to balance + used as its total fails here.
  it("the total is the ceiling on both sides, never balance + used again", () => {
    const go = read("services/chat-edge-api/internal/handler/usage.go");
    expect(go).toMatch(/CreditQuota\s+int64\s+`json:"credit_quota"`/);
    expect(go).toMatch(/Total:\s*creditsTotal\(me\)/);
    expect(go).not.toMatch(/Total:\s*me\.CreditBalance \+ me\.CreditUsed/);
    const dash = read("portal/app/(app)/dashboard/page.tsx");
    expect(dash).toMatch(/user\.credit_quota/);
    expect(dash).not.toMatch(/const total = balance \+ used;/);
    const me = read("backend/app/api/routes/portal_me.py");
    expect(me).toMatch(/"credit_quota":\s*quota/);
    expect(me).toMatch(/quota = int\(user\.credit_quota\)/);
    // the ceiling is a stored column with a backfill equal to the old derivation
    const mig = read("backend/migrations/versions/088_portal_user_credit_quota.py");
    expect(mig).toMatch(/UPDATE portal_users SET credit_quota = credit_balance \+ credit_used/);
    // and no route writes the balance directly: the ledger service is the only writer
    const admin = read("backend/app/api/routes/admin_portal_users.py");
    expect(admin).not.toMatch(/\.credit_balance\s*[-+]?=/);
    expect(admin).not.toMatch(/\.credit_quota\s*[-+]?=/);
  });

  it("the roll-up is named for what it is on the backend, so nobody mistakes it for the ledger", () => {
    const me = read("backend/app/api/routes/portal_me.py");
    expect(me).toMatch(/Display credits — derived from real logged cost, NOT the/);
  });
});
