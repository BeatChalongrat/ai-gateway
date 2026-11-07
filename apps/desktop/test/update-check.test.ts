// FIX22 §2 — update NOTIFICATION, stage 1. The comparison is written and
// pinned BEFORE the notifier (the prompt's order): "0.1.0-e10" must be newer
// than "0.1.0-e9", a release newer than its pre-releases, garbage never newer
// than anything. Then the notifier: an unconfigured / failing / equal gateway
// posts NOTHING (an unconfigured deployment looks exactly like today), a newer
// version posts exactly one host.notice, and a page reload re-posts it without
// another request. No download, no URL, no electron-updater anywhere.
import { describe, expect, it, vi } from "vitest";

import { compareVersions, isNewerVersion, parseVersion, UpdateNotifier } from "../src/main/update-check";

describe("FIX22 §2 — version comparison (written first)", () => {
  it.each([
    ["0.1.0-e10", "0.1.0-e9", 1],
    ["0.1.0-e9", "0.1.0-e10", -1],
    ["0.1.0-e4", "0.1.0-e3", 1],
    ["0.1.0-e4", "0.1.0-e4", 0],
    ["0.1.0", "0.1.0-e9", 1], // release > its own pre-release
    ["0.1.0-e9", "0.1.0", -1],
    ["0.1.1-e1", "0.1.0-e99", 1], // core wins over pre-release
    ["0.2.0", "0.1.9", 1],
    ["1.0.0", "0.9.9", 1],
    ["0.1.0-e9.1", "0.1.0-e9", 1], // longer pre-release list is higher
    ["0.1.0-rc.2", "0.1.0-rc.1", 1],
    ["0.1.0-alpha", "0.1.0-1", 1], // alpha > numeric (semver §11)
    ["v0.1.0-e4", "0.1.0-e3", 1], // leading v tolerated
    ["0.1.0-e4+build7", "0.1.0-e4", 0], // build metadata ignored
  ] as const)("compareVersions(%s, %s) = %i", (a, b, expected) => {
    expect(compareVersions(a, b)).toBe(expected);
  });

  it("garbage never compares as newer (so a bad feed value cannot show a banner)", () => {
    expect(compareVersions("latest", "0.1.0-e4")).toBe(0);
    expect(compareVersions("0.1.0-e4", "")).toBe(0);
    expect(isNewerVersion("not a version", "0.1.0-e4")).toBe(false);
    expect(isNewerVersion("", "0.1.0-e4")).toBe(false);
    expect(parseVersion("1.2")).toEqual({ core: [1, 2, 0], pre: null });
    expect(parseVersion("x.y")).toBeNull();
  });

  it("isNewerVersion is strictly greater", () => {
    expect(isNewerVersion("0.1.0-e10", "0.1.0-e9")).toBe(true);
    expect(isNewerVersion("0.1.0-e9", "0.1.0-e10")).toBe(false);
    expect(isNewerVersion("0.1.0-e4", "0.1.0-e4")).toBe(false);
  });
});

const log = { info: () => {}, warn: () => {} };

function notifier(latest: string | null | Error, current = "0.1.0-e4") {
  const source = {
    latestDesktopVersion: vi.fn(async () => {
      if (latest instanceof Error) throw latest;
      return latest;
    }),
  };
  const sink = { post: vi.fn() };
  return { n: new UpdateNotifier(source, sink, current, log), source, sink };
}

describe("FIX22 §2 — UpdateNotifier shows nothing unless the gateway is newer", () => {
  it("unconfigured gateway (null / 404 path) → no notice, one request, no retry", async () => {
    const { n, source, sink } = notifier(null);
    expect(await n.check()).toBeNull();
    expect(sink.post).not.toHaveBeenCalled();
    expect(source.latestDesktopVersion).toHaveBeenCalledTimes(1);
  });

  it("empty string → nothing", async () => {
    const { n, sink } = notifier("   ");
    expect(await n.check()).toBeNull();
    expect(sink.post).not.toHaveBeenCalled();
  });

  it("gateway error → nothing, and it never throws", async () => {
    const { n, sink } = notifier(new Error("ECONNREFUSED"));
    await expect(n.check()).resolves.toBeNull();
    expect(sink.post).not.toHaveBeenCalled();
  });

  it("same or older version → nothing", async () => {
    for (const v of ["0.1.0-e4", "0.1.0-e3", "0.0.9"]) {
      const { n, sink } = notifier(v);
      expect(await n.check()).toBeNull();
      expect(sink.post).not.toHaveBeenCalled();
    }
  });

  it("unparsable version → nothing (a version that matches no installer cannot be acted on)", async () => {
    const { n, sink } = notifier("latest");
    expect(await n.check()).toBeNull();
    expect(sink.post).not.toHaveBeenCalled();
  });

  it("newer version → exactly one host.notice carrying latest + current, no URL", async () => {
    const { n, sink } = notifier("0.1.0-e5");
    const notice = await n.check();
    expect(notice).toEqual({ kind: "update-available", latest: "0.1.0-e5", current: "0.1.0-e4" });
    expect(sink.post).toHaveBeenCalledTimes(1);
    expect(sink.post).toHaveBeenCalledWith("host.notice", notice);
    expect(JSON.stringify(notice)).not.toMatch(/https?:|url|download|\.exe/i);
  });

  it("page reload → the pending notice is re-posted WITHOUT a new request", async () => {
    const { n, source, sink } = notifier("0.1.0-e5");
    await n.check();
    n.onPageReady();
    n.onPageReady();
    expect(sink.post).toHaveBeenCalledTimes(3);
    expect(source.latestDesktopVersion).toHaveBeenCalledTimes(1);
  });

  it("page reloads with nothing pending are rate-limited to one check per 10 minutes (no request loop)", async () => {
    const { n, source } = notifier(null);
    n.onPageReady();
    n.onPageReady();
    n.onPageReady();
    await Promise.resolve();
    expect(source.latestDesktopVersion).toHaveBeenCalledTimes(1);
  });
});

describe("FIX22 §2 — what must NOT exist", () => {
  it("no electron-updater dependency, no publish config, no update metadata", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const root = path.resolve(__dirname, "..");
    const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
    expect(Object.keys({ ...pkg.dependencies, ...pkg.devDependencies })).not.toContain("electron-updater");
    const builder = fs.readFileSync(path.join(root, "electron-builder.yml"), "utf8");
    expect(builder).toMatch(/^publish:\s*null\s*$/m);
    expect(fs.existsSync(path.join(root, "release", "latest.yml"))).toBe(false);
    const src = fs.readFileSync(path.join(root, "src", "main", "update-check.ts"), "utf8");
    // Code identifiers only — the module's header comment is allowed to NAME
    // electron-updater as the thing it deliberately is not.
    expect(src).not.toMatch(/autoUpdater|from ["']electron-updater|require\(["']electron-updater|downloadUpdate|quitAndInstall|checkForUpdates/);
  });
});
