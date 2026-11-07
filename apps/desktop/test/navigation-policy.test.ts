// PINNING TEST — the navigation allow-list (E1 §4.4) and its semantics.
//
// Pins: the SHIPPED config file has a non-empty allow-list with no '*', the
// page URL is inside it, the portal origin is NOT; and the policy compares by
// ORIGIN — the prefix trick, the userinfo trick, scheme and port mismatches
// are all refused.
//
// LIMITS — what this test cannot see:
//   * It reads config/desktop.config.json from disk. A deployment that points
//     SRAN_DESKTOP_CONFIG at a different file is validated by the same
//     loadConfig() at startup (a '*' there is a startup failure), but this
//     test never sees that file.
//   * It cannot see whether index.ts actually calls preventDefault() on a
//     refusal — that is the live probe's job (`npm run probe`, checks 1–3).
//   * FIX134: the shipped default pins a per-machine CA (gitignored); on a
//     box without it the file is validated with that one field cleared
//     (test/shipped-config.ts), so the origin rules below are still the
//     real file's.
import { describe, expect, it } from "vitest";
import path from "node:path";
import { resolveConfigPath, validateConfig } from "../src/main/config";
import {
  classifyFrame,
  classifyTopLevel,
  createNavigationPolicy,
  isExternalHttpUrl,
  originOf,
  parseOriginEntry,
} from "../src/main/navigation-policy";
import { CONFIG_DIR, DEFAULT_CONFIG_NAME, loadShippedConfig } from "./shipped-config";

const APP_ROOT = path.resolve(__dirname, "..");

describe("shipped config", () => {
  // The dev resolution still lands on the file this helper loads.
  expect(resolveConfigPath(APP_ROOT, {})).toBe(path.join(CONFIG_DIR, DEFAULT_CONFIG_NAME));
  const config = loadShippedConfig();

  it("allow-list is non-empty and contains no wildcard", () => {
    expect(config.allowedOrigins.length).toBeGreaterThan(0);
    for (const origin of config.allowedOrigins) {
      expect(origin).not.toContain("*");
      expect(origin).toMatch(/^https?:\/\/[^/]+$/); // an origin, not a URL
    }
  });

  it("page URL is inside the allow-list; portal origin is outside it", () => {
    expect(config.allowedOrigins).toContain(originOf(config.pageUrl));
    expect(config.portalOrigin).not.toBeNull();
    expect(config.allowedOrigins).not.toContain(config.portalOrigin);
  });

  it("uses the app's own deep-link scheme, sran:// (FIX34 Part B)", () => {
    expect(config.protocolScheme).toBe("sran");
    expect(config.oauthClient).toBe("desktop");
  });

  it("refuses a config that tries to widen the list", () => {
    const base = {
      pageUrl: "http://localhost:3002/chat",
      allowedOrigins: ["http://localhost:3002"],
      portalOrigin: "http://localhost:3001",
      protocolScheme: "sran-dev",
      hotkey: "CommandOrControl+Shift+Space",
      contentSecurityPolicy: "default-src 'self'",
    };
    expect(() => validateConfig({ ...base, allowedOrigins: ["*"] }, "t")).toThrow(/Wildcards/);
    expect(() => validateConfig({ ...base, allowedOrigins: ["http://*.example.com"] }, "t")).toThrow(/Wildcards/);
    expect(() => validateConfig({ ...base, allowedOrigins: [] }, "t")).toThrow(/at least one/);
    expect(() => validateConfig({ ...base, allowedOrigins: ["http://localhost:3002/chat"] }, "t")).toThrow(/origin, not a URL/);
    expect(() => validateConfig({ ...base, pageUrl: "http://localhost:3001/login" }, "t")).toThrow(/outside/);
    expect(() => validateConfig({ ...base, portalOrigin: "http://localhost:3002" }, "t")).toThrow(/must not also/);
    expect(() => validateConfig({ ...base, protocolScheme: "SRAN" }, "t")).toThrow(/protocolScheme/); // grammar: lower-case only
    expect(() => validateConfig({ ...base, allowedOrigins: ["file:///C:/"] }, "t")).toThrow(/http\(s\)/);
  });
});

