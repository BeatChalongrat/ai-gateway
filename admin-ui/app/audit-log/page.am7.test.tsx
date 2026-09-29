// AM7 — "who allowed our ledger to go to an outside model?" is one search
// in the log auditors already read. The page shows an action as a label
// ("Agent Market.Org.Documents May Leave"); before, its search box matched
// only the raw action ("agent_market.org.documents_may_leave"), the actor
// and the resource, so typing what the page shows found nothing, and the
// admin named in the entry's details (who changed it, from what to what)
// could not be searched at all.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../lib/api", () => ({
  api: { get },
  ApiError: class extends Error {},
}));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";

import AuditLogPage from "./page";

const now = new Date().toISOString();
const rows = [
  {
    id: 2, timestamp: now, actor_key_id: "session:7a7a7a7a-0000-4000-8000-0000000000a1", tenant_id: "__global__",
    action: "agent_market.org.documents_may_leave", resource_type: "agent_market_org", resource_id: "default", outcome: "success",
    details: JSON.stringify({ setting: "documents_may_leave", label: "เอกสารส่งไปโมเดลนอกองค์กรได้", from: false, to: true,
      by: "it-admin@example.test", by_name: "IT Admin", via: "admin-ui" }),
  },
  {
    id: 1, timestamp: now, actor_key_id: "session:someone", tenant_id: "t-1", action: "key_create", resource_type: "api_key",
    resource_id: "k-1", outcome: "success", details: null,
  },
];

beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue({ items: rows, total: rows.length });
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <AuditLogPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

const search = () => screen.getByPlaceholderText(/Search actions|ค้นหา/);

describe("the audit log's search (AM7)", () => {
  for (const [what, typed] of [
    ["the label the page shows", "Documents May Leave"],
    ["the raw action", "documents_may_leave"],
    ["the admin named in the entry", "it-admin@example.test"],
    ["the setting's Thai label", "นอกองค์กร"],
  ] as const) {
    it(`finds the egress change by ${what}`, async () => {
      renderPage();
      await waitFor(() => expect(get).toHaveBeenCalled());
      await screen.findAllByText(/Documents May Leave/);
      fireEvent.change(search(), { target: { value: typed } });
      await waitFor(() => expect(screen.queryAllByText(/Key Create/)).toHaveLength(0));
      expect(screen.getAllByText(/Documents May Leave/).length).toBeGreaterThan(0);
    });
  }
});

it("names an admin-ui session a session, not an API key", async () => {
  renderPage();
  await screen.findAllByText(/Documents May Leave/);
  fireEvent.change(search(), { target: { value: "documents_may_leave" } });
  await waitFor(() => expect(screen.queryAllByText(/Key Create/)).toHaveLength(0));
  expect(screen.getAllByText(/^(Session|เซสชัน)$/).length).toBeGreaterThan(0);
  expect(screen.queryAllByText(/^(API key|คีย์ API)$/)).toHaveLength(0);
});
