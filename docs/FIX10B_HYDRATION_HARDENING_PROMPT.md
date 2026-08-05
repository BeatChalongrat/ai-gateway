# FIX 10b — Harden the new desktop file-hydration code (2 small fixes)

Follow-up to FIX 10 (commit a94e379, cross-client file sync — verified working). An adversarial code review found two issues in the NEW desktop hydration code. Both are small. Desktop-only (`SranDesktop/`, WPF .NET 8, Windows). Do NOT `git push`, do NOT edit to force a test pass, don't touch DLP/auth/server code.

## Fix 1 — Path traversal in the filecache path (security, defense-in-depth)
`ChatViewModel.cs` ~:886 (in the hydration path added by FIX 10):
```csharp
cachePath = Path.Combine(dir, $"{file.Id}_{SafeFileName(file.Filename)}");
```
`file.Filename` is sanitized via `SafeFileName`, but **`file.Id` — a server-controlled JSON string — is concatenated raw**. A compromised/malicious chat-edge response with an id like `..\..\..\evil` writes outside `%LocalAppData%\SranDesktop\filecache`.

Fix: validate the id before use — server file ids are UUIDs, so `Guid.TryParse(file.Id, out _)` and skip hydration (log/ignore) if it fails; or run the id through `SafeFileName` as well. Either is fine; TryParse is stricter — prefer it. Check whether the same pattern exists anywhere else `file.Id` touches a filesystem path (grep the hydration/cache/save code) and fix all instances.

## Fix 2 — Don't eagerly download full bytes for doc chips (memory/bandwidth)
`HydrateServerAttachmentsAsync` currently downloads and **retains the entire `byte[]` of every attachment when a conversation opens**, including documents whose only UI is the "Save file…" chip — bytes that aren't needed until the user clicks Save. Up to ~100 MB (the server per-conversation cap) resident per open conversation, plus a download per attachment on first open.

Fix: hydrate **images/videos** as today (they render inline — bytes needed). For **documents** (the `IsDocument` chip case): don't download on open — create the attachment VM with metadata only (filename, size, file id), and fetch bytes lazily when `SaveAs` runs (reuse the existing local-first → filecache → `DownloadFileAsync` resolution there, and still write the cache + `server_url` back-fill on that first fetch). SaveAs must handle the download failing (offline) gracefully — a toast/message, not a crash. Keep the "conversation switched while hydrating" re-check semantics intact.

## Verify
1. `dotnet build SranDesktop/SranDesktop.sln -c Release` 0/0 + `dotnet test` green. Add/adjust tests: (a) a non-GUID `file.Id` from the server is rejected (no file written outside filecache — assert the guard, not the traversal), (b) doc attachments hydrate metadata-only (no download call on open), (c) SaveAs triggers the download and writes the cache.
2. **Live proof (rebuild + restart the desktop binary first — stale builds have fooled us twice):** open the FIX-10 test conversation with the web-uploaded image + PDF → image still renders inline immediately; the PDF chip appears WITHOUT a download having fired (verify via logs/proxy or a breakpoint); click Save → file downloads and saves correctly. Offline SaveAs → graceful error. If you can't run live, mark UNVERIFIED.
3. Confirm no regression to image/video inline render and generated-media hydration.

## Report
R0-lite: confirm both issues at current line numbers before editing. Then: exact guard added (+ any other `file.Id`-in-path sites found), the lazy-doc change, test results, live-proof table. One local commit, nothing pushed.
