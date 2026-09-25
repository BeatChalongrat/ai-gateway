// FIX134 D1 — the packaged default is the https stack (the Caddy front door,
// docs/ops/tls.md) with the local CA pinned; the plain-http twin stays for a
// stack that runs without Caddy. Both must pass the validator, and they may
// differ only in scheme, CSP and the CA — a drift between them is a config
// bug, not a deployment choice.
import { describe, expect, it } from "vitest";

import { loadShippedConfig, readShippedJson, shippedCaPath } from "./shipped-config";

const PLAIN = "desktop.config.plain-http.json";

describe("FIX134 — shipped configs", () => {
  it("the default is https on the Caddy ports and pins the local CA", () => {
    const cfg = loadShippedConfig();
    expect(cfg.pageUrl).toBe("https://localhost:3002/chat");
    expect(cfg.allowedOrigins).toEqual(["https://localhost:3002"]);
    expect(cfg.portalOrigin).toBe("https://localhost:3001");
    expect(cfg.gatewayUrl).toBe("https://localhost:8080");
    expect(cfg.chatEdgeUrl).toBe("https://localhost:4000");
    expect(cfg.contentSecurityPolicy).not.toMatch(/http:\/\/localhost:(3001|4000|8080)/);
    expect(shippedCaPath()).toMatch(/infra[\\/]caddy[\\/]certs[\\/]rootCA\.pem$/);
  });

  it("the plain-http twin validates, is http on the same ports and pins no CA", () => {
    const cfg = loadShippedConfig(PLAIN);
    expect(cfg.pageUrl).toBe("http://localhost:3002/chat");
    expect(cfg.allowedOrigins).toEqual(["http://localhost:3002"]);
    expect(cfg.portalOrigin).toBe("http://localhost:3001");
    expect(cfg.gatewayUrl).toBe("http://localhost:8080");
    expect(cfg.chatEdgeUrl).toBe("http://localhost:4000");
    expect(cfg.trustedCaFile).toBeNull();
  });

  it("the two differ only in scheme, CSP and the CA", () => {
    const a = readShippedJson();
    const b = readShippedJson(PLAIN);
    const varying = new Set(["_comment", "pageUrl", "allowedOrigins", "portalOrigin", "gatewayUrl", "chatEdgeUrl", "contentSecurityPolicy", "trustedCaFile"]);
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (varying.has(k)) continue;
      expect(a[k], k).toEqual(b[k]);
    }
    expect(Object.keys(a).filter((k) => !(k in b))).toEqual(["trustedCaFile"]);
    expect(Object.keys(b).filter((k) => !(k in a))).toEqual([]);
  });
});
