// FIX89 Part B — the page stops hiding silently, and a card says what the
// connector DOES rather than what its functions are called.
//
// The owner opened this page, counted four cards and asked where the other
// four went. FIX69's rule is right and stays — a card whose server URL is a
// placeholder has nowhere to go, so it is not a card — but the page owed him
// the trace. Collapse, never delete.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: true }));
/** The real embedded starter catalogue, as the sidecar serves it. */
const CATALOGUE = vi.hoisted(() => [
  { id: "slack", name: "Slack", description: "Send and read messages", category: "communication", authType: "oauth2", tools: ["slack_send_message", "slack_read_channel", "slack_search"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "gmail", name: "Gmail", description: "Search and draft mail", category: "communication", authType: "oauth2", tools: ["gmail_search", "gmail_read", "gmail_draft"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "gdrive", name: "Google Drive", description: "Search files", category: "productivity", authType: "oauth2", tools: ["gdrive_search", "gdrive_read"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "notion", name: "Notion", description: "Search and write pages", category: "productivity", authType: "api_key", tools: ["notion_search", "notion_read_page", "notion_create_page"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "github", name: "GitHub", description: "Search repos", category: "developer", authType: "api_key", tools: ["github_search", "github_read_issue", "github_list_prs"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "jira", name: "Jira", description: "Search and update issues", category: "developer", authType: "api_key", tools: ["jira_search", "jira_read_issue", "jira_update_issue"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "gcal", name: "Google Calendar", description: "List and create events", category: "productivity", authType: "oauth2", tools: ["gcal_list_events", "gcal_create_event"], installed: false, status: "NotConfigured", placeholder: true },
  { id: "postgres", name: "PostgreSQL", description: "Read-only queries", category: "data", authType: "api_key", tools: ["postgres_query", "postgres_schema"], installed: false, status: "NotConfigured", placeholder: true },
]);

const host = vi.hoisted(() => ({
  listConnectorCatalog: vi.fn(),
  listInstalledConnectors: vi.fn(async () => []),
  oauthStatus: vi.fn(async (): Promise<{ connectorId: string; configured: boolean; enabled: boolean; connected: boolean; expiresAt: number | null; scopes: string[] }[]> => []),
  uninstallConnector: vi.fn(async () => ({ ok: true })),
  installConnector: vi.fn(),
  addCustomConnector: vi.fn(),
  addRemoteConnector: vi.fn(),
  oauthConnect: vi.fn(async () => ({ ok: true })),
  oauthDisconnect: vi.fn(async () => ({ ok: true })),
}));
const toasts = vi.hoisted(() => ({ shown: [] as string[] }));
vi.mock("@/components/ui/Toast", () => ({ toast: (m: string) => toasts.shown.push(m) }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/lib/hostApi", () => ({ hostApi: host }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));

import { useI18nStore } from "@/lib/i18n";

import ConnectorsPage from "./page";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  toasts.shown.length = 0;
  useI18nStore.setState({ locale: "en" });
  host.listConnectorCatalog.mockResolvedValue(CATALOGUE);
  host.listInstalledConnectors.mockResolvedValue([]);
  host.oauthStatus.mockResolvedValue([]);
});

describe("the connectors it cannot offer yet are named, not hidden", () => {
  it("shows the four cards it can, and NAMES the four it cannot with the reason", async () => {
    render(<ConnectorsPage />);
    const note = await screen.findByTestId("connectors-unavailable");
    // still four cards — FIX69's rule is untouched
    expect(screen.getAllByTestId("connector-access").length).toBe(4);
    expect(screen.queryByText("Notion")).toBeNull();
    // …and now the page says so
    expect(note.textContent).toContain("4 more connectors are not available yet");
    for (const name of ["Notion", "GitHub", "Jira", "PostgreSQL"]) {
      expect(note.textContent).toContain(name);
    }
    expect(note.textContent).toContain("placeholders");
    // nothing in the note is clickable: a card with no destination is not a card
    expect(note.querySelectorAll("button, a").length).toBe(0);
  });

  it("says nothing when every connector is available", async () => {
    host.listConnectorCatalog.mockResolvedValue(CATALOGUE.map((c) => ({ ...c, placeholder: false })));
    render(<ConnectorsPage />);
    await screen.findByText("Slack");
    expect(screen.queryByTestId("connectors-unavailable")).toBeNull();
    expect(screen.getAllByTestId("connector-access").length).toBe(8);
  });

  it("the note is not a search result — it stays while the search box narrows the cards", async () => {
    render(<ConnectorsPage />);
    await screen.findByTestId("connectors-unavailable");
    fireEvent.change(screen.getByLabelText("Search connectors"), { target: { value: "slack" } });
    await waitFor(() => expect(screen.queryByText("Gmail")).toBeNull());
    expect(screen.getByTestId("connectors-unavailable")).toBeTruthy();
  });
});

describe("a card says what it does, not what its functions are called", () => {
  it("reads vs writes, per connector, from the tool names the catalogue already carries", async () => {
    host.listConnectorCatalog.mockResolvedValue(CATALOGUE.map((c) => ({ ...c, placeholder: false })));
    render(<ConnectorsPage />);
    await screen.findByText("Slack");
    const cardOf = (name: string) => screen.getByText(name).closest("section")!;
    expect(cardOf("Slack").textContent).toContain("Reads and changes data");
    expect(cardOf("Google Drive").textContent).toContain("Reads only — changes nothing");
    expect(cardOf("GitHub").textContent).toContain("Reads only — changes nothing");
    expect(cardOf("PostgreSQL").textContent).toContain("Reads only — changes nothing");
    expect(cardOf("Jira").textContent).toContain("Reads and changes data");
    // and the tool ids are gone from the card
    expect(cardOf("Slack").textContent).not.toContain("slack_send_message");
  });

  it("localises to Thai", async () => {
    useI18nStore.setState({ locale: "th" });
    render(<ConnectorsPage />);
    const note = await screen.findByTestId("connectors-unavailable");
    expect(note.textContent).toContain("ยังมีอีก 4 ตัวเชื่อมต่อที่ยังใช้ไม่ได้");
    expect(screen.getAllByTestId("connector-access")[0]!.textContent).toContain("อ่าน");
  });
});

describe("the connect / disconnect toast speaks the user's language", () => {
  it("names the connector through the catalogue, in Thai as well as English", async () => {
    host.listConnectorCatalog.mockResolvedValue(CATALOGUE.map((c) => ({ ...c, placeholder: false })));
    host.oauthStatus.mockResolvedValue([{ connectorId: "slack", configured: true, enabled: true, connected: false, expiresAt: null, scopes: [] }]);
    render(<ConnectorsPage />);
    await screen.findByText("Slack");
    fireEvent.click(screen.getAllByTestId("oauth-action")[0]!);
    await waitFor(() => expect(toasts.shown.length).toBe(1));
    expect(toasts.shown[0]).toBe("Slack connected.");

    useI18nStore.setState({ locale: "th" });
    cleanup();
    toasts.shown.length = 0;
    render(<ConnectorsPage />);
    await screen.findByText("Slack");
    fireEvent.click(screen.getAllByTestId("oauth-action")[0]!);
    await waitFor(() => expect(toasts.shown.length).toBe(1));
    expect(toasts.shown[0]).toBe("เชื่อมต่อ Slack แล้ว");
  });

  it("the needs-an-administrator reason keeps its wording and gains Thai", async () => {
    host.listConnectorCatalog.mockResolvedValue(CATALOGUE.map((c) => ({ ...c, placeholder: false })));
    host.oauthStatus.mockResolvedValue([{ connectorId: "slack", configured: false, enabled: false, connected: false, expiresAt: null, scopes: [] }]);
    render(<ConnectorsPage />);
    await screen.findByText("Slack");
    // every oauth card carries a reason; this asserts Slack's own
    const slack = screen.getByText("Slack").closest("section")!;
    const reason = slack.querySelector('[data-testid="oauth-reason"]')!;
    expect(reason.textContent).toBe("Not set up yet — this connector needs an app registration from your administrator.");
    expect((slack.querySelector('[data-testid="oauth-action"]') as HTMLButtonElement).disabled).toBe(true);
    // a card whose status the host could not report at all says the build reason
    const gmail = screen.getByText("Gmail").closest("section")!;
    expect(gmail.querySelector('[data-testid="oauth-reason"]')!.textContent).toBe("Sign-in for connectors isn’t available in this build.");
  });
});
