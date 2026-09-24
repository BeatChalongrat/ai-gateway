// FIX132 F12 — one place that says "open this chat at that message".
import { beforeEach, describe, expect, it } from "vitest";

import { jumpTargetFromHash, requestMessageJump, useMessageJumpStore } from "./messageJump";

beforeEach(() => useMessageJumpStore.setState({ request: null }));

describe("messageJump", () => {
  it("parses #msg-<id> and ignores anything else", () => {
    expect(jumpTargetFromHash("#msg-4f1c")).toBe("4f1c");
    expect(jumpTargetFromHash("#msg-")).toBeNull();
    expect(jumpTargetFromHash("#other")).toBeNull();
    expect(jumpTargetFromHash("")).toBeNull();
  });
  it("each request is new, even for the same message twice", () => {
    requestMessageJump("c1", "m16");
    const a = useMessageJumpStore.getState().request;
    requestMessageJump("c1", "m16");
    const b = useMessageJumpStore.getState().request;
    expect(a).toMatchObject({ convId: "c1", target: "m16" });
    expect(b!.nonce).toBeGreaterThan(a!.nonce);
  });
});