describe("origin comparison", () => {
  const policy = createNavigationPolicy(["http://localhost:3002", "https://chat.example.com:443"], "http://localhost:3001");

  it("normalises default ports and case", () => {
    expect(policy.allowedOrigins.has("https://chat.example.com")).toBe(true);
    expect(classifyTopLevel(policy, "HTTPS://CHAT.EXAMPLE.COM/x")).toEqual({ allowed: true });
  });

  it("allows the page origin and about:blank", () => {
    expect(classifyTopLevel(policy, "http://localhost:3002/chat?x=1#y")).toEqual({ allowed: true });
    expect(classifyTopLevel(policy, "about:blank")).toEqual({ allowed: true });
  });

  it("refuses the prefix trick: https://evil.com/?x=<allowed>", () => {
    expect(classifyTopLevel(policy, "https://evil.com/?x=http://localhost:3002")).toEqual({
      allowed: false,
      reason: "outside-allow-list",
    });
  });

  it("refuses the userinfo trick: http://localhost:3002@evil.com/", () => {
    expect(classifyTopLevel(policy, "http://localhost:3002@evil.com/")).toMatchObject({ allowed: false });
  });

  it("refuses scheme, port and host mismatches of an allowed origin", () => {
    expect(classifyTopLevel(policy, "https://localhost:3002/")).toMatchObject({ allowed: false });
    expect(classifyTopLevel(policy, "http://localhost:3003/")).toMatchObject({ allowed: false });
    expect(classifyTopLevel(policy, "http://localhost.evil.com:3002/")).toMatchObject({ allowed: false });
    expect(classifyTopLevel(policy, "http://127.0.0.1:3002/")).toMatchObject({ allowed: false });
  });

  it("classifies the portal as the re-auth signal — refused, not allowed", () => {
    expect(classifyTopLevel(policy, "http://localhost:3001/login?redirect=x")).toEqual({ allowed: false, reason: "portal-reauth" });
  });

  it("refuses non-http schemes and garbage", () => {
    expect(classifyTopLevel(policy, "file:///C:/Windows/system.ini")).toEqual({ allowed: false, reason: "unsupported-scheme" });
    expect(classifyTopLevel(policy, "javascript:alert(1)")).toEqual({ allowed: false, reason: "unsupported-scheme" });
    expect(classifyTopLevel(policy, "not a url")).toEqual({ allowed: false, reason: "unparseable" });
  });

  it("frames: allow-list plus about:blank/about:srcdoc only", () => {
    expect(classifyFrame(policy, "about:srcdoc")).toEqual({ allowed: true });
    expect(classifyFrame(policy, "about:blank")).toEqual({ allowed: true });
    expect(classifyFrame(policy, "https://example.com/")).toMatchObject({ allowed: false });
  });

  it("window.open: only http(s) may reach the system browser", () => {
    expect(isExternalHttpUrl("https://example.com/")).toBe(true);
    expect(isExternalHttpUrl("file:///C:/")).toBe(false);
    expect(isExternalHttpUrl("javascript:1")).toBe(false);
    expect(isExternalHttpUrl("sran-dev://x")).toBe(false);
  });

  it("parseOriginEntry rejects everything that is not a bare origin", () => {
    expect(() => parseOriginEntry("http://a/b", "t")).toThrow();
    expect(() => parseOriginEntry("http://a/?q", "t")).toThrow();
    expect(() => parseOriginEntry("http://u:p@a/", "t")).toThrow();
    expect(() => parseOriginEntry("a.example.com", "t")).toThrow();
    expect(parseOriginEntry(" http://a:80/ ", "t")).toBe("http://a");
  });
});
