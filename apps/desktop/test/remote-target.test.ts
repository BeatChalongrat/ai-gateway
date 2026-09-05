// FIX69 — where a user-typed remote MCP URL may point (host side). Mirrors
// the sidecar's RemoteTargetPolicy so the user gets the reason before a dial.

import { describe, expect, it } from "vitest";
import { checkRemoteTarget, classifyRemoteUrl, isInternalIp, isMetadataIp } from "../src/main/remote-target";

describe("remote target policy (host)", () => {
  it("accepts https only, with no credentials in the URL", () => {
    expect(classifyRemoteUrl("https://mcp.example.com/mcp").ok).toBe(true);
    expect(classifyRemoteUrl("http://mcp.example.com/mcp")).toEqual({ ok: false, reason: "https-required" });
    expect(classifyRemoteUrl("http://127.0.0.1:9000")).toEqual({ ok: false, reason: "https-required" });
    expect(classifyRemoteUrl("ftp://x")).toEqual({ ok: false, reason: "https-required" });
    expect(classifyRemoteUrl("https://user:pw@mcp.example.com")).toEqual({ ok: false, reason: "userinfo-in-url" });
    expect(classifyRemoteUrl("not a url")).toEqual({ ok: false, reason: "invalid-url" });
    expect(classifyRemoteUrl("")).toEqual({ ok: false, reason: "invalid-url" });
  });

  it("classifies internal and metadata addresses", () => {
    for (const ip of ["127.0.0.1", "10.0.0.1", "172.16.5.5", "172.31.0.1", "192.168.0.1", "169.254.10.10", "100.64.1.1", "0.0.0.0", "::1", "::", "fe80::1", "fd00::1", "fc00::1", "::ffff:192.168.1.1", "224.0.0.1"]) {
      expect(isInternalIp(ip), ip).toBe(true);
    }
    for (const ip of ["8.8.8.8", "172.32.0.1", "100.128.0.1", "2606:4700::1111"]) expect(isInternalIp(ip), ip).toBe(false);
    for (const ip of ["169.254.169.254", "::ffff:169.254.169.254", "100.100.100.200", "fd00:ec2::254"]) expect(isMetadataIp(ip), ip).toBe(true);
    expect(isMetadataIp("169.254.169.253")).toBe(false);
  });

  it("refuses internal targets by default, allows them with the switch, refuses metadata always", async () => {
    const resolve = async (h: string) => (h === "internal.corp" ? ["192.168.10.5"] : h === "meta.corp" ? ["169.254.169.254"] : h === "gone.corp" ? [] : ["93.184.216.34"]);
    expect((await checkRemoteTarget("https://internal.corp/mcp", { allowPrivate: false, resolve })).reason).toBe("internal");
    expect((await checkRemoteTarget("https://internal.corp/mcp", { allowPrivate: true, resolve })).ok).toBe(true);
    expect((await checkRemoteTarget("https://meta.corp/mcp", { allowPrivate: true, resolve })).reason).toBe("metadata");
    expect((await checkRemoteTarget("https://gone.corp/mcp", { allowPrivate: true, resolve })).reason).toBe("unresolvable");
    const ok = await checkRemoteTarget("https://public.example/mcp?x=1", { allowPrivate: false, resolve });
    expect(ok).toMatchObject({ ok: true, origin: "https://public.example", host: "public.example" });
  });

  it("checks a literal IP without resolving, and every address of a name must pass", async () => {
    expect((await checkRemoteTarget("https://127.0.0.1:8443", { allowPrivate: false })).reason).toBe("internal");
    expect((await checkRemoteTarget("https://[::1]:8443", { allowPrivate: false })).reason).toBe("internal");
    expect((await checkRemoteTarget("https://127.0.0.1:8443", { allowPrivate: true })).ok).toBe(true);
    const mixed = async () => ["93.184.216.34", "10.0.0.7"];
    expect((await checkRemoteTarget("https://mixed.example", { allowPrivate: false, resolve: mixed })).reason).toBe("internal");
  });

  it("the scheme rule wins before any resolution", async () => {
    let resolved = false;
    const resolve = async () => {
      resolved = true;
      return ["8.8.8.8"];
    };
    expect((await checkRemoteTarget("http://plain.example", { allowPrivate: true, resolve })).reason).toBe("https-required");
    expect(resolved).toBe(false);
  });
});
