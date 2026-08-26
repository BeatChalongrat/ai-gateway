// FIX5 §2 — a 429 is either OUT OF CREDITS or RATE LIMITED, and the advice is
// opposite. These pins feed classify429 the REAL body shapes (the credits body
// was captured live from the gateway for a zero-credit user — HTTP 429; the
// rate-limit body is the shape backend/app/core/exceptions.py emits for
// RateLimitExceededError) and assert the two are told apart. This is not
// "asserting the string I wrote": it asserts the CLASSIFICATION BRANCH — that a
// rate-limited user is never told to buy credits, and a credit-dry user is.
import { describe, expect, it } from "vitest";

import { classify429 } from "../src/main/gateway-client";

// Captured live: curl POST /v1/chat/completions as a 0-credit user → HTTP 429.
const CREDITS_BODY = {
  error: {
    code: "insufficient_credits",
    message: "No credits remaining. Please contact your administrator to add more credits.",
    details: { credit_balance: 0, upgrade_url: "/billing" },
  },
};

// The per-key limiter's shape (RateLimitExceededError → HTTP 429).
const RATE_LIMIT_BODY = {
  error: { code: "rate_limit_exceeded", message: "Too many requests. Please retry later." },
};

describe("FIX5 §2 — classify429 tells credits and rate-limit apart", () => {
  it("insufficient_credits → a CREDITS message naming the account, not a rate-limit one", () => {
    const r = classify429(CREDITS_BODY, null, " (account suda@acme.ai.gateway)");
    expect(r.code).toBe("insufficient_credits");
    expect(r.message).toMatch(/add credits/i);
    expect(r.message).toContain("suda@acme.ai.gateway");
    expect(r.message).not.toMatch(/no purchase is needed/i); // that's the rate-limit line
  });

  it("rate_limit_exceeded → a RATE-LIMIT message that never mentions buying credits", () => {
    const r = classify429(RATE_LIMIT_BODY, null, "");
    expect(r.message).toMatch(/rate limit/i);
    expect(r.message).toMatch(/no purchase is needed/i);
    expect(r.message).not.toMatch(/add credits|buy credits/i);
  });

  it("a bare 429 with no parseable body is treated as rate-limit, NOT credits (safe default)", () => {
    const r = classify429(null, null, "");
    expect(r.code).toBe("rate_limited");
    expect(r.message).not.toMatch(/credits/i);
  });

  it("a Retry-After header is surfaced in the rate-limit message", () => {
    const r = classify429(RATE_LIMIT_BODY, "30", "");
    expect(r.message).toContain("30s");
  });

  it("classifies on details.credit_balance even if the code were absent", () => {
    const r = classify429({ error: { message: "x", details: { credit_balance: 0 } } }, null, "");
    expect(r.code).toBe("insufficient_credits");
  });
});
