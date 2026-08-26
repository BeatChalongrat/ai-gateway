// FIX6 — per-site browser permissions (Claude-in-Chrome model). These pins prove
// the containment logic that does NOT need a live browser: the origin scope rule,
// the per-site gate (first action prompts, always-allow holds for the SAME site,
// a DIFFERENT site prompts again), revoke, the always-prompt categories, the
// four always-refused rules, and that the approved-site list is a read-only VIEW
// the page cannot add to. Live browser UX is owner-gated (bridge + session); the
// gate DECISION is proven here.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  siteScope,
  evaluateBrowserSite,
  alwaysPromptCategory,
  alwaysRefused,
  listApprovedSites,
  looksSensitive,
  BROWSER_SITE_PERM,
  PermissionStore,
} from "../src/main/permissions";

function freshStore(): PermissionStore {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fix6-")), "perm.json");
  return new PermissionStore(f);
}

describe("FIX6 §1.1 — the scope is the ORIGIN", () => {
  it("http(s) → scheme://host[:port], default ports omitted, host lowercased", () => {
    expect(siteScope("https://Example.com/path?q=1")).toBe("https://example.com");
    expect(siteScope("https://example.com:443/")).toBe("https://example.com");
    expect(siteScope("http://example.com:80/")).toBe("http://example.com");
    expect(siteScope("https://example.com:8443/x")).toBe("https://example.com:8443");
  });
  it("subdomains are DIFFERENT sites (the whole point of choosing origin)", () => {
    expect(siteScope("https://docs.example.com")).not.toBe(siteScope("https://intranet.example.com"));
    expect(siteScope("https://example.com")).not.toBe(siteScope("https://www.example.com"));
  });
  it("http and https are different origins (a downgrade re-prompts)", () => {
    expect(siteScope("http://example.com")).not.toBe(siteScope("https://example.com"));
  });
  it("non-http(s) scopes by scheme; garbage → null", () => {
    expect(siteScope("chrome://newtab")).toBe("chrome:");
    expect(siteScope("about:blank")).toBe("about:");
    expect(siteScope("not a url")).toBeNull();
  });
});

describe("FIX6 §2/§3 — per-site gate: first prompts, always-allow holds for the SAME site, DIFFERENT site prompts, revoke re-prompts", () => {
  const A = "https://a.example.com";
  const B = "https://b.example.com";
  const ev = (store: PermissionStore, site: string) => evaluateBrowserSite("browser_click", { ref: 1 }, site, store, "auto", null, null);

  it("first action on a site → NeedsConsent (rule: no grant for browser_site::site)", () => {
    const store = freshStore();
    expect(ev(store, A).decision).toBe("NeedsConsent");
  });
  it("after ALWAYS-allow on site A, a SECOND action on A is Auto — same-origin rule", () => {
    const store = freshStore();
    store.persist(BROWSER_SITE_PERM, A, "allow_always"); // what the consent handler does on "always"
    expect(ev(store, A).decision).toBe("Auto");
  });
  it("a DIFFERENT site still prompts — the grant did NOT leak across origins", () => {
    const store = freshStore();
    store.persist(BROWSER_SITE_PERM, A, "allow_always");
    expect(ev(store, B).decision).toBe("NeedsConsent"); // decided by: B has no grant
  });
  it("allow-ONCE (no record) means the next action on the same site prompts again", () => {
    const store = freshStore();
    // "once" records nothing; so a fresh evaluate still needs consent.
    expect(ev(store, A).decision).toBe("NeedsConsent");
  });
  it("revoke removes the grant → the next action prompts again", () => {
    const store = freshStore();
    store.persist(BROWSER_SITE_PERM, A, "allow_always");
    expect(ev(store, A).decision).toBe("Auto");
    expect(store.revoke(BROWSER_SITE_PERM, A)).toBe(true);
    expect(ev(store, A).decision).toBe("NeedsConsent");
  });
});

