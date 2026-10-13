# Connectors on the desktop — owner's setup sheet

Two kinds of card on the Connectors screen, and one button above them:

| Card | Button | Engine | Works today? |
|---|---|---|---|
| Slack · Gmail · Google Drive · Google Calendar | **Connect** | host OAuth (`connector-oauth.ts`) + host MCP client (`connector-mcp.ts`), tools `mcp__<id>__<tool>` | after **you** register an app and name an MCP server (§2, §3) |
| Notion · GitHub · Jira · PostgreSQL | **Install** | sidecar (`McpConnectorService`), needs an API key | **hidden** while the catalogue is the placeholder one — see §4 |
| **+ Add custom connector** → *Program on this computer* | — | sidecar spawns a local MCP server over stdio | **yes** — proven end to end in FIX68 (§5) |
| **+ Add custom connector** → *Remote server (URL)* | — | sidecar dials an https MCP server, key from the DPAPI store | **yes** — proven end to end in FIX69 (§5, §6) |

The engine is the same MCP protocol in all three; what differs is who holds the credential and where the server runs. The OAuth runbook with every failure mode is `docs/CONNECTOR_OAUTH.md`; this sheet is the short "what do I type" version.

## 1. Where the files live

`<userData>` = `%APPDATA%\SranDesktop-Electron` = `C:\Users\<you>\AppData\Roaming\SranDesktop-Electron`

| File | Written by | Holds |
|---|---|---|
| `connector-oauth.json` | **you** | OAuth registrations (§2). Absent by default = nothing configured. Re-read on change, no restart. |
| `connector-tokens\` | the app | one encrypted token file per connected OAuth connector (DPAPI) |
| `connectors.json` | the app | the sidecar's installed list, including every custom server you added |
| `credentials.dat` | the app | keys of remote connectors, one entry per connector (DPAPI, this Windows user) — deleted on Uninstall |

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

They are **not shown** while the app runs on the embedded starter catalogue, because that catalogue's server URLs are placeholders (no such hosts) — a card with no destination is not a card. They appear when the sidecar is pointed at a real catalogue (`SRAN_MCP_CATALOG_URL`) that names real servers; the key prompt for them is the same host-owned form as §6. Until then, reach those services through §5 or §6: GitHub and Notion publish MCP servers — a local stdio one (`github-mcp-server`, `@notionhq/notion-mcp-server`, token via an environment variable, so start it through a small `.js` wrapper) or a hosted https one that takes your token as a Bearer, which is exactly what the remote path sends.

## 5. Your own MCP server — the same capability the hosted assistants give their users

**A program on this computer (stdio).** Have a program that speaks MCP over stdio (one JSON-RPC message per line); the smallest working example is `apps/desktop/scripts/demo-mcp-server.mjs`. Connectors → **+ Add custom connector** → *Program on this computer* → pick the file → the dialog shows the exact command that will be spawned → **Add connector**. A `.js`/`.mjs` file runs as `node <file>`, a `.py` as `python <file>`, anything else directly; no arguments, no environment — use a wrapper if the server needs them.

**A remote server (https).** Connectors → **+ Add custom connector** → *Remote server (URL)* → the app opens **its own window** (not the chat page) for the address and, optionally, a key → **Next** → the dialog names the exact origin it will connect to and whether a key is sent → **Add connector**. The sidecar dials the server (`POST <url>/mcp`), lists its tools, and only then stores the key.

Either way: the card appears with the tool names the server announced; the model gets the tools on its next turn (no restart); every call raises the consent card (Deny stops it before the server hears anything); **Uninstall** ends the process or session, removes the tools **and deletes the key**.

## 6. Keys and where a remote URL may point

- **https only.** `http://` is refused with the reason, loopback included — a key must never travel in the clear.
- **Public servers by default.** An address that points inside the network (loopback, 10/8, 172.16/12, 192.168/16, link-local, ULA) is refused with the reason. A build for a company network may open that with `"connectorPrivateNetwork": true` in the shipped `desktop.config.json` (a deploy-time decision, not a page setting); **cloud metadata endpoints are refused always.** The rule is applied twice: by the app before anything is dialled, and by the sidecar when it opens the socket (the name is resolved again there).
- **A private CA.** A server inside the company network usually carries a certificate from your own CA. Put that CA's PEM on the machine and name it in the shipped `desktop.config.json` as `"connectorExtraCaFile": "<absolute path>"`; validation is not relaxed (name, dates and chain are still checked), one more root is trusted. Without it an untrusted certificate is reported as *"The server's certificate isn't trusted on this computer"* and nothing is saved.
- **The key** is typed in the app's own window, sent to the sidecar over its private pipe, stored **only** in `credentials.dat` (DPAPI, this Windows user), and never written to a log, shown on a card (the card says *Key set*), returned to the page, or placed near a model. A wrong key is reported as **"The server rejected the key"** — nothing is saved in that case.
- The URL you typed is stored as you typed it in `connectors.json`; put the key in the key field, never in the URL.

Limits to know: masked content (`<<PHONE_1>>` etc.) in a custom tool's arguments is **blocked** by the gateway's unknown-tool rule for both transports; the test server is dev-only and binds loopback only — it ships in no installer or image.

## 7. The browser extension (Chrome tabs as tools)

1. **Load it** — chrome://extensions → *Developer mode* → *Load unpacked* → the folder shown in SRAN Desktop → Settings → Browser (`%APPDATA%\SranDesktop-Electronrowser-extension`; the app refreshes that copy at every start — never load the copy in the repo).
2. **The id** — Chrome derives an unpacked extension's id from the folder's path; the app derives the same id from the same folder and writes it into the native-messaging manifest (`browser\com.sran.browser.electron.json`, `allowed_origins`) at every start. *Expected id* on the Settings page must equal the id under the extension on chrome://extensions. It will, unless Chrome was given the path in a different spelling; then use *I loaded a different folder…* (or type the id — 32 letters a–p; anything else is refused).
3. **The token** — there is nothing to paste: the app writes `pairing.json` into that folder and the extension reads it itself. Paste only if you loaded a folder the app does not write to; an empty field on the options page means "use the app's".
4. **Changed the id or the folder?** Nothing else to do: the app rewrites the manifest itself (immediately for a typed id, at the next start otherwise); no registry editing, ever. The extension retries on its own within a minute.
5. **Token possibly leaked?** Settings → Browser → *Generate a new pairing token*. The old one stops working at once; the extension picks the new one up from `pairing.json` and reconnects.

If the options page says *Chrome refused … not the id SRAN Desktop authorised*, step 2 is the fix; if it says *native host not registered*, start the app once.

