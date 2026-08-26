// E2 — the session half of the shipped config, and its validator.
//
// Pins: the gateway is an origin that is NOT allow-listed for navigation
// (the page reaches it with fetch); the refresh interval is an integer in
// [1, 59] — strictly under the 60-minute access TTL; the OAuth client id
// matches the dev scheme (desktop-dev ↔ sran-dev); a config that pairs the
// shipped WebView2 client id with the dev scheme is refused.
//
// LIMITS: "under 60" is the only thing the guard knows about the TTL. A
// production access TTL shorter than 60 minutes makes 45 wrong and this test
// would still pass — the number has to move by hand (E2_REPORT.md §4).
import { describe, expect, it } from "vitest";
import path from "node:path";
import { loadConfig, resolveConfigPath, validateConfig } from "../src/main/config";

const APP_ROOT = path.resolve(__dirname, "..");

const base = {
  pageUrl: "http://localhost:3002/chat",
  allowedOrigins: ["http://localhost:3002"],
  portalOrigin: "http://localhost:3001",
  gatewayUrl: "http://localhost:8080",
  chatEdgeUrl: "http://localhost:4000",
  defaultModel: "auto",
  sidecarPath: "sidecar.exe",
  approvalMode: "auto",
  protocolScheme: "sran-dev",
  oauthClient: "desktop-dev",
  oauthProvider: "google",
  sessionRefreshMinutes: 45,
  signInTimeoutSeconds: 300,
  hotkey: "CommandOrControl+Shift+Space",
  contentSecurityPolicy: "default-src 'self'",
};

describe("shipped session config", () => {
  const config = loadConfig(resolveConfigPath(APP_ROOT, {}));

  it("gateway is an origin outside the navigation allow-list", () => {
    expect(config.gatewayUrl).toMatch(/^https?:\/\/[^/]+$/);
    expect(config.allowedOrigins).not.toContain(config.gatewayUrl);
    expect(config.gatewayUrl).not.toBe(config.portalOrigin);
  });

  it("refresh interval is strictly under the 60-minute access TTL", () => {
    expect(Number.isInteger(config.sessionRefreshMinutes)).toBe(true);
    expect(config.sessionRefreshMinutes).toBeGreaterThanOrEqual(1);
    expect(config.sessionRefreshMinutes).toBeLessThan(60);
    expect(config.sessionRefreshMinutes).toBe(45);
  });

  it("dev OAuth client maps to the dev scheme", () => {
    expect(config.oauthClient).toBe("desktop-dev");
    expect(config.protocolScheme).toBe("sran-dev");
    expect(config.oauthProvider).toBe("google");
    expect(config.signInTimeoutSeconds).toBeGreaterThanOrEqual(30);
  });
});

describe("session config validation", () => {
  it("accepts the baseline", () => {
    expect(() => validateConfig(base, "t")).not.toThrow();
  });

  it("refuses a gateway inside the allow-list, or as a URL", () => {
    expect(() => validateConfig({ ...base, gatewayUrl: "http://localhost:3002" }, "t")).toThrow(/must not also/);
    expect(() => validateConfig({ ...base, gatewayUrl: "http://localhost:8080/portal" }, "t")).toThrow(/origin, not a URL/);
    expect(() => validateConfig({ ...base, gatewayUrl: "" }, "t")).toThrow(/non-empty/);
  });

  it("refuses a refresh interval at or above the TTL, zero, or non-integer", () => {
    for (const bad of [60, 0, -1, 45.5, "45"]) {
      expect(() => validateConfig({ ...base, sessionRefreshMinutes: bad }, "t")).toThrow(/sessionRefreshMinutes/);
    }
    expect(() => validateConfig({ ...base, sessionRefreshMinutes: 59 }, "t")).not.toThrow();
    expect(() => validateConfig({ ...base, sessionRefreshMinutes: 1 }, "t")).not.toThrow();
  });

  it("refuses the shipped client id on the dev scheme, and unknown providers", () => {
    expect(() => validateConfig({ ...base, oauthClient: "desktop" }, "t")).toThrow(/sran/);
    expect(() => validateConfig({ ...base, oauthClient: "Desktop Dev" }, "t")).toThrow(/identifier/);
    expect(() => validateConfig({ ...base, oauthProvider: "facebook" }, "t")).toThrow(/google or github/);
    expect(() => validateConfig({ ...base, signInTimeoutSeconds: 5 }, "t")).toThrow(/signInTimeoutSeconds/);
  });
});
