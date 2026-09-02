// FIX47.6 Part D — the clipboard helper's honesty contract:
// writeText success → true; writeText REFUSED (the desktop shell's
// permission-denied state, not just absence) → the execCommand fallback
// runs and its boolean is honoured; both failing → false, so the caller
// can show the failure instead of a silent no-op.

import { afterEach, describe, expect, it, vi } from "vitest";

import { copyTextToClipboard } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubClipboard(writeText: (() => Promise<void>) | undefined) {
  vi.stubGlobal("navigator", {
    ...navigator,
    clipboard: writeText ? { writeText } : undefined,
  });
}

describe("copyTextToClipboard", () => {
  it("returns true when writeText resolves", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    await expect(copyTextToClipboard("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("falls back to execCommand when writeText REJECTS (permission denied)", async () => {
    stubClipboard(vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")));
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as unknown as typeof document.execCommand;
    await expect(copyTextToClipboard("hello")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("returns false when writeText rejects AND execCommand returns false", async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    document.execCommand = vi.fn().mockReturnValue(false) as unknown as typeof document.execCommand;
    await expect(copyTextToClipboard("hello")).resolves.toBe(false);
  });

  it("returns false when writeText is absent and execCommand throws", async () => {
    stubClipboard(undefined);
    document.execCommand = vi.fn().mockImplementation(() => { throw new Error("unsupported"); }) as unknown as typeof document.execCommand;
    await expect(copyTextToClipboard("hello")).resolves.toBe(false);
  });
});
