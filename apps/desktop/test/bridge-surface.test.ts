// PINNING TEST — the preload's exposed surface (E1 §4.2, updated in E3).
//
// E1 pinned "exactly ['surfaceVersion'], no methods" — the state E3 is
// defined to change. This version pins the E3 surface BY NAME: exactly
// ['surfaceVersion','nonce','postMessage','addEventListener'] — two data
// members, two functions — and that the preload source exposes exactly one
// global, 'sranHost', whose frozen literal has exactly those keys. Adding,
// removing or renaming a member without updating BRIDGE_SURFACE_MEMBERS
// fails here; so does exposing ipcRenderer or any of its methods directly.
//
// LIMITS — what this test cannot see:
//   * SOURCE-TEXT assertions on src/preload/index.ts. A member added via a
//     computed key, a spread, or Object.assign evades the literal scan; a
//     second exposeInMainWorld built from strings evades the call scan.
//   * It does not run the preload. The live probes (--e1-probe check 7,
//     --probe-e3 containment) read Object.keys(window.sranHost) from the page
//     and from inside a sandboxed artifact frame.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { BRIDGE_GLOBAL_NAME, BRIDGE_SURFACE_MEMBERS, BRIDGE_SURFACE_VERSION, IPC_CHANNELS } from "../src/shared/bridge-surface";

const PRELOAD_TS = path.resolve(__dirname, "..", "src", "preload", "index.ts");

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "").replace(/([^:"'])\/\/.*$/gm, "$1");
}

describe("exposed bridge surface", () => {
  const source = stripComments(fs.readFileSync(PRELOAD_TS, "utf8"));

  it("declares exactly the four E3 members, by name", () => {
    expect([...BRIDGE_SURFACE_MEMBERS]).toEqual(["surfaceVersion", "nonce", "postMessage", "addEventListener"]);
    expect(BRIDGE_SURFACE_VERSION).toBe(2);
  });

  it("preload exposes one global, 'sranHost', whose literal keys equal the member list", () => {
    const calls = [...source.matchAll(/exposeInMainWorld\(\s*(['"])([^'"]+)\1\s*,\s*(\w+)\s*\)/g)];
    expect(calls.length, "exactly one exposeInMainWorld call").toBe(1);
    const [, , globalName, identifier] = calls[0]!;
    expect(globalName).toBe(BRIDGE_GLOBAL_NAME);
    const literal = new RegExp(`const\\s+${identifier}\\s*=\\s*Object\\.freeze\\(\\{([\\s\\S]*?)\\n\\}\\)`).exec(source);
    expect(literal, `preload must define '${identifier}' as a frozen object literal`).not.toBeNull();
    const body = literal![1]!;
    const keys = [...body.matchAll(/^\s{2}([A-Za-z_$][\w$]*)\s*[:,]/gm)].map((m) => m[1]);
    expect(keys.sort()).toEqual([...BRIDGE_SURFACE_MEMBERS].sort());
    expect(body).not.toMatch(/\.\.\./); // no spreads
    expect(body).not.toMatch(/\[[^\]]*\]\s*:/); // no computed keys
  });

  it("the surface's functions never hand out ipcRenderer or a channel name", () => {
    const literal = /const surface = Object\.freeze\(\{([\s\S]*?)\n\}\)/.exec(source)![1]!;
    // Functions may CALL ipcRenderer.send; the object must not CONTAIN
    // ipcRenderer, a bound ipcRenderer method, or the channel strings as values.
    expect(literal).not.toMatch(/:\s*ipcRenderer\b/);
    expect(literal).not.toMatch(/ipcRenderer\.(send|on|invoke|sendSync|postMessage)\b(?!\()/);
    expect(literal).not.toMatch(/(surfaceVersion|nonce|postMessage|addEventListener)\s*:\s*ipcRenderer/);
    expect(literal).not.toMatch(/invoke|sendSync/); // only fire-and-forget send + on
  });

  it("uses exactly the three declared transport channels and no others", () => {
    const channels = [...source.matchAll(/ipcRenderer\.(?:send|sendSync|on)\(\s*"([^"]+)"/g)].map((m) => m[1]).sort();
    expect(channels).toEqual([IPC_CHANNELS.inbound, IPC_CHANNELS.nonce, IPC_CHANNELS.outbound].sort());
  });
});
