// FIX134 C4 — the admin-ui image is built the same way as the portal's
// (`output: 'standalone'`, no sharp), so the image optimiser is off here
// too; a future <Image> then serves the file as-is instead of logging a
// missing-sharp error per request.

import { createRequire } from "node:module";
import path from "node:path";
import { describe, expect, it } from "vitest";

import type { NextConfig } from "next";

// __dirname, not `new URL(..., import.meta.url)`: under the jsdom
// environment the global URL is jsdom's, which node:url rejects.
const require = createRequire(import.meta.url);
const CONFIG_PATH = path.join(__dirname, "next.config.js");

describe("next.config.js (C4)", () => {
  it("keeps the standalone output and turns the image optimiser off", () => {
    delete require.cache[CONFIG_PATH];
    const config = require(CONFIG_PATH) as NextConfig;
    expect(config.output).toBe("standalone");
    expect(config.images?.unoptimized).toBe(true);
  });
});