describe("FIX6 §2/#4 — the three always-prompt categories still prompt on an ALWAYS-allowed site", () => {
  const A = "https://shop.example.com";
  const approved = () => {
    const s = freshStore();
    s.persist(BROWSER_SITE_PERM, A, "allow_always");
    return s;
  };
  it("download — navigating to a downloadable asset prompts even when approved", () => {
    const r = evaluateBrowserSite("browser_navigate", { url: "https://shop.example.com/invoice.pdf" }, A, approved(), "auto", "https://shop.example.com/invoice.pdf", null);
    expect(r.alwaysPrompt).toBe("download");
    expect(r.decision).toBe("NeedsConsent");
  });
  it("sensitive-info — typing a card-like value prompts even when approved", () => {
    const r = evaluateBrowserSite("browser_type", { ref: 2, text: "4111 1111 1111 1111" }, A, approved(), "auto", null, null);
    expect(r.alwaysPrompt).toBe("sensitive-info");
    expect(r.decision).toBe("NeedsConsent");
  });
  it("authorization — navigating to an OAuth authorize URL prompts even when approved", () => {
    const r = evaluateBrowserSite("browser_navigate", { url: "https://accounts.google.com/o/oauth2/v2/auth?client_id=x" }, "https://accounts.google.com", approved(), "auto", "https://accounts.google.com/o/oauth2/v2/auth?client_id=x", null);
    expect(r.alwaysPrompt).toBe("authorization");
    expect(r.decision).toBe("NeedsConsent");
  });
  it("but in SKIP mode nothing checks — always-prompt categories do not fire", () => {
    const r = evaluateBrowserSite("browser_type", { ref: 2, text: "4111111111111111" }, A, approved(), "skip", null, null);
    expect(r.alwaysPrompt).toBeNull();
    expect(r.decision).toBe("Auto");
  });
});

describe("FIX6 §2 — the four always-refused rules block in EVERY mode, including skip", () => {
  for (const mode of ["manual", "auto", "skip"] as const) {
    it(`purchase is refused in ${mode} mode`, () => {
      const r = evaluateBrowserSite("browser_click", { ref: 1 }, "https://shop.example.com", freshStore(), mode, null, "Place order");
      expect(r.refused?.reason).toMatch(/purchase/);
      expect(r.decision).toBe("Denied");
    });
    it(`account creation is refused in ${mode} mode`, () => {
      const r = evaluateBrowserSite("browser_navigate", { url: "https://x.example.com/signup" }, "https://x.example.com", freshStore(), mode, "https://x.example.com/signup", null);
      expect(r.refused?.reason).toMatch(/account/);
    });
    it(`permanent deletion is refused in ${mode} mode`, () => {
      const r = evaluateBrowserSite("browser_click", { ref: 3 }, "https://x.example.com", freshStore(), mode, null, "Delete account permanently");
      expect(r.refused?.reason).toMatch(/deletion/);
    });
  }
});

describe("FIX6 #8 — the approved-site list is a read-only VIEW the page cannot add to", () => {
  it("listApprovedSites reflects ONLY persisted allow_always browser_site keys", () => {
    const store = freshStore();
    store.persist(BROWSER_SITE_PERM, "https://a.example.com", "allow_always");
    store.persist(BROWSER_SITE_PERM, "https://b.example.com", "allow_always");
    store.persist("write_file", "C:/x", "allow_always"); // a non-site grant is NOT a site
    expect(listApprovedSites(store)).toEqual(["https://a.example.com", "https://b.example.com"]);
  });
  it("a deny_always browser_site key is not an approved site", () => {
    const store = freshStore();
    store.persist(BROWSER_SITE_PERM, "https://a.example.com", "deny_always");
    expect(listApprovedSites(store)).toEqual([]);
  });
});

describe("FIX6 — recogniser blind spots are honest (documented in the report)", () => {
  it("looksSensitive catches card/SSN/token shapes but NOT an ordinary password", () => {
    expect(looksSensitive("4111111111111111")).toBe(true);
    expect(looksSensitive("123-45-6789")).toBe(true);
    expect(looksSensitive("hunter2")).toBe(false); // a real password it cannot catch
  });
  it("alwaysRefused is label/url based — a purchase button labelled 'Continue' is NOT caught", () => {
    expect(alwaysRefused("browser_click", { ref: 1 }, null, "Continue")).toBeNull();
  });
  it("alwaysPromptCategory misses a download triggered by a click (no extension signal)", () => {
    expect(alwaysPromptCategory("browser_click", { ref: 1 }, "https://x.example.com/page")).toBeNull();
  });
});
