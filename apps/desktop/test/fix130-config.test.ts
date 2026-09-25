// FIX130 A2b — the FIX56.1 `temporaryChatReadsMemory` setting is retired.
// A memory-off or temporary chat reads no memory, full stop; chat-edge no
// longer reads TEMPORARY_CHAT_READS_MEMORY either. A config file written
// before this still carries the key — it must load, and the key must do
// nothing.
import path from "node:path";
import { describe, expect, it } from "vitest";

import { validateConfig } from "../src/main/config";
import { CONFIG_DIR, loadShippedConfig, readShippedJson, shippedJsonForThisMachine } from "./shipped-config";

// FIX134: the shipped default pins a per-machine CA (see test/shipped-config.ts);
// "old.json" sits in the config folder so a relative trustedCaFile resolves as
// it does for the real file.
const shipped = shippedJsonForThisMachine();

describe("FIX130 — temporaryChatReadsMemory is retired", () => {
  it("an old config that still says true or false loads, and the key is not part of the config", () => {
    for (const v of [true, false, "yes"]) {
      const cfg = validateConfig({ ...shipped, temporaryChatReadsMemory: v }, path.join(CONFIG_DIR, "old.json"));
      expect("temporaryChatReadsMemory" in cfg).toBe(false);
    }
  });

  it("the shipped config files no longer carry the key", () => {
    for (const name of ["desktop.config.json", "desktop.config.plain-http.json", "desktop.config.server.example.json"]) {
      expect("temporaryChatReadsMemory" in readShippedJson(name), name).toBe(false);
    }
    expect("temporaryChatReadsMemory" in loadShippedConfig()).toBe(false);
  });
});
