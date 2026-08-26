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
`protocolScheme` stays `sran-dev` / `oauthClient` `desktop-dev` until the
production scheme is decided (FIX19 §1.3); the backend maps that pair today.
