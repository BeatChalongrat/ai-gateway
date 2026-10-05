// The connector catalogue is the desktop host's, written in English there. On
// a Thai screen every card said what it does in English, under an English
// category, with a dot whose tooltip read "NotConfigured" (the walk on the
// installed app). A card now reads in the reader's language, and a person can
// search for the words they see.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The catalogue as the sidecar serves it (McpConnectorService), the ones a card is shown for. */
const CATALOGUE = vi.hoisted(() => [
  { id: "slack", name: "Slack", description: "Send messages, read channels, search your Slack workspace", category: "communication", authType: "oauth2", tools: ["slack_search"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "gmail", name: "Gmail", description: "Read, search and draft emails in your Gmail account", category: "communication", authType: "oauth2", tools: ["gmail_search"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "gcal", name: "Google Calendar", description: "Read and create events in your Google Calendar", category: "productivity", authType: "oauth2", tools: ["gcal_list_events"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "warehouse", name: "Warehouse", description: "Query your warehouse", category: "analytics", authType: "oauth2", tools: ["wh_query"], installed: false, status: "Throttled", placeholder: true },
]);

const host = vi.hoisted(() => ({
  listConnectorCatalog: vi.fn(),
  listInstalledConnectors: vi.fn(async () => []),
  oauthStatus: vi.fn(async () => []),
  uninstallConnector: vi.fn(async () => ({ ok: true })),
  installConnector: vi.fn(),
  addCustomConnector: vi.fn(),
  addRemoteConnector: vi.fn(),
  oauthConnect: vi.fn(async () => ({ ok: true })),
  oauthDisconnect: vi.fn(async () => ({ ok: true })),
}));
vi.mock("@/components/ui/Toast", () => ({ toast: () => {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => true }));
vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/lib/hostApi", () => ({ hostApi: host }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import ConnectorsPage from "./page";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  host.listConnectorCatalog.mockResolvedValue(CATALOGUE);
  host.listInstalledConnectors.mockResolvedValue([]);
  host.oauthStatus.mockResolvedValue([]);
});

const texts = (id: string) => screen.getAllByTestId(id).map((e) => e.textContent);

describe("a connector card in the reader's language", () => {
  it("Thai: what it does, its category and its state are Thai; what this app does not know is as the host wrote it", async () => {
    useI18nStore.setState({ locale: "th" });
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getAllByTestId("connector-description").length).toBe(4));
    expect(texts("connector-description")).toEqual([
      dictionaries.th["connectors.catalog.slack"],
      dictionaries.th["connectors.catalog.gmail"],
      dictionaries.th["connectors.catalog.gcal"],
      "Query your warehouse",
    ]);
    expect(texts("connector-category")).toEqual(["การสื่อสาร", "การสื่อสาร", "เครื่องมือทำงาน", "analytics"]);
    expect(screen.getAllByLabelText("ยังไม่ได้ตั้งค่า").length).toBe(3);
    expect(screen.getAllByLabelText("Throttled").length).toBe(1);
    expect(document.body.textContent).not.toContain("Read and create events in your Google Calendar");
  });

  it("English: every card reads exactly as the host wrote it", async () => {
    useI18nStore.setState({ locale: "en" });
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getAllByTestId("connector-description").length).toBe(4));
    expect(texts("connector-description")).toEqual(CATALOGUE.map((c) => c.description));
    expect(texts("connector-category")).toEqual(CATALOGUE.map((c) => c.category));
  });

  it("a search finds a card by the words on it, in the language they are shown in", async () => {
    useI18nStore.setState({ locale: "th" });
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getAllByTestId("connector-description").length).toBe(4));
    const box = document.querySelector("input") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "นัดหมาย" } });
    await waitFor(() => expect(texts("connector-description")).toEqual([dictionaries.th["connectors.catalog.gcal"]]));
    fireEvent.change(box, { target: { value: "events" } }); // the catalogue's own word still finds it
    await waitFor(() => expect(texts("connector-description")).toEqual([dictionaries.th["connectors.catalog.gcal"]]));
  });
});
