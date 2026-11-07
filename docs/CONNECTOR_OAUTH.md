# Connector OAuth (desktop) — owner's runbook

Status after FIX31: **built, tested, switched off.** No connector is enabled, no
registration exists in the repo, and nothing here can be turned on by a default,
an environment variable or a catalogue row. The only switch is a file you write
on the machine that runs the desktop app.

## What exists

| Piece | Where | What it does |
|---|---|---|
| The flow | `apps/desktop/src/main/connector-oauth.ts` | RFC 8252 native-app OAuth: **system browser** (never an in-app window), **PKCE S256**, **loopback redirect** on `127.0.0.1:<ephemeral>/callback`, **`state`** checked; token exchange + refresh; **disconnect revokes** (RFC 7009 when a revocation endpoint is configured) **and deletes**. |
| The token store | same file (`ConnectorTokenStore`) | One encrypted file per connector under `%APPDATA%\SranDesktop-Electron\connector-tokens\` (Electron `safeStorage` = DPAPI on Windows). If encryption is unavailable the flow refuses to connect rather than store plain text. |
| The MCP client | `apps/desktop/src/main/connector-mcp.ts` | Streamable-HTTP MCP client in the **host**; puts `Authorization: Bearer <token>` on the wire itself. The sidecar never sees the tool, the arguments or the token. |
| The page | Connectors screen | For an oauth2 connector the button reads **Connect / Disconnect** and is enabled only when the connector is **configured and enabled** in your file; otherwise the reason is printed on the card. The page learns *that* a connector is connected — never the token. |
| Host ops | `connectors.oauthConnect`, `connectors.oauthDisconnect`, `connectors.oauthStatus` | Page → host; the page never talks to the provider. |

## The one file: `connector-oauth.json`

Path: `%APPDATA%\SranDesktop-Electron\connector-oauth.json` (the app's `userData`
folder — the same folder as `permissions.json`). It is re-read whenever it
changes; no restart needed.

```json
{
  "connectors": {
    "slack": {
      "enabled": true,
      "clientId": "<from the Slack app page>",
      "clientSecret": "<from the Slack app page — optional for PKCE public clients>",
      "authorizationEndpoint": "https://slack.com/oauth/v2/authorize",
      "tokenEndpoint": "https://slack.com/api/oauth.v2.access",
      "revocationEndpoint": "https://slack.com/api/auth.revoke",
      "serverUrl": "https://<your MCP server for Slack>/mcp",
      "scopes": ["chat:write", "channels:read"],
      "extraAuthParams": { "user_scope": "chat:write" }
    }
  }
}
```

Rules the loader enforces (a bad entry is dropped and named in `shell.log`,
never half-loaded): the id is `[a-z][a-z0-9_-]*`; `clientId` non-empty; every
endpoint and `serverUrl` **https**; `enabled` is a boolean and **defaults to
false**. `clientSecret` is read from this file, kept in memory, sent only to the
token/revocation endpoints, and never logged or shown to the page.

Where this file lives is the owner's machine profile. It is **not** in the repo
and must not be: `git check-ignore` the path if you ever copy it near the tree.

## "I have a Slack client id and secret — what do I do?"

1. In the Slack app's settings add the redirect URL. Slack requires **https**
   redirect URLs and does not accept `http://127.0.0.1:<port>`. So for Slack
   specifically you need one of: a Slack app configured for a fixed https
   redirect that forwards to the loopback (a tiny relay page), or a provider
   that accepts loopback (Google does: `http://127.0.0.1:<port>` is allowed for
   "Desktop app" clients). This is a provider policy, not a code gap — the flow
   itself is complete.
2. Write the file above with `"enabled": true` for that connector, using your
   MCP server's URL as `serverUrl`.
3. Open the desktop app → Connectors → the card's button now reads **Connect**.
   Click it: the system browser opens the provider's consent page. Approve.
   The browser tab shows "Connected to SRAN Desktop — you can close this tab";
   the card reads **Connected in this app**.
4. What happens next, with no further change: on the next turn the model is
   offered the connector's tools as `mcp__slack__<tool>`; each call **asks you**
   (category Network, default Ask — it asks in "Asks each action" and in "Runs
   safe actions"; only "Never asks" runs it unprompted); on allow the host calls
   your MCP server with the Bearer. `shell.log` shows
   `TOOL mcp__slack__send_message … → host (connector)`.
5. Disconnect revokes at the provider (both tokens, when a revocation endpoint
   is configured) and deletes the local file even if revocation fails.

Nothing in steps 1–5 is a code change.

## The Slack mark on the connector card (owner's three steps)

The card shows a neutral mark for Slack on purpose: Slack asked simple-icons to
remove its icon, and Slack's brand kit is downloaded under Slack's own terms —
accepting those is your decision, so the repo carries no copy and the code
downloads nothing. The wiring (FIX42 D2, verified with a test file in FIX43 C3)
prefers a file **you** place, served from our own origin:

1. Download the Slack mark (SVG) from Slack's brand centre and accept Slack's terms yourself.
2. Save it as `apps/web-chat/public/icons/connectors/slack.svg` (any connector id works the same way: `<id>.svg`).
3. Rebuild the web-chat image (`public/` is baked in at build time) — the card picks it up; no code change.

Remove the file and the card goes back to the neutral mark — never a broken image.

## Failure modes the flow handles

| What happens | The user sees | Log |
|---|---|---|
| You decline at the provider | "Not connected — you declined, or the provider refused." | `oauth-denied` |
| You close the tab / walk away (10 min) | "Not connected — the sign-in wasn't completed in time." | `oauth-timeout`; the loopback listener is closed |
| Something hits the loopback with a foreign `state` | ignored (HTTP 400 to that request); the real sign-in still works | `callback with a foreign state — ignored` |
| Click Connect twice | the first attempt is cancelled, the second proceeds | `oauth-cancelled` |
| Token near expiry | refreshed silently via the refresh grant | `token refreshed` |
| Refresh fails | the tool call answers "not connected"; the card still shows Connected until you Disconnect/Connect again | `refresh failed — reconnect needed` |
| Provider revoke fails | local token still deleted | `revoked=false` |

## What is deliberately NOT decided here (owner's call)

**DLP policy for connector traffic.** Today the gateway's tool-argument policy
classifies `mcp__*` arguments as external egress: a message that carries masked
data (`<<PHONE_1>>`) is **blocked** by the host (`dlpBlocked`), not sent. The
options — none chosen in FIX31, no policy code changed:

1. **Block** (current): a Slack message containing a phone number never leaves.
   Safe, and the model has to explain why it could not send.
2. **Send masked**: the colleague receives `<<PHONE_1>>`. Nothing leaks, but the
   message is visibly broken and the recipient cannot act on it.
3. **Send unmasked** (treat connector tools like `LOCAL_EXECUTION`, restoring the
   original values on emit): the message is useful, and we have opened an
   egress channel by hand — every connected connector becomes a way for
   tenant data to leave, gated only by the per-call consent card.
4. **Per-connector / per-tool policy** chosen by the tenant admin (e.g. Slack
   inside the company: unmasked; Gmail: masked; unknown MCP server: blocked).
   The most defensible, and the most work (a policy table + admin UI).

Until one is chosen, option 1 is what ships, because it is what the existing
rule does.
