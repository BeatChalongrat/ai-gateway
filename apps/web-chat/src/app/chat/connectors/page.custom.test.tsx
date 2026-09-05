// FIX68 — the Connectors page shows the connectors the user ADDED (custom MCP
// servers) next to the catalogue: their live tool names from the host, a
// Custom badge, and an Uninstall that goes through the host op. Before this
// the page listed the catalogue only, so an added server had no card and no
// way back out.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: true }));
const host = vi.hoisted(() => ({
  listConnectorCatalog: vi.fn(async () => [
    { id: "github", name: "GitHub", description: "Search repos", category: "developer", authType: "api_key", tools: ["github_search"], installed: false, status: "NotConfigured" },
  ]),
  listInstalledConnectors: vi.fn(async () => [
    { id: "custom-demo-mcp-server", isCustom: true, name: "demo-mcp-server", tools: ["fix68_hello", "fix68_add_numbers"], status: "Connected" },
    { id: "github", isCustom: false, name: null, tools: [], status: "NotConfigured" },
  ]),
  oauthStatus: vi.fn(async () => []),
  uninstallConnector: vi.fn(async () => ({ ok: true })),
  installConnector: vi.fn(),
  addCustomConnector: vi.fn(async () => ({ ok: true, data: { added: false } })),
  addRemoteConnector: vi.fn(async () => ({ ok: true, data: { added: false } })),
  oauthConnect: vi.fn(),
  oauthDisconnect: vi.fn(),
}));
const toasts = vi.hoisted(() => ({ shown: [] as string[] }));
vi.mock("@/components/ui/Toast", () => ({ toast: (m: string) => toasts.shown.push(m) }));

vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/lib/hostApi", () => ({ hostApi: host }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <a>{children}</a> }));

import ConnectorsPage from "./page";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
});

describe("/chat/connectors — custom connectors (FIX68)", () => {
  it("lists an added custom connector with its live tools and a Custom badge, ahead of the catalogue", async () => {
    render(<ConnectorsPage />);
    const card = await screen.findByTestId("connector-card-custom-demo-mcp-server");
    expect(card).toHaveTextContent("demo-mcp-server");
    expect(card).toHaveTextContent("Custom");
    expect(screen.getByTestId("connector-tools")).toHaveTextContent("fix68_hello · fix68_add_numbers");
    // a non-custom installed record is NOT rendered as a custom card
    expect(screen.queryByTestId("connector-card-github")).toBeNull();
    // the custom card precedes the catalogue card
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings.indexOf("demo-mcp-server")).toBeLessThan(headings.indexOf("GitHub"));
  });

  it("Uninstall goes through the host op and the card disappears on reload", async () => {
    render(<ConnectorsPage />);
    await screen.findByTestId("connector-card-custom-demo-mcp-server");
    host.listInstalledConnectors.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByTestId("connector-uninstall"));
    await waitFor(() => expect(host.uninstallConnector).toHaveBeenCalledWith("custom-demo-mcp-server"));
    await waitFor(() => expect(screen.queryByTestId("connector-card-custom-demo-mcp-server")).toBeNull());
  });

  it("FIX69: the Add button opens a two-way menu; the remote choice asks the HOST to open its form (the page sends nothing)", async () => {
    render(<ConnectorsPage />);
    await screen.findByTestId("connector-card-custom-demo-mcp-server");
    expect(screen.queryByTestId("add-custom-menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /add custom connector/i }));
    expect(screen.getByTestId("add-custom-menu")).toBeTruthy();
    fireEvent.click(screen.getByTestId("add-custom-remote"));
    await waitFor(() => expect(host.addRemoteConnector).toHaveBeenCalledTimes(1));
    expect(host.addRemoteConnector).toHaveBeenCalledWith(); // no url, no key — ever
    expect(host.addCustomConnector).not.toHaveBeenCalled();
  });

  it("FIX69: a rejected key is reported as REJECTED, an http URL as https-only, an internal target by reason", async () => {
    render(<ConnectorsPage />);
    await screen.findByTestId("connector-card-custom-demo-mcp-server");
    for (const [error, expected] of [
      ["test-failed:key-rejected", /rejected the key/i],
      ["remote-refused:https-required", /only https/i],
      ["remote-refused:internal", /inside your network/i],
      ["remote-refused:metadata", /metadata/i],
      ["test-failed:target-internal", /inside your network/i],
      ["test-failed:not-mcp", /didn't respond as an MCP server/i],
    ] as const) {
      toasts.shown.length = 0;
      host.addRemoteConnector.mockResolvedValueOnce({ ok: false, error } as never);
      fireEvent.click(screen.getByRole("button", { name: /add custom connector/i }));
      fireEvent.click(screen.getByTestId("add-custom-remote"));
      await waitFor(() => expect(toasts.shown.length).toBe(1));
      expect(toasts.shown[0], error).toMatch(expected);
      expect(toasts.shown[0], error).not.toMatch(/try again/i);
    }
  });

  it("FIX69: a remote connector card shows the origin and THAT a key is set — nothing else", async () => {
    host.listInstalledConnectors.mockResolvedValueOnce([
      { id: "custom-mcp-example-com", isCustom: true, name: "mcp.example.com", tools: ["t1"], status: "Connected", hasKey: true, transport: "http", serverUrl: "https://mcp.example.com/mcp?x=1" },
    ] as never);
    render(<ConnectorsPage />);
    const card = await screen.findByTestId("connector-card-custom-mcp-example-com");
    expect(card).toHaveTextContent("Remote");
    expect(screen.getByTestId("connector-origin")).toHaveTextContent("https://mcp.example.com");
    expect(screen.getByTestId("connector-key-set")).toHaveTextContent("Key set");
    expect(card.textContent).not.toMatch(/x=1/);
  });

  it("FIX69: a key-based catalogue card with a placeholder server is not shown; a real one is", async () => {
    host.listConnectorCatalog.mockResolvedValueOnce([
      { id: "github", name: "GitHub", description: "d", category: "developer", authType: "api_key", tools: [], installed: false, status: "NotConfigured", placeholder: true },
      { id: "jira", name: "Jira", description: "d", category: "developer", authType: "api_key", tools: [], installed: false, status: "NotConfigured", placeholder: false },
      { id: "slack", name: "Slack", description: "d", category: "communication", authType: "oauth2", tools: [], installed: false, status: "NotConfigured", placeholder: true },
    ] as never);
    render(<ConnectorsPage />);
    expect(await screen.findByText("Jira")).toBeTruthy();
    expect(screen.getByText("Slack")).toBeTruthy(); // oauth cards keep their owner-file server
    expect(screen.queryByText("GitHub")).toBeNull();
  });

  it("an older host without listInstalled still renders the catalogue", async () => {
    host.listInstalledConnectors.mockRejectedValueOnce(new Error("unsupported"));
    render(<ConnectorsPage />);
    expect(await screen.findByText("GitHub")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-custom-demo-mcp-server")).toBeNull();
  });
});
