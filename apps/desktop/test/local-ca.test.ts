// FIX133 B — the desktop app pins the deployment's local CA: a server
// certificate is accepted exactly when it is issued by that CA, valid now and
// names one of the app's own hosts; everything else keeps Chromium's verdict.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { certificateTrustedByLocalCa, loadLocalCa, trustedHostsOf } from "../src/main/local-ca";

const FIX = path.join(__dirname, "..", "src", "main", "__fixtures__", "local-ca");
const read = (f: string) => fs.readFileSync(path.join(FIX, f), "utf8");

describe("local CA pin", () => {
  const trust = loadLocalCa(path.join(FIX, "ca.crt"), ["localhost", "127.0.0.1"]);

  it("accepts a leaf issued by the pinned CA for one of the app's hosts", () => {
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "localhost", trust)).toBe(true);
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "127.0.0.1", trust)).toBe(true);
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "LOCALHOST", trust)).toBe(true);
  });

  it("refuses a leaf from another CA, even for the right host", () => {
    expect(certificateTrustedByLocalCa(read("other-leaf.crt"), "localhost", trust)).toBe(false);
  });

  it("refuses a host the app does not talk to, and a name the leaf does not carry", () => {
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "example.com", trust)).toBe(false);
    const wide = loadLocalCa(path.join(FIX, "ca.crt"), ["localhost", "evil.test"]);
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "evil.test", wide)).toBe(false);
  });

  it("refuses an expired or not-yet-valid leaf", () => {
    const farFuture = Date.now() + 30 * 365 * 24 * 3600 * 1000;
    const farPast = Date.now() - 30 * 365 * 24 * 3600 * 1000;
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "localhost", trust, farFuture)).toBe(false);
    expect(certificateTrustedByLocalCa(read("leaf.crt"), "localhost", trust, farPast)).toBe(false);
  });

  it("refuses garbage instead of throwing", () => {
    expect(certificateTrustedByLocalCa("not a certificate", "localhost", trust)).toBe(false);
  });

  it("only loads a CA certificate", () => {
    expect(() => loadLocalCa(path.join(FIX, "leaf.crt"), ["localhost"])).toThrow(/not a CA/);
  });

  it("collects the hostnames the app talks to", () => {
    const hosts = trustedHostsOf(["https://localhost:3002/chat", "https://Localhost:8080", "https://192.168.1.196:4000", null, "nope"]);
    expect([...hosts].sort()).toEqual(["192.168.1.196", "localhost"]);
  });
});
