// AM21 part 4 — a person sees the finished file before they send it: the files card offers a preview of
// each office file and the PDF, asks the server for the pictures, waits while they are made, and shows
// them; a file whose pictures cannot be made says why. The pictures are the delivered file's own
// (the server made them from the stored file); this test holds the card to asking and showing.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/agents/i/i-1",
}));

import { useI18nStore } from "@/lib/i18n";

import { tAm } from "./i18n";
import type { OutputsPayload } from "./types";
import { OutputsCard } from "./workspace/OutputsCard";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const png = () => ({ ok: true, status: 200, blob: async () => new Blob(["png"], { type: "image/png" }) });
const BASE = "/agent-market";
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;

const outputs: OutputsPayload = {
  run_id: "r-1",
  period_label: "กันยายน 2569",
  artifacts: [
    { id: "a-1", name: "report_2569-09_v1.docx", format: "docx", size: 120_000, version: 1 },
    { id: "a-2", name: "deck_2569-09_v1.pptx", format: "pptx", size: 90_000, version: 1 },
  ],
};

beforeEach(() => {
  raw.mockReset();
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => handler(path, init));
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => `blob:${Math.random()}`), revokeObjectURL: vi.fn() });
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AM21 — the files card previews a delivered file", () => {
  it("asks for the pictures, shows them when they are ready, and says what they are of", async () => {
    let asked = 0;
    handler = (path, init) => {
      if (path === `${BASE}/artifacts/a-1/pages` && init?.method === "POST") {
        asked++;
        return json({ status: "ready", pages: 11, available: 2, dpi: 110 });
      }
      if (path === `${BASE}/artifacts/a-1/pages/1` || path === `${BASE}/artifacts/a-1/pages/2`) return png();
      return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
    };
    render(<OutputsCard outputs={outputs} />);
    const buttons = screen.getAllByTestId("am-artifact-preview");
    expect(buttons).toHaveLength(2); // the report and the deck both offer it
    fireEvent.click(buttons[0]);
    expect(screen.getByTestId("am-pages-pending")).toHaveTextContent(tAm("outputs.previewPending"));
    await waitFor(() => expect(screen.getByTestId("am-pages-strip")).toBeTruthy());
    expect(asked).toBe(1);
    const thumbs = screen.getAllByRole("button", { name: /หน้า \d จาก 11/ });
    expect(thumbs).toHaveLength(2);
    expect(screen.getByText(tAm("outputs.previewMore", { n: "2", total: "11" }))).toBeTruthy();
    // a thumbnail opens large; the same one again closes it
    fireEvent.click(thumbs[1]);
    expect(screen.getByRole("button", { name: tAm("outputs.previewClose") })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: tAm("outputs.previewClose") }));
    expect(screen.queryByRole("button", { name: tAm("outputs.previewClose") })).toBeNull();
    // hiding the preview takes the strip away
    fireEvent.click(screen.getAllByTestId("am-artifact-preview")[0]);
    expect(screen.queryByTestId("am-pages")).toBeNull();
  });

  it("waits while the pictures are being made, then shows them", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let polls = 0;
    handler = (path, init) => {
      if (path === `${BASE}/artifacts/a-2/pages` && init?.method === "POST") return json({ status: "pending", job: "j-1" });
      if (path === `${BASE}/artifacts/a-2/pages`) {
        polls++;
        return json(polls < 2 ? { status: "pending", job: "j-1" } : { status: "ready", pages: 14, available: 1 });
      }
      if (path === `${BASE}/artifacts/a-2/pages/1`) return png();
      return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
    };
    render(<OutputsCard outputs={outputs} />);
    fireEvent.click(screen.getAllByTestId("am-artifact-preview")[1]);
    await waitFor(() => expect(screen.getByTestId("am-pages-pending")).toBeTruthy());
    await vi.advanceTimersByTimeAsync(2100);
    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(screen.getByTestId("am-pages-strip")).toBeTruthy());
    expect(polls).toBeGreaterThanOrEqual(2);
    vi.useRealTimers();
  });

  it("says why when the pictures cannot be made", async () => {
    handler = (path, init) => {
      if (path === `${BASE}/artifacts/a-1/pages` && init?.method === "POST") return json({ status: "unavailable", reason: "no document converter" });
      return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
    };
    render(<OutputsCard outputs={outputs} />);
    fireEvent.click(screen.getAllByTestId("am-artifact-preview")[0]);
    await waitFor(() => expect(screen.getByTestId("am-pages-unavailable")).toHaveTextContent("no document converter"));
    expect(screen.queryByTestId("am-pages-strip")).toBeNull();
  });
});
