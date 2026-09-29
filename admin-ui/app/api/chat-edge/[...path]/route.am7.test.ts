// AM7 — the proxy names the signed-in admin to chat-edge and lets an
// agent_publisher through to Agent Market's door only.
//
// Before: every admin-ui call reached chat-edge as the same anonymous bearer
// (chat-edge could not tell who changed a setting), only admin/super_admin
// got through, and a download lost its file name (Content-Disposition was
// dropped).
//
// The route reads its environment when the module loads, so each test sets
// it and imports the route afresh.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Me = { id: string; email: string; username: string; role: string } | null;

let me: Me;
let upstream: { url: string; init: RequestInit }[];

const ADMIN: Me = { id: "a1", email: "it@example.test", username: "IT Admin", role: "admin" };
const PUBLISHER: Me = { id: "p1", email: "lead@example.test", username: "หัวหน้าบัญชี", role: "agent_publisher" };
const VIEWER: Me = { id: "v1", email: "viewer@example.test", username: "Viewer", role: "viewer" };

beforeEach(() => {
  upstream = [];
  vi.stubEnv("CHAT_EDGE_API_URL", "http://edge.test/api/v1");
  vi.stubEnv("CHAT_EDGE_ADMIN_API_KEY", "test-admin-key-not-real");
  vi.stubEnv("GATEWAY_API_URL", "http://gw.test");
  vi.resetModules();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url === "http://gw.test/auth/session/me") {
        return me ? new Response(JSON.stringify({ user: me }), { status: 200 }) : new Response("{}", { status: 401 });
      }
      upstream.push({ url, init });
      if (url.includes("/download")) {
        return new Response("PK", {
          status: 200,
          headers: { "content-type": "application/octet-stream", "content-disposition": 'attachment; filename="report.xlsx"' },
        });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

async function call(method: string, path: string[], body?: unknown) {
  const { GET, PUT } = await import("./route");
  const req = new NextRequest(`http://admin.test/api/chat-edge/${path.join("/")}`, {
    method,
    headers: { cookie: "admin_session=x", "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const handler = method === "PUT" ? PUT : GET;
  return handler(req, { params: { path } });
}

describe("admin-ui → chat-edge proxy (AM7)", () => {
  it("names the signed-in admin to chat-edge next to the bearer", async () => {
    me = ADMIN;
    const res = await call("PUT", ["agent-market", "org"], { documents_may_leave: true });
    expect(res.status).toBe(200);
    expect(upstream).toHaveLength(1);
    expect(upstream[0]!.url).toBe("http://edge.test/api/v1/admin/agent-market/org");
    const h = upstream[0]!.init.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer test-admin-key-not-real");
    expect(h["X-Admin-User-Id"]).toBe("a1");
    expect(h["X-Admin-Email"]).toBe("it@example.test");
    expect(h["X-Admin-Role"]).toBe("admin");
    expect(decodeURIComponent(h["X-Admin-Name"]!)).toBe("IT Admin");
  });

  it("lets an agent_publisher through to Agent Market's door, with a Thai name escaped", async () => {
    me = PUBLISHER;
    const res = await call("GET", ["agent-market", "catalog"]);
    expect(res.status).toBe(200);
    const h = upstream[0]!.init.headers as Record<string, string>;
    expect(h["X-Admin-Role"]).toBe("agent_publisher");
    expect(decodeURIComponent(h["X-Admin-Name"]!)).toBe("หัวหน้าบัญชี");
  });

  it("stops an agent_publisher at every other chat-edge admin path, before chat-edge", async () => {
    me = PUBLISHER;
    for (const p of [["conversations"], ["users"], ["analytics", "usage"], ["knowledge-bases"], ["retention-settings"]]) {
      const res = await call("GET", p);
      expect(res.status, p.join("/")).toBe(403);
    }
    expect(upstream).toHaveLength(0);
  });

  it("stops a viewer everywhere, and a request with no session", async () => {
    me = VIEWER;
    expect((await call("GET", ["agent-market", "org"])).status).toBe(403);
    me = null;
    expect((await call("GET", ["agent-market", "org"])).status).toBe(401);
    expect(upstream).toHaveLength(0);
  });

  it("refuses a path that climbs out of the door", async () => {
    me = PUBLISHER;
    expect((await call("GET", ["agent-market", "..", "conversations"])).status).toBe(400);
    expect(upstream).toHaveLength(0);
  });

  it("keeps an e-mail in a path as it is, and a download's file name", async () => {
    me = ADMIN;
    await call("PUT", ["agent-market", "members", "somchai@example.test"], { role: "member" });
    expect(upstream[0]!.url).toBe("http://edge.test/api/v1/admin/agent-market/members/somchai@example.test");
    const res = await call("GET", ["agent-market", "runs", "r1", "artifacts", "a1", "download"]);
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="report.xlsx"');
  });
});
