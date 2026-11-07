// FIX25 §1b/§1c — the approval mode as a LIVE setting (sidecar = store of
// record, read per tool call) and the startup rule for `skip` (never sticky,
// user told once). Driven with a scripted sidecar + a recording notice sink.
import { describe, expect, it } from "vitest";

import { ApprovalModeResetter, LiveApprovalMode, parseStoredApprovalMode, startupApprovalMode } from "../src/main/approval-mode";

const noopLog = { info: () => {}, warn: () => {} };

function fakeStore(snapshot: Record<string, unknown> | null, opts: { setOk?: boolean; throwOnGet?: boolean } = {}) {
  const calls: { op: string; args: Record<string, unknown> | undefined }[] = [];
  let current = snapshot;
  const store = {
    hostQuery: async (op: string, args?: Record<string, unknown>) => {
      calls.push({ op, args });
      if (op === "settings.get") {
        if (opts.throwOnGet) throw new Error("pipe closed");
        return current ? { ok: true, data: current } : { ok: false, error: "sidecar-down" };
      }
      if (op === "settings.set") {
        if (opts.setOk === false) return { ok: false, error: "refused" };
        current = { ...(current ?? {}), [String(args?.["key"])]: args?.["value"] };
        return { ok: true, data: { key: args?.["key"], restartRequired: false } };
      }
      return { ok: false, error: "unsupported" };
    },
  };
  return { store, calls, snapshot: () => current };
}

describe("parseStoredApprovalMode — strict, no laundering", () => {
  it("accepts exactly manual|auto|skip (trimmed, case-insensitive) and nothing else", () => {
    expect(parseStoredApprovalMode("manual")).toBe("manual");
    expect(parseStoredApprovalMode(" Skip ")).toBe("skip");
    expect(parseStoredApprovalMode("AUTO")).toBe("auto");
    expect(parseStoredApprovalMode("yolo")).toBeNull(); // parseApprovalMode would say "auto"; here it is null
    expect(parseStoredApprovalMode("")).toBeNull();
    expect(parseStoredApprovalMode(undefined)).toBeNull();
    expect(parseStoredApprovalMode(3)).toBeNull();
  });
});

describe("startupApprovalMode — §1c.3 skip is not sticky", () => {
  it("skip comes back as manual (reset); the other two and 'unknown' pass through", () => {
    expect(startupApprovalMode("skip")).toEqual({ mode: "manual", reset: true });
    expect(startupApprovalMode("auto")).toEqual({ mode: "auto", reset: false });
    expect(startupApprovalMode("manual")).toEqual({ mode: "manual", reset: false });
    expect(startupApprovalMode(null)).toEqual({ mode: null, reset: false });
  });
});

describe("LiveApprovalMode — read per call from the sidecar; config only as fallback", () => {
  it("returns the sidecar's value, and a change is seen on the NEXT read (no caching)", async () => {
    const { store, calls } = fakeStore({ approvalMode: "auto" });
    const live = new LiveApprovalMode(store, "manual", noopLog);
    expect(await live.current()).toBe("auto");
    await store.hostQuery("settings.set", { key: "approvalMode", value: "skip" }); // the composer wrote it
    expect(await live.current()).toBe("skip");
    expect(calls.filter((c) => c.op === "settings.get")).toHaveLength(2); // one read per call
  });

  it("falls back to the config value when the sidecar has no value, errors, or throws", async () => {
    expect(await new LiveApprovalMode(fakeStore({ theme: "Dark" }).store, "manual", noopLog).current()).toBe("manual");
    expect(await new LiveApprovalMode(fakeStore({ approvalMode: "bogus" }).store, "auto", noopLog).current()).toBe("auto");
    expect(await new LiveApprovalMode(fakeStore(null).store, "manual", noopLog).current()).toBe("manual");
    expect(await new LiveApprovalMode(fakeStore({ approvalMode: "skip" }, { throwOnGet: true }).store, "manual", noopLog).current()).toBe("manual");
  });

  it("logs the effective mode once per change of outcome, not on every call", async () => {
    const lines: string[] = [];
    const { store } = fakeStore({ approvalMode: "auto" });
    const live = new LiveApprovalMode(store, "manual", { info: (m) => lines.push(m), warn: () => {} });
    await live.current();
    await live.current();
    await store.hostQuery("settings.set", { key: "approvalMode", value: "manual" });
    await live.current();
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain("approvalMode=auto");
    expect(lines[1]).toContain("approvalMode=manual");
  });
});

describe("ApprovalModeResetter — a stored skip is written back as manual and the page told once", () => {
  it("skip → manual written through settings.set; notice posted when the page is ready; once only", async () => {
    const { store, snapshot, calls } = fakeStore({ approvalMode: "skip" });
    const posted: unknown[] = [];
    const r = new ApprovalModeResetter(store, { post: (_t, p) => posted.push(p) }, noopLog);
    expect(await r.resetIfSkip()).toBe(true);
    expect(snapshot()?.["approvalMode"]).toBe("manual");
    expect(calls.find((c) => c.op === "settings.set")?.args).toEqual({ key: "approvalMode", value: "manual" });
    expect(posted).toHaveLength(0); // page not up yet — held
    r.onPageReady();
    expect(posted).toEqual([{ kind: "approval-mode-reset", from: "skip", to: "manual" }]);
    r.onPageReady(); // a reload later
    expect(posted).toHaveLength(1); // told ONCE
  });

  it("posts immediately when the page was already ready", async () => {
    const { store } = fakeStore({ approvalMode: "skip" });
    const posted: unknown[] = [];
    const r = new ApprovalModeResetter(store, { post: (_t, p) => posted.push(p) }, noopLog);
    r.onPageReady();
    await r.resetIfSkip();
    expect(posted).toHaveLength(1);
  });

  it("manual / auto / no value: nothing written, nothing posted", async () => {
    for (const snap of [{ approvalMode: "manual" }, { approvalMode: "auto" }, { theme: "Dark" }, null]) {
      const { store, calls } = fakeStore(snap);
      const posted: unknown[] = [];
      const r = new ApprovalModeResetter(store, { post: (_t, p) => posted.push(p) }, noopLog);
      expect(await r.resetIfSkip()).toBe(false);
      r.onPageReady();
      expect(calls.filter((c) => c.op === "settings.set")).toHaveLength(0);
      expect(posted).toHaveLength(0);
    }
  });

  it("a refused write leaves skip stored and posts no notice (the user is not told a lie)", async () => {
    const { store, snapshot } = fakeStore({ approvalMode: "skip" }, { setOk: false });
    const posted: unknown[] = [];
    const r = new ApprovalModeResetter(store, { post: (_t, p) => posted.push(p) }, noopLog);
    expect(await r.resetIfSkip()).toBe(false);
    expect(snapshot()?.["approvalMode"]).toBe("skip");
    r.onPageReady();
    expect(posted).toHaveLength(0);
  });
});
