# Desktop runtime config

`desktop.config.json` is the **dev** config (`npm start`): every origin is
`localhost`. It is read from this folder only when running from the repo.

## The installed app reads a file beside the exe

A packaged build reads `<install>\resources\desktop.config.json` — **not** a
copy inside the app — so where a build points is decided when the installer is
built, never by editing source. `SRAN_DESKTOP_CONFIG=<absolute path>` still
overrides everything at runtime (both modes).

## Producing a build for a colleague (shared server)

1. Copy `desktop.config.server.example.json` somewhere outside the repo, e.g.
   `C:\sran\desktop.config.json`, and replace every `sran.example.internal`
   host with the real server. Keep the rules the validator enforces:
   `pageUrl` inside `allowedOrigins`; `gatewayUrl` / `chatEdgeUrl` /
   `portalOrigin` **not** in `allowedOrigins`; the CSP lists the same hosts.
2. From `apps/desktop`:
   `npm run dist -- --config C:\sran\desktop.config.json`
   (or set `SRAN_DESKTOP_BUILD_CONFIG` to that path and run `npm run dist`).
3. The build validates the file with the app's own validator (a bad file fails
   the build), ships it beside the exe, and prints the installer's full path as
   the last line. `npm run dist` with no config = the localhost build.

`sidecarPath` is ignored by the packaged app (it always uses the bundled
sidecar) but the validator still requires the key — leave it as in the example.
`protocolScheme` is `sran` / `oauthClient` `desktop` (FIX34 Part B — the owner
decided sran:// is this app's scheme; the backend maps that pair). The installer
registers `sran://` per-user (electron-builder `protocols`) and the deep-link
host mirrors `pageUrl`'s host: `sran://<host>/new?q=…`, `/chat/{id}`, `/project/{id}`.
A dev build that must not touch the machine's `sran://` registration can still
pair `sran-dev` with `desktop-dev`.

## `temporaryChatReadsMemory` (FIX56.1)

What a temporary chat — one whose memory switch is off — does with the
memories already on file.

- **absent or `true` (default):** it reads them and writes none. "Don't record
  this" is what the user asked for; an assistant that also forgets their
  language and standing instructions makes the mode one people avoid, and a
  privacy switch nobody turns on protects nobody.
- **`false`:** it reads nothing either — the stricter seal in both directions,
  for a deployment that wants it.

Only an explicit `false` turns it off, so a config written before this key
existed keeps the default rather than silently claiming the stricter promise.

**It must match chat-edge's `TEMPORARY_CHAT_READS_MEMORY` and web-chat's
build-time `NEXT_PUBLIC_TEMPORARY_CHAT_READS_MEMORY`.** The three describe one
behaviour to three audiences — the desktop prompt, the server prompt, and the
sentence the user reads above the composer — and a deployment that sets one
without the others will tell the user something the server is not doing.

The write side does **not** move with this key. Under either value a temporary
chat is offered no memory-write tool, `remember` is refused at dispatch, and a
sub-agent inherits the same flag.
