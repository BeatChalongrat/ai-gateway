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
  addCustomConnector: vi.fn(),
  oauthConnect: vi.fn(),
  oauthDisconnect: vi.fn(),
}));

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

  it("an older host without listInstalled still renders the catalogue", async () => {
    host.listInstalledConnectors.mockRejectedValueOnce(new Error("unsupported"));
    render(<ConnectorsPage />);
    expect(await screen.findByText("GitHub")).toBeTruthy();
    expect(screen.queryByTestId("connector-card-custom-demo-mcp-server")).toBeNull();
  });
});
