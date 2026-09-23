// FIX130 A2b — the FIX56.1 `temporaryChatReadsMemory` setting is retired.
// A memory-off or temporary chat reads no memory, full stop; chat-edge no
// longer reads TEMPORARY_CHAT_READS_MEMORY either. A config file written
// before this still carries the key — it must load, and the key must do
// nothing.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { loadConfig, validateConfig } from "../src/main/config";

const CONFIG_DIR = path.join(__dirname, "..", "config");
const shipped = JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, "desktop.config.json"), "utf8")) as Record<string, unknown>;

describe("FIX130 — temporaryChatReadsMemory is retired", () => {
  it("an old config that still says true or false loads, and the key is not part of the config", () => {
    for (const v of [true, false, "yes"]) {
      const cfg = validateConfig({ ...shipped, temporaryChatReadsMemory: v }, "old.json");
      expect("temporaryChatReadsMemory" in cfg).toBe(false);
    }
  });

  it("the shipped config files no longer carry the key", () => {
    for (const name of ["desktop.config.json", "desktop.config.server.example.json"]) {
      const obj = JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, name), "utf8")) as Record<string, unknown>;
      expect("temporaryChatReadsMemory" in obj, name).toBe(false);
    }
    expect("temporaryChatReadsMemory" in loadConfig(path.join(CONFIG_DIR, "desktop.config.json"))).toBe(false);
  });
});
