# Connectors on the desktop — owner's setup sheet

Two kinds of card on the Connectors screen, and one button above them:

| Card | Button | Engine | Works today? |
|---|---|---|---|
| Slack · Gmail · Google Drive · Google Calendar | **Connect** | host OAuth (`connector-oauth.ts`) + host MCP client (`connector-mcp.ts`), tools `mcp__<id>__<tool>` | after **you** register an app and name an MCP server (§2, §3) |
| Notion · GitHub · Jira · PostgreSQL | **Install** | sidecar (`McpConnectorService`), needs an API key | **no** — see §4 |
| **+ Add custom connector** | — | sidecar spawns a local MCP server over stdio | **yes, now** — proven end to end in FIX68 (§5) |

The engine is the same MCP protocol in all three; what differs is who holds the credential and where the server runs. The OAuth runbook with every failure mode is `docs/CONNECTOR_OAUTH.md`; this sheet is the short "what do I type" version.

## 1. Where the files live

`<userData>` = `%APPDATA%\SranDesktop-Electron` = `C:\Users\<you>\AppData\Roaming\SranDesktop-Electron`

| File | Written by | Holds |
|---|---|---|
| `connector-oauth.json` | **you** | OAuth registrations (§2). Absent by default = nothing configured. Re-read on change, no restart. |
| `connector-tokens\` | the app | one encrypted token file per connected OAuth connector (DPAPI) |
| `connectors.json` | the app | the sidecar's installed list, including every custom server you added |
| `credentials.dat` | the app | API keys (DPAPI) — none today |

Nothing under `<userData>` is in the repo and nothing there must ever be copied into it.

## 2. `connector-oauth.json` — template (fill the blanks, keep the file out of git)

```json
{
  "connectors": {
    "gmail": {
      "enabled": true,
      "clientId": "<client id from the provider>",
      "clientSecret": "<client secret, or omit for a PKCE public client>",
      "authorizationEndpoint": "https://<provider authorize url>",
      "tokenEndpoint": "https://<provider token url>",
      "revocationEndpoint": "https://<provider revoke url, or omit>",
      "serverUrl": "https://<the MCP server that accepts this provider's token>/mcp",
      "scopes": ["<scope 1>", "<scope 2>"],
      "extraAuthParams": { "<extra authorize query param>": "<value>" }
    }
  }
}
```

- The key (`gmail`) must be a catalogue id: `slack`, `gmail`, `gdrive`, `gcal`.
- Every URL must be **https**; `enabled` defaults to **false**; a bad entry is dropped and named in `shell.log`.
- `serverUrl` is the one thing the registration alone does not give you: the OAuth flow only obtains a token, the **tools** come from an MCP server at that URL that accepts the token as `Authorization: Bearer`. The catalogue's built-in `https://mcp.sran.ai/...` values are placeholders (the name does not resolve). If you have no such server for a provider, use §5 with a local server instead.
- The **redirect URI** the app uses is `http://127.0.0.1:<random port>/callback` (RFC 8252 loopback; the port is chosen per sign-in).

## 3. Registering with a provider — the clicks

**Google (Gmail, Drive, Calendar)** — console.cloud.google.com → *APIs & Services* → *OAuth consent screen* (add yourself as a test user while in Testing) → *Credentials* → *Create credentials* → *OAuth client ID* → Application type **Desktop app** → copy the client id and secret. Desktop-app clients accept the loopback redirect on **any** port, so there is no redirect URL to type. Endpoints: authorize `https://accounts.google.com/o/oauth2/v2/auth`, token `https://oauth2.googleapis.com/token`, revoke `https://oauth2.googleapis.com/revoke`; add `"extraAuthParams": { "access_type": "offline", "prompt": "consent" }` so a refresh token is issued. Enable the Gmail / Drive / Calendar API for the project.

**Slack** — api.slack.com/apps → *Create New App* → *OAuth & Permissions* → add scopes → copy the client id and secret from *Basic Information*. Slack **only accepts https redirect URLs**, so the loopback redirect is refused; you need a small https relay that forwards to `127.0.0.1:<port>/callback`, or a provider that accepts loopback. This is Slack's policy, not a code gap. Endpoints: `https://slack.com/oauth/v2/authorize`, `https://slack.com/api/oauth.v2.access`, revoke `https://slack.com/api/auth.revoke`.

Then: write the file with `"enabled": true` → open Connectors → **Connect** → approve in the system browser → the card reads *Connected in this app*. Each tool call asks you first (category Network) under "Asks each action" and "Runs safe actions".

## 4. The "Install" cards (Notion, GitHub, Jira, PostgreSQL)

Clicking **Install** does nothing useful today and says so: *"Key-based connectors need a secure key input, coming in a later round."* Two things are missing, neither of them yours to register: the app has no secure key prompt for the sidecar path, and the catalogue's server URLs are placeholders. **Until then, reach those services through §5** — GitHub and Notion both publish an MCP server that runs locally over stdio (`github-mcp-server`, `@notionhq/notion-mcp-server`); they read their token from an environment variable, so start them through a small `.js` wrapper that sets the variable and spawns the server (the token then lives in that file on your disk — your call), and point *Add custom connector* at the wrapper.

## 5. Your own MCP server — the same capability the hosted assistants give their users

1. Have a program that speaks MCP over **stdio** (one JSON-RPC message per line). The smallest working example is `scripts/probes/fix68/demo-mcp-server.mjs` (no dependencies; two tools). A `.js`/`.mjs` file is started as `node <file>`, a `.py` as `python <file>`, anything else is run directly. No arguments, no environment — use a wrapper if the server needs them.
2. Connectors → **+ Add custom connector** → pick the file → read the dialog (it shows the exact command that will be spawned) → **Add connector**. The sidecar spawns it, lists its tools, and a card appears with the tool names the server announced.
3. The model gets the tools on its next turn (no restart). Every call raises the consent card (Deny stops it before the server hears anything); the connector's status dot goes red if the server dies.
4. **Uninstall** on the card ends the process and removes its tools.

A remote (https) MCP server without OAuth is not offered by the picker; the sidecar supports one (`"transport": "http"`, `"server_url"` in `connectors.json`), the UI does not yet.

Limits to know: masked content (`<<PHONE_1>>` etc.) in a custom tool's arguments is **blocked** by the gateway's unknown-tool rule; the test server binds no port and is dev-only — it ships in no installer or image.
