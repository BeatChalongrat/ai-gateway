# Desktop runtime config

`desktop.config.json` is the **default** config: `npm start` reads it from
this folder, and `npm run dist` with no `--config` ships it beside the exe.
Since FIX134 it is the **https** stack — every origin is `localhost` on the
ports the Caddy front door serves (`docs/ops/tls.md`), and it pins this
machine's local CA (`trustedCaFile` → `infra/caddy/certs/rootCA.pem`,
gitignored, one per machine). The validator refuses to start or build without
that file, so make the CA first (mkcert, see the TLS doc); a worktree or a
fresh clone has none until you copy `infra/caddy/certs/` in or point
`SRAN_DESKTOP_CONFIG` elsewhere.

`desktop.config.plain-http.json` is the same stack over plain http, for a
deployment that runs **without** Caddy (the TLS doc's rollback). It does not
work against the FIX133 stack (the plain ports are Caddy's). Use it with
`npm run dist -- --config config/desktop.config.plain-http.json`, or
`SRAN_DESKTOP_CONFIG=<absolute path>` for `npm start`.

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

## `temporaryChatReadsMemory` (FIX56.1 — retired in FIX130)

The key no longer exists. A chat whose memory switch is off, and a temporary
chat whatever its switch says, neither reads nor writes memory: no saved
memory or local note goes into its prompt, no memory-write tool is offered,
`remember` is refused at dispatch, and a sub-agent inherits the same rule.
chat-edge applies the same rule and no longer reads
`TEMPORARY_CHAT_READS_MEMORY`. A config file that still carries the key loads
normally; the value is ignored.
