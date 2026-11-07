// FIX34 Part B — deep-link params: prefill only (never a send), stripped from
// the address bar, cut at 14,000 code points, id-shaped attach/project only.
import { beforeEach, describe, expect, it } from "vitest";

import { consumeQueryAttach, consumeQueryPrefill, consumeQueryProject, MAX_PREFILL_CHARS } from "./deepLinkParams";

function setUrl(path: string) {
  window.history.replaceState(null, "", path);
}

beforeEach(() => setUrl("/chat"));

describe("consumeQueryPrefill", () => {
  it("returns the decoded q and removes it from the URL", () => {
    setUrl("/chat?q=hello%20%E0%B8%AA%E0%B8%A7%E0%B8%B1%E0%B8%AA%E0%B8%94%E0%B8%B5&x=1");
    expect(consumeQueryPrefill()).toBe("hello สวัสดี");
    expect(window.location.search).toBe("?x=1");
    expect(consumeQueryPrefill()).toBeNull(); // consumed once
  });
  it("returns null when absent or blank", () => {
    expect(consumeQueryPrefill()).toBeNull();
    setUrl("/chat?q=%20%20");
    expect(consumeQueryPrefill()).toBeNull();
  });
  it("cuts at MAX_PREFILL_CHARS code points", () => {
    expect(MAX_PREFILL_CHARS).toBe(14_000);
    setUrl("/chat?q=" + "ก".repeat(15_000));
    expect(Array.from(consumeQueryPrefill()!).length).toBe(14_000);
  });
});

describe("consumeQueryAttach / consumeQueryProject", () => {
  it("accept id-shaped values only", () => {
    setUrl("/chat/c1?attach=file_01HZX-abc&q=hi");
    expect(consumeQueryAttach()).toBe("file_01HZX-abc");
    expect(window.location.search).toBe("?q=hi");
    setUrl("/chat/c1?attach=..%2F..%2Fetc");
    expect(consumeQueryAttach()).toBeNull();
    setUrl("/chat/projects?project=p_9");
    expect(consumeQueryProject()).toBe("p_9");
    expect(window.location.search).toBe("");
    expect(consumeQueryProject()).toBeNull();
  });
});
