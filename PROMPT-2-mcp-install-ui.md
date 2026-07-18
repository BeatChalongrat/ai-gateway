# Task: Custom MCP server install UI for SranDesktop

Work in `SranDesktop/` (WPF .NET 8, clean architecture). Build `dotnet build SranDesktop/SranDesktop.sln`, test `dotnet test`.

## Context

The MCP connector backend is COMPLETE — only the UI is missing. Today users can only install predefined catalog connectors via `ConnectorMarketplaceView`. The goal: let users add ANY MCP server (HTTP or stdio) from the UI, like Claude Desktop's custom connectors.

Existing pieces (read these first):
- `src/SranDesktop.Infrastructure/Mcp/Remote/` — MCP protocol client: `StdioMcpTransport` (spawns a local process), HTTP streamable transport, `RemoteMcpTool` adapter (Category Network, DefaultPermission Ask).
- `McpConnectorService` — `InstallCustomAsync` already exists at the service layer, install state in `connectors.json`, tool caching, 5-min health checks with status dots.
- Auth flows: oauth2 broker (system browser + deep link), api_key (DPAPI via `DpapiCredentialStore`), none.
- UI: `ConnectorMarketplaceView` / its ViewModel (catalog install/uninstall only).

## What to build

### 1. "Add custom connector" dialog

New dialog (follow the existing dialog patterns in `Presentation/Views/Dialogs/` + CommunityToolkit MVVM):

Fields:
- **Name** (required, unique among installed connectors)
- **Transport**: radio/segmented — `HTTP` | `Local command (stdio)`
- HTTP: **Server URL** (https required for non-localhost; allow http for localhost/127.0.0.1)
- stdio: **Command** (e.g. `npx`), **Arguments** (single text line, e.g. `-y @modelcontextprotocol/server-filesystem C:\data`), optional **Environment variables** (multiline `KEY=value`)
- **Auth**: `None` | `API key` → when API key: **Header name** (default `Authorization`) + **Key value** (PasswordBox, stored via DPAPI like the existing api_key flow — never in connectors.json plaintext)

Buttons: **Test connection** (calls the service to connect + list tools; show tool count or the error inline) and **Add** (enabled only after a successful test, or at minimum after validation passes — pick whichever the existing service API supports cleanly).

Entry point: an "Add custom…" button/card in `ConnectorMarketplaceView` alongside the catalog.

### 2. Installed-list treatment

Custom connectors appear in the installed list like catalog ones: status dot from the existing health check, tool count, uninstall. Mark them visually as "Custom" (small badge/tag).

### 3. Safety

- stdio spawns a local process — before install completes, show a themed confirm dialog (use the existing `IDialogService`) warning in the user's language that this runs a local program with their permissions, showing the exact command line.
- Remote tools already default to Ask permission per tool — do not change that.
- Persist nothing secret outside DPAPI.

### 4. Localization

All new strings in `src/SranDesktop.Presentation/Localization/Dict.cs`, BOTH `En` and `Th` dictionaries (e.g. `connector.addCustom` = "Add custom connector" / "เพิ่ม connector เอง", `connector.testConnection` = "Test connection" / "ทดสอบการเชื่อมต่อ", `connector.stdioWarning` = warning text both languages, etc.). Follow `loc:T` usage.

### 5. Tests

Service-level: if `InstallCustomAsync` validation gaps exist (duplicate name, bad URL scheme, empty command), add validation + xunit tests. ViewModel: test the form validation logic (can-add gating). Follow xunit + Moq + FluentAssertions style.

## Conventions

- No new NuGet packages.
- Register anything new in `AppHostBuilder.cs` as singleton, interfaces in `Application/Interfaces`.
- Concise `///` XML comments explaining WHY, matching the codebase's phase-tagged style.
- Do not touch `ChatOrchestrator`, `PathGuard`, permission flow.

## Acceptance

- `dotnet build` clean, `dotnet test` green.
- Can add an HTTP MCP server by URL, see its tools appear to the agent, uninstall it.
- Can add a stdio server (e.g. `npx -y @modelcontextprotocol/server-everything`), get the warning dialog, connect, and the agent can call its tools (each gated by Ask consent as today).
- API key never appears in `connectors.json`.
