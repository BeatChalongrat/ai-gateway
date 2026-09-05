// FIX66 Part 4 — the desktop generate_image path calls the gateway image
// endpoint directly, so a failure there must reach the model as a truthful
// sentence, never raw HTTP/JSON, and never as the chat model's fault.
//
// The sharp case (the owner's screenshot): the image account is out of
// credits. OpenAI returns that as a 429 with code "insufficient_quota".
// The message must (a) name the image service, (b) NOT tell the user to
// "wait and try again" (a top-up is what's needed), (c) say it is separate
// from the chat model.
import { describe, expect, it } from "vitest";

import { classifyDesktopImageError } from "../src/main/gateway-client";

describe("FIX66 — desktop image error classification", () => {
  it("insufficient_quota → out-of-credits, not a passing rate limit", () => {
    const msg = classifyDesktopImageError(
      429,
      '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}',
    );
    expect(msg).toMatch(/out of credits/i);
    expect(msg).toMatch(/separate from the chat model/i);
    expect(msg).not.toMatch(/try again/i);
  });

  it("a genuine rate limit (no quota) stays a wait-and-retry message", () => {
    const msg = classifyDesktopImageError(
      429,
      '{"error":{"code":"rate_limit_exceeded","type":"requests","message":"Rate limit reached"}}',
    );
    expect(msg).toMatch(/busy/i);
    expect(msg).toMatch(/try again/i);
    expect(msg).toMatch(/separate from the chat model/i);
  });

  it("401/permission → an authorization problem, not the chat model", () => {
    const msg = classifyDesktopImageError(401, '{"error":{"message":"no api key"}}');
    expect(msg).toMatch(/not authorized/i);
    expect(msg).not.toMatch(/try again/i);
  });

  it("503 / not configured → contact the administrator", () => {
    const msg = classifyDesktopImageError(503, "service not configured");
    expect(msg).toMatch(/not configured/i);
  });

  it("an unknown failure degrades to a plain retry sentence", () => {
    const msg = classifyDesktopImageError(500, "boom");
    expect(msg).toMatch(/try again/i);
  });
});
