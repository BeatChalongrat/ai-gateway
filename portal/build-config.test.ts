// FIX134 A2 / C4 — what the portal image bakes in at `next build`.
//
// With `output: 'standalone'` the rewrite destinations from next.config.js
// are written into the build (routes manifest), so BACKEND_URL is read when
// the image is BUILT, not when the container starts. The image built without
// a BACKEND_URL build arg rewrote /api and /static to http://localhost:8080
// inside the container: /api/health 500, /api/portal/me 503, ECONNREFUSED in
// the portal log, while compose's runtime `BACKEND_URL: http://backend:8080`
// changed nothing. Two pins:
//   1. next.config.js honours BACKEND_URL for both rewrites (and falls back
//      to localhost for `npm run dev` outside Docker);
//   2. portal/Dockerfile declares the ARG/ENV pair in the builder stage
//      BEFORE `npm run build`, so compose's build arg reaches the build.
// C4: image optimisation is off — the standalone image has no sharp.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { NextConfig } from "next";

// __dirname, not `new URL(..., import.meta.url)`: under the jsdom
// environment the global URL is jsdom's, which node:url rejects.
const require = createRequire(import.meta.url);
const CONFIG_PATH = path.join(__dirname, "next.config.js");
const DOCKERFILE_PATH = path.join(__dirname, "Dockerfile");

const ORIGINAL = process.env.BACKEND_URL;

/** Load next.config.js afresh with BACKEND_URL set (or unset). */
function loadConfig(backendUrl: string | undefined): NextConfig {
  if (backendUrl === undefined) delete process.env.BACKEND_URL;
  else process.env.BACKEND_URL = backendUrl;
  delete require.cache[CONFIG_PATH];
  return require(CONFIG_PATH) as NextConfig;
}

type Rewrite = { source: string; destination: string };

async function rewritesOf(config: NextConfig): Promise<Rewrite[]> {
  const result = await config.rewrites!();
  return (Array.isArray(result) ? result : [...result.beforeFiles, ...result.afterFiles, ...result.fallback]) as Rewrite[];
}

afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.BACKEND_URL;
  else process.env.BACKEND_URL = ORIGINAL;
  delete require.cache[CONFIG_PATH];
});

describe("next.config.js rewrites (A2)", () => {
  it("send /api and /static to BACKEND_URL when it is set", async () => {
    const rewrites = await rewritesOf(loadConfig("http://backend:8080"));
    expect(rewrites.map((r) => r.source)).toEqual(["/api/:path((?!auth).*)", "/static/:path*"]);
    for (const r of rewrites) {
      expect(r.destination.startsWith("http://backend:8080/")).toBe(true);
    }
  });

  it("fall back to http://localhost:8080 when BACKEND_URL is unset (npm run dev outside Docker)", async () => {
    const rewrites = await rewritesOf(loadConfig(undefined));
    expect(rewrites).toHaveLength(2);
    for (const r of rewrites) {
      expect(r.destination.startsWith("http://localhost:8080/")).toBe(true);
    }
  });
});

describe("Dockerfile (A2)", () => {
  const lines = readFileSync(DOCKERFILE_PATH, "utf8").split(/\r?\n/);
  const at = (re: RegExp) => lines.findIndex((l) => re.test(l));

  it("declares ARG BACKEND_URL (default http://backend:8080) and its ENV before `npm run build` in the builder stage", () => {
    const builderFrom = at(/^FROM .* AS builder$/);
    const arg = at(/^ARG BACKEND_URL=http:\/\/backend:8080$/);
    const env = at(/^ENV BACKEND_URL=\$BACKEND_URL$/);
    const build = at(/^RUN npm run build$/);
    expect(builderFrom, "builder stage").toBeGreaterThanOrEqual(0);
    expect(build, "RUN npm run build").toBeGreaterThan(builderFrom);
    expect(arg, "ARG BACKEND_URL=http://backend:8080 line").toBeGreaterThan(builderFrom);
    expect(env, "ENV BACKEND_URL=$BACKEND_URL line").toBeGreaterThan(arg);
    expect(env).toBeLessThan(build);
  });
});
