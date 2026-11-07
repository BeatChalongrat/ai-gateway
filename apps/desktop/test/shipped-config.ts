// Test helper — loading the SHIPPED config files on any machine.
//
// Since FIX134 the default config (config/desktop.config.json) pins the
// machine's local CA: trustedCaFile → infra/caddy/certs/rootCA.pem, which is
// gitignored (docs/ops/tls.md: one CA per machine, made with mkcert).
// validateConfig() refuses a config whose CA file is missing — right for a
// build (scripts/dist.js) and for a launch, wrong for the pinning tests: they
// assert the origins, the allow-list and the keys of the shipped file, not
// whether this box (a fresh clone, CI, a worktree) has made its CA yet.
//
// So: read the real file; when the CA it names is absent HERE, validate it
// with trustedCaFile cleared. Nothing else is relaxed — every other rule of
// validateConfig still applies to the real contents. When the CA is present
// the file goes through loadConfig() exactly as the app does.
import fs from "node:fs";
import path from "node:path";

import { loadConfig, validateConfig, type ShellConfig } from "../src/main/config";

export const CONFIG_DIR = path.join(__dirname, "..", "config");
export const DEFAULT_CONFIG_NAME = "desktop.config.json";

export function readShippedJson(name: string = DEFAULT_CONFIG_NAME): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, name), "utf8")) as Record<string, unknown>;
}

/** The CA file a shipped config names, resolved the way validateConfig does; null when it names none. */
export function shippedCaPath(name: string = DEFAULT_CONFIG_NAME): string | null {
  const raw = readShippedJson(name)["trustedCaFile"];
  if (typeof raw !== "string" || raw === "") return null;
  return path.isAbsolute(raw) ? raw : path.resolve(CONFIG_DIR, raw);
}

/** The shipped JSON as this machine can validate it: trustedCaFile cleared only when that CA is not here. */
export function shippedJsonForThisMachine(name: string = DEFAULT_CONFIG_NAME): Record<string, unknown> {
  const obj = readShippedJson(name);
  const ca = shippedCaPath(name);
  return ca !== null && !fs.existsSync(ca) ? { ...obj, trustedCaFile: null } : obj;
}

export function loadShippedConfig(name: string = DEFAULT_CONFIG_NAME): ShellConfig {
  const file = path.join(CONFIG_DIR, name);
  const ca = shippedCaPath(name);
  if (ca === null || fs.existsSync(ca)) return loadConfig(file);
  return validateConfig(shippedJsonForThisMachine(name), file);
}
