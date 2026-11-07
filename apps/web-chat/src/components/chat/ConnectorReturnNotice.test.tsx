// The Connectors page, when a consent flow comes back to it: the link the
// desktop app followed says a flow ended; what the page says is what the
// broker holds — so a hand-made link cannot make it claim a connection.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/chat/connectors",
}));

import { useI18nStore } from "@/lib/i18n";

import { ConnectorReturnNotice } from "./ConnectorReturnNotice";

const broker = (connected: string[]) =>
  vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ connections: connected.map((id) => ({ connector_id: id, connected: true })), available: ["mail"] }),
  });

beforeEach(() => {
  nav.search = "";
  useI18nStore.setState({ locale: "th", hydrated: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a consent flow, come back to the Connectors page", () => {
  it("says the mailbox is connected when the broker holds it — in Thai and in English", async () => {
    nav.search = "connected=mail&status=ok";
    vi.stubGlobal("fetch", broker(["mail"]));
    render(<ConnectorReturnNotice />);
    const notice = await screen.findByTestId("connector-return");
    await waitFor(() => expect(notice.getAttribute("data-tone")).toBe("ok"));
    expect(notice.textContent).toContain("เชื่อมต่อ Gmail (ส่งอย่างเดียว) แล้ว");
    cleanup();

    useI18nStore.setState({ locale: "en", hydrated: true });
    render(<ConnectorReturnNotice />);
    const en = await screen.findByTestId("connector-return");
    await waitFor(() => expect(en.getAttribute("data-tone")).toBe("ok"));
    expect(en.textContent).toContain("Gmail (send only) is connected");
    expect(en.textContent).not.toMatch(/[฀-๿]/);
  });

  it("a link that says ok is not believed when the broker holds nothing", async () => {
    nav.search = "connected=mail&status=ok";
    vi.stubGlobal("fetch", broker([]));
    render(<ConnectorReturnNotice />);
    const notice = await screen.findByTestId("connector-return");
    await waitFor(() => expect(notice.getAttribute("data-tone")).toBe("error"));
    expect(notice.textContent).toContain("ยังเชื่อมต่อ Gmail (ส่งอย่างเดียว) ไม่สำเร็จ");
    expect(notice.textContent).not.toContain("แล้ว กลับไปทำงาน");
  });

  it("a refusal says nothing was connected and nothing changed", async () => {
    nav.search = "connected=mail&status=error&reason=provider_denied";
    vi.stubGlobal("fetch", broker([]));
    render(<ConnectorReturnNotice />);
    const notice = await screen.findByTestId("connector-return");
    await waitFor(() => expect(notice.getAttribute("data-tone")).toBe("error"));
    expect(notice.textContent).toContain("ยังไม่ได้เชื่อมต่อ");
    expect(notice.textContent).toContain("ยังไม่มีอะไรถูกเปลี่ยน");
    expect(notice.textContent).not.toContain("provider_denied"); // a code of ours is not what a person reads
  });

  it("an id that is not a connector's is shown nowhere", async () => {
    nav.search = "connected=%3Cscript%3E&status=ok";
    vi.stubGlobal("fetch", broker([]));
    const { container } = render(<ConnectorReturnNotice />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("connector-return")).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("with no arrival it still says a connected mailbox is connected, and nothing otherwise", async () => {
    vi.stubGlobal("fetch", broker(["mail"]));
    render(<ConnectorReturnNotice />);
    expect((await screen.findByTestId("connector-mailbox")).textContent).toContain("เชื่อมต่ออีเมลของคุณแล้ว");
    cleanup();
    vi.stubGlobal("fetch", broker([]));
    const { container } = render(<ConnectorReturnNotice />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
