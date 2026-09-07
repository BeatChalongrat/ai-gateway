// FIX81 ⑤ — the session cookies must reach EVERY origin the page calls
// with credentials, not only the gateway. On a split-host deploy (gateway
// and chat-edge on different hosts) the old gateway-only jar meant every
// page→chat-edge call — plan-card execute, downloads, the conversation
// list — carried no cookie and 401'd; dev never showed it because both
// are localhost and cookies ignore ports.
import { describe, expect, it } from "vitest";
import { sessionCookieTargets } from "../src/main/session-controller";

describe("sessionCookieTargets", () => {
  it("split-host: both gateway and chat-edge get the cookies", () => {
    expect(sessionCookieTargets("https://gateway.example.co.th", ["https://gateway.example.co.th", "https://chat.example.co.th"])).toEqual([
      "https://gateway.example.co.th",
      "https://chat.example.co.th",
    ]);
  });

  it("dev single-host: dedupes by origin — one write, exactly as before", () => {
    expect(sessionCookieTargets("http://localhost:8080", ["http://localhost:8080", "http://localhost:8080"])).toEqual(["http://localhost:8080"]);
  });

  it("no extra list → gateway only (the pre-FIX81 behaviour)", () => {
    expect(sessionCookieTargets("http://gw")).toEqual(["http://gw"]);
  });

  it("malformed entries are dropped; an all-malformed list falls back to the gateway", () => {
    expect(sessionCookieTargets("http://gw", ["not a url"])).toEqual(["http://gw"]);
    expect(sessionCookieTargets("http://gw", ["not a url", "http://edge"])).toEqual(["http://edge"]);
  });
});
