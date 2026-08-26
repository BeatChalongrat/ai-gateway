using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using Microsoft.Extensions.Logging;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Shell.Hosting;

namespace SranDesktop.Shell;

/// <summary>
/// The one window. Owns the shell's WebView2 and every policy on it
/// (DESKTOP_SHELL_PLAN.md §4):
///
///   C-1  never calls AddHostObjectToScript; never registers a frame
///        WebMessageReceived handler — and in Phase 1, no WebMessageReceived
///        handler of any kind. Artifact iframes then have no path to the host.
///        (Pinned by ShellPinningTests as a source-text assertion; see its
///        comment for what such an assertion cannot see.)
///   C-5  NavigationStarting + FrameNavigationStarting allow-list;
///        NewWindowRequested → system browser; downloads denied; permissions
///        denied + logged.
///   C-6  DevTools on in Debug, off in Release.
///   ProcessFailed → log + CrashReportService + a Reload button.
///
/// Diagnostics (Phase 1 §6 hand checks): CDP Runtime.consoleAPICalled and
/// Log.entryAdded are subscribed so page JS errors reach shell-.log without
/// DevTools; a document-created script logs paste-event facts (types only —
/// never clipboard contents).
/// </summary>
public partial class ShellWindow : Window, ISessionSurface
{
    private readonly ShellOptions _options;
    private readonly NavigationPolicy _policy;
    private readonly ICrashReportService _crashReports;
    private readonly McpToolRegistry _tools;
    private readonly ShellSessionController _sessionController;
    private readonly ISettingsService _settings;
    private readonly ILogger<ShellWindow> _logger;
    private readonly string[] _args;
    private CoreWebView2Environment? _environment;
    private bool _firstNavigationLogged;
    private bool _signedIn;

    private static readonly Regex SensitiveDiagnosticValue = new(
        """(?i)(access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|authorization|cookie|ticket|code)([\s"']*[:=][\s"']*)([^\s"'&,;}]+)""",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex BearerValue = new(
        """(?i)\bBearer\s+[A-Za-z0-9._~-]+""",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex JwtValue = new(
        """\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,})?\b""",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex PrefixedKeyValue = new(
        """\b(?:sk|gw)_[A-Za-z0-9_-]{4,}\b""",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private readonly SranDesktop.Shell.Bridge.BridgeChannel _bridge;
    private readonly SranDesktop.Shell.Bridge.ShellTurnCoordinator _turns;
    private readonly SranDesktop.Shell.Bridge.ShellHostQueryCoordinator _hostQuery;
    private readonly SranDesktop.Shell.Hosting.ShellConversationMigrator _migrator;
    private bool _bridgeAttached;

    public ShellWindow(
        ShellOptions options,
        NavigationPolicy policy,
        ICrashReportService crashReports,
        McpToolRegistry tools,
        ShellSessionController sessionController,
        SranDesktop.Shell.Bridge.BridgeChannel bridge,
        SranDesktop.Shell.Bridge.ShellTurnCoordinator turns,
        SranDesktop.Shell.Bridge.ShellHostQueryCoordinator hostQuery,
        SranDesktop.Shell.Hosting.ShellConversationMigrator migrator,
        ISettingsService settings,
        ILogger<ShellWindow> logger)
    {
        _options = options;
        _policy = policy;
        _crashReports = crashReports;
        _tools = tools;
        _sessionController = sessionController;
        _bridge = bridge;
        _turns = turns;
        _hostQuery = hostQuery;
        _migrator = migrator;
        _settings = settings;
        _logger = logger;
        _args = Environment.GetCommandLineArgs();
        InitializeComponent();
        _sessionController.StateChanged += OnSessionStateChanged;
        Loaded += async (_, _) => await InitializeWebViewAsync();
    }

    // ------------------------------------------------------------------
    // Environment + policy wiring
    // ------------------------------------------------------------------

    private async Task InitializeWebViewAsync()
    {
        try
        {
            // Own environment, own user-data folder. Never WebView2Environments.GetSharedAsync().
            var userDataFolder = _options.ResolvedUserDataFolder;
            Directory.CreateDirectory(userDataFolder);
            _environment = await CoreWebView2Environment.CreateAsync(userDataFolder: userDataFolder);
            await Browser.EnsureCoreWebView2Async(_environment);
            var core = Browser.CoreWebView2;

            _logger.LogInformation("WebView2 ready: runtime {Runtime}, SDK {Sdk}, user-data {Folder}",
                _environment.BrowserVersionString,
                typeof(CoreWebView2).Assembly.GetName().Version,
                userDataFolder);

            ApplySettings(core.Settings);
            _logger.LogInformation("WebView2 settings: DevTools={DevTools}, web messages={WebMessages}",
                core.Settings.AreDevToolsEnabled, core.Settings.IsWebMessageEnabled);
            WirePolicy(core);
            await WireDiagnosticsAsync(core);

            // Phase 3: the one bridge. Attach exactly once — the top-level
            // WebMessageReceived registration lives only in BridgeChannel.
            if (!_bridgeAttached)
            {
                _bridge.Attach(core, Dispatcher);
                _turns.Attach(_bridge);
                _hostQuery.Attach(_bridge); // Phase 5 control vocabulary
                _bridgeAttached = true;
#if DEBUG
                // 4.5: when --probe-consent <deny|allow> is set, answer each
                // consent.request with a REAL consent.respond posted from the
                // page over the bridge (valid nonce) — the exact round-trip a
                // Phase 4 consent card will make. The host triggers the page to
                // send it; the message still flows through BridgeChannel's full
                // inbound path (origin + nonce + enum + parse).
                var consentDecision = ArgAfter("--probe-consent");
                if (consentDecision is "deny" or "allow")
                {
                    _turns.ConsentRequestPosted += promptId =>
                    {
                        _logger.LogWarning("PROBE consent: answering prompt {Prompt} with '{Decision}' via a real bridge consent.respond", promptId, consentDecision);
                        _ = Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                            "(function(){var b=window.__SRAN_BRIDGE__;window.chrome.webview.postMessage(" +
                            $"{{nonce:b.nonce,type:'consent.respond',payload:{{promptId:'{promptId}',decision:'{consentDecision}',scope:'once'}}}});}})();"));
                    };
                }
#endif
            }

            _logger.LogInformation("Allow-list: {Origins}; portal origin (re-auth signal, NOT allowed): {Portal}",
                string.Join(", ", _policy.Origins), _policy.PortalOrigin ?? "(none)");

            // ── Phase 2 §2.1 · boot order: authenticate FIRST, navigate second ──
            _sessionController.AttachSurface(this);
            ShowSessionOverlay("Signing in…", showButton: false);
            var haveSession = await _sessionController.EnsureSessionAtBootAsync();
            if (!haveSession)
            {
                ShowSessionOverlay("You are signed out. Sign in opens your browser.", showButton: true);
                _logger.LogInformation("BOOT: no session established — showing the shell-owned signed-out view (no navigation)");
                return;
            }

            await _sessionController.ApplySessionToBrowserAsync();
            _sessionController.StartRotation();
            _signedIn = true;
            _logger.LogInformation("BOOT ORDER: session established first; navigating second → {Url}", _options.PageUrl);
            core.Navigate(_options.PageUrl);
        }
        catch (Exception ex)
        {
            _logger.LogCritical(ex, "WebView2 initialisation failed");
            ShowCrashOverlay($"WebView2 could not start: {ex.Message}");
        }
    }

    private static void ApplySettings(CoreWebView2Settings settings)
    {
        // C-6: DevTools only in Debug builds. The spike confirmed nothing it
        // observed depended on DevTools being open.
#if DEBUG
        settings.AreDevToolsEnabled = true;
#else
        settings.AreDevToolsEnabled = false;
#endif
        settings.IsStatusBarEnabled = false;
        // Phase 3: the bridge needs inbound web messages. This enables
        // window.chrome.webview.postMessage — but delivery for a CHILD frame
        // still goes to CoreWebView2Frame.WebMessageReceived, which the shell
        // never registers (C-1), so an artifact iframe's postMessage lands
        // nowhere. Only the TOP document reaches BridgeChannel. Verified by
        // exit criterion 4.1.
        settings.IsWebMessageEnabled = true;
        settings.AreDefaultContextMenusEnabled = true;
        settings.IsPasswordAutosaveEnabled = false;
        settings.IsGeneralAutofillEnabled = false;
        settings.AreBrowserAcceleratorKeysEnabled = true;
    }

    private void WirePolicy(CoreWebView2 core)
    {
        // C-5 · top-level navigation. Phase 2 §2.2: the portal origin stays
        // OFF the allow-list; an attempted navigation there is cancelled like
        // any other refusal, but recognised as the page's "I have no session"
        // signal and answered with a host-side re-auth instead of a dead
        // spinner. Every occurrence is logged with the triggering URL.
        core.NavigationStarting += (_, e) =>
        {
            if (_policy.IsTopLevelAllowed(e.Uri))
                return;
            e.Cancel = true;
            if (_policy.IsPortalNavigation(e.Uri))
            {
                _logger.LogWarning("NAV REFUSED (top-level, portal): {Uri} — treating as re-auth signal; userInitiated={UserInitiated} redirect={Redirect}",
                    SafeDiagnosticUrl(e.Uri), e.IsUserInitiated, e.IsRedirected);
                _ = _sessionController.NotifyPortalRedirectAsync(SafeDiagnosticUrl(e.Uri));
                return;
            }
            _logger.LogWarning("NAV REFUSED (top-level): {Uri} — not in allow-list [{Origins}]; userInitiated={UserInitiated} redirect={Redirect}",
                SafeDiagnosticUrl(e.Uri), string.Join(", ", _policy.Origins), e.IsUserInitiated, e.IsRedirected);
        };

        // C-5 · frame navigation (artifact/canvas iframes are about:srcdoc).
        // A frame heading for the portal gets the same recognition — some
        // login bounces arrive via an embedded frame rather than the top
        // document, and both mean the same thing: no session.
        core.FrameNavigationStarting += (_, e) =>
        {
            if (_policy.IsFrameAllowed(e.Uri))
                return;
            e.Cancel = true;
            if (_policy.IsPortalNavigation(e.Uri))
            {
                _logger.LogWarning("NAV REFUSED (frame, portal): {Uri} — treating as re-auth signal", SafeDiagnosticUrl(e.Uri));
                _ = _sessionController.NotifyPortalRedirectAsync(SafeDiagnosticUrl(e.Uri));
                return;
            }
            _logger.LogWarning("NAV REFUSED (frame): {Uri}", SafeDiagnosticUrl(e.Uri));
        };

        // C-5 · target=_blank / window.open → the system browser, never a second WebView
        core.NewWindowRequested += (_, e) =>
        {
            e.Handled = true;
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
            {
                _logger.LogInformation("NewWindowRequested → system browser: {Uri}", SafeDiagnosticUrl(e.Uri));
                try
                {
                    Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Could not open system browser");
                }
            }
            else
            {
                _logger.LogWarning("NewWindowRequested DROPPED (non-http scheme): {Uri}", SafeDiagnosticUrl(e.Uri));
            }
        };

        // C-5 · downloads: explicit policy — none this phase.
        core.DownloadStarting += (_, e) =>
        {
            e.Cancel = true;
            e.Handled = true;
            _logger.LogWarning("DOWNLOAD REFUSED (Phase 1 policy): {Uri}", SafeDiagnosticUrl(e.DownloadOperation.Uri));
        };

        // C-5 · permissions: deny + log by default. Logged at Information so the
        // paste hand check (§6) can tell "denied by shell" from "never asked".
        core.PermissionRequested += (_, e) =>
        {
            e.State = CoreWebView2PermissionState.Deny;
            _logger.LogInformation("PermissionRequested kind={Kind} uri={Uri} userInitiated={UserInitiated} → {State}",
                e.PermissionKind, SafeDiagnosticUrl(e.Uri), e.IsUserInitiated, e.State);
        };

        // ProcessFailed: never a silent white window.
        core.ProcessFailed += (_, e) =>
        {
            _logger.LogError("WebView2 ProcessFailed kind={Kind} reason={Reason} exit={ExitCode} process={Process}",
                e.ProcessFailedKind, e.Reason, e.ExitCode, e.ProcessDescription);
            ReportCrashBestEffort(
                new InvalidOperationException($"WebView2 {e.ProcessFailedKind}: {e.Reason} (exit {e.ExitCode})"));
            if (e.ProcessFailedKind is CoreWebView2ProcessFailedKind.BrowserProcessExited
                or CoreWebView2ProcessFailedKind.RenderProcessExited
                or CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
            {
                Dispatcher.InvokeAsync(() => ShowCrashOverlay($"{e.ProcessFailedKind} ({e.Reason})"));
            }
        };

        core.NavigationCompleted += (_, e) => OnNavigationCompleted(core, e);
    }

    // ------------------------------------------------------------------
    // Phase 2 · ISessionSurface (all UI-marshalled; token values are never
    // logged — only SHA-256/8 fingerprints, so a rotation is observable
    // without a byte of either token appearing anywhere)
    // ------------------------------------------------------------------

    /// <summary>Cookie names match the gateway's (PortalAuthClient.AccessCookieName /
    /// RefreshCookieName are internal to Infrastructure, hence the literals).
    /// HttpOnly + Lax + host-only on the GATEWAY host: the page's fetches to
    /// the gateway and chat-edge ride the same registrable host in every
    /// current environment, exactly as the spike's step 2 proved. Secure
    /// follows the gateway scheme (http dev → off, https prod → on).</summary>
    public Task WriteSessionCookiesAsync(string accessToken, string refreshToken) =>
        RunOnUiAsync(() =>
        {
            var core = Browser.CoreWebView2
                ?? throw new InvalidOperationException("WriteSessionCookiesAsync before CoreWebView2 is ready");
            var gateway = new Uri(_settings.Current.GatewayBaseUrl);
            WriteCookie(core, "portal_access_token", accessToken, gateway);
            WriteCookie(core, "portal_refresh_token", refreshToken, gateway);
            _logger.LogInformation(
                "Jar updated for host {Host} (secure={Secure}): access sha256/8={Access}, refresh sha256/8={Refresh}",
                gateway.Host, gateway.Scheme == Uri.UriSchemeHttps,
                Fingerprint(accessToken), Fingerprint(refreshToken));
        });

    private static void WriteCookie(CoreWebView2 core, string name, string value, Uri gateway)
    {
        var cookie = core.CookieManager.CreateCookie(name, value, gateway.Host, "/");
        cookie.IsHttpOnly = true;
        cookie.SameSite = CoreWebView2CookieSameSiteKind.Lax;
        cookie.IsSecure = gateway.Scheme == Uri.UriSchemeHttps;
        core.CookieManager.AddOrUpdateCookie(cookie);
    }

    public Task ClearSessionCookiesAsync() =>
        RunOnUiAsync(() =>
        {
            Browser.CoreWebView2?.CookieManager.DeleteAllCookies();
            _logger.LogInformation("Jar cleared (all cookies deleted)");
        });

    public Task ReloadPageAsync() =>
        RunOnUiAsync(() => Browser.CoreWebView2?.Reload());

    public Task NavigatePageAsync() =>
        RunOnUiAsync(() =>
        {
            _signedIn = true;
            Browser.CoreWebView2?.Navigate(_options.PageUrl);
        });

    public Task NavigateBlankAsync() =>
        RunOnUiAsync(() =>
        {
            _signedIn = false;
            Browser.CoreWebView2?.Navigate("about:blank");
        });

    private Task RunOnUiAsync(Action action) => Dispatcher.InvokeAsync(action).Task;

    private static string Fingerprint(string value)
    {
        var hash = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value));
        return Convert.ToHexString(hash)[..8].ToLowerInvariant();
    }

    // ------------------------------------------------------------------
    // Phase 2 · shell-owned session view
    // ------------------------------------------------------------------

    private void OnSessionStateChanged(ShellSessionState state, string? detail) =>
        Dispatcher.InvokeAsync(() =>
        {
            switch (state)
            {
                case ShellSessionState.SigningIn:
                    ShowSessionOverlay("Signing in… complete the sign-in in your browser.", showButton: false);
                    break;
                case ShellSessionState.SignedIn:
                    _signedIn = true;
                    HideSessionOverlay();
                    break;
                case ShellSessionState.SignedOut:
                    _signedIn = false;
                    ShowSessionOverlay(detail switch
                    {
                        "user" => "You are signed out.",
                        "reauth_loop" => "Sign-in keeps failing — the server is refusing the session. Try again.",
                        "refresh_rejected" => "Your session was ended by the server. Sign in again.",
                        _ => "You are signed out. Sign in opens your browser.",
                    }, showButton: true);
                    break;
            }
        });

    private void ShowSessionOverlay(string status, bool showButton)
    {
        SessionStatusText.Text = status;
        SignInButton.Visibility = showButton ? Visibility.Visible : Visibility.Collapsed;
        SessionOverlay.Visibility = Visibility.Visible;
    }

    private void HideSessionOverlay() => SessionOverlay.Visibility = Visibility.Collapsed;

    private async void SignInButton_Click(object sender, RoutedEventArgs e)
    {
        SignInButton.IsEnabled = false;
        ShowSessionOverlay("Waiting for your browser sign-in…", showButton: false);
        try
        {
            var ok = await _sessionController.InteractiveSignInAsync();
            if (!ok)
            {
                ShowSessionOverlay("Sign-in was cancelled or failed. Try again.", showButton: true);
                return;
            }
            await _sessionController.ApplySessionToBrowserAsync();
            _sessionController.StartRotation();
            await NavigatePageAsync();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Sign-in from the signed-out view failed");
            ShowSessionOverlay("Sign-in failed. Try again.", showButton: true);
        }
        finally
        {
            SignInButton.IsEnabled = true;
        }
    }

    // ------------------------------------------------------------------
    // Diagnostics: CDP console/log receivers + paste instrumentation
    // ------------------------------------------------------------------

    private async Task WireDiagnosticsAsync(CoreWebView2 core)
    {
        // Page console → shell log, DevTools closed. Truncated; the page never
        // logs tokens (HttpOnly cookies), but nothing here should rely on that.
        var console = core.GetDevToolsProtocolEventReceiver("Runtime.consoleAPICalled");
        console.DevToolsProtocolEventReceived += (_, e) =>
        {
            try
            {
                using var doc = JsonDocument.Parse(e.ParameterObjectAsJson);
                var type = doc.RootElement.GetProperty("type").GetString();
                var text = string.Join(' ', doc.RootElement.GetProperty("args").EnumerateArray()
                    .Select(a => a.TryGetProperty("value", out var v) ? v.ToString()
                        : a.TryGetProperty("description", out var d) ? d.GetString() ?? "" : a.ToString()));
                _logger.Log(type is "error" or "assert" ? LogLevel.Warning : LogLevel.Information,
                    "page.console.{Type}: {Text}", type, SafeDiagnosticText(text));
            }
            catch (Exception ex)
            {
                _logger.LogDebug(ex, "console event parse failed");
            }
        };
        var log = core.GetDevToolsProtocolEventReceiver("Log.entryAdded");
        log.DevToolsProtocolEventReceived += (_, e) =>
        {
            try
            {
                using var doc = JsonDocument.Parse(e.ParameterObjectAsJson);
                var entry = doc.RootElement.GetProperty("entry");
                _logger.LogInformation("page.log.{Level} [{Source}]: {Text} {Url}",
                    entry.GetProperty("level").GetString(),
                    entry.GetProperty("source").GetString(),
                    SafeDiagnosticText(entry.GetProperty("text").GetString() ?? ""),
                    entry.TryGetProperty("url", out var u) ? SafeDiagnosticUrl(u.GetString()) : "");
            }
            catch (Exception ex)
            {
                _logger.LogDebug(ex, "log event parse failed");
            }
        };
        await core.CallDevToolsProtocolMethodAsync("Runtime.enable", "{}");
        await core.CallDevToolsProtocolMethodAsync("Log.enable", "{}");

        // Paste probe (Phase 1 §6, hand check 2). Logs WHETHER the paste event
        // fires, WHICH clipboardData.types/items kinds it carries, and whether
        // navigator.clipboard.read() resolves or rejects — the three facts that
        // separate the three causes in the paste table. Never the contents.
        // This is not a bridge: it only writes to the page's own console.
        await core.AddScriptToExecuteOnDocumentCreatedAsync(PasteProbeScript);

        // Phase 4: native chrome a hosted page lacks. Print (Ctrl+P) and zoom
        // (Ctrl+/-/0) come from AreBrowserAcceleratorKeysEnabled; find-in-page
        // does NOT (embedded WebView2 ships no find bar), so inject a minimal
        // one bound to Ctrl+F. It runs only in the TOP document.
        await core.AddScriptToExecuteOnDocumentCreatedAsync(FindBarScript);

        // Exit criterion 4.1 — the escape attempt from inside a REAL canvas
        // frame. AddScriptToExecuteOnDocumentCreated runs in EVERY frame,
        // child srcdoc frames included, with the same reach the frame's own
        // scripts have (spike finding 6). So when an executable canvas artifact
        // is opened, this fires inside that genuine model-authored
        // ExecutableFrame and tries window.chrome.webview.postMessage — exactly
        // what a hostile artifact would. The host must receive nothing: a child
        // frame's message is delivered to CoreWebView2Frame.WebMessageReceived,
        // which BridgeChannel never registers (C-1). Gated behind an arg so
        // normal runs stay quiet. Never posts anything from a child that could
        // succeed — the point is to prove it CANNOT.
        if (_args.Contains("--probe-canvas-escape"))
        {
            await core.AddScriptToExecuteOnDocumentCreatedAsync(CanvasEscapeProbeScript);
            _logger.LogWarning("PROBE canvas-escape ARMED: any child-frame escape attempt must NOT appear as a BRIDGE INBOUND line");
        }
    }

#if DEBUG
    /// <summary>
    /// Exit criterion 3 (Phase 4): Thai IME composition. Exercises the
    /// composition guard at the DOM level — the composingRef path (driven by
    /// compositionstart/end) that guarantees Enter during composition commits
    /// the candidate rather than submitting a half-formed word. Builds a Thai
    /// word with combining upper/lower vowel marks per keystroke (input
    /// events, not paste) and confirms it commits intact. Physical typing on a
    /// Windows Thai keyboard, and a real composing IME, remain owner
    /// hand-checks — but the synthetic events drive the exact same handlers.
    /// </summary>
    private async Task RunImeProbeAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE ime: Thai composition guard + commit");
        // "ที่นี่" = th-ii-tone + n-ii-tone — combining upper vowel (ี) and
        // upper tone marks (่). Built one code point at a time via input
        // events to simulate typing, not pasting.
        var script =
            "(function(){" +
            "var ta=document.querySelector('textarea');if(!ta)return 'no-textarea';ta.focus();" +
            "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;" +
            "function type(s){set.call(ta,ta.value+s);ta.dispatchEvent(new Event('input',{bubbles:true}));}" +
            "function enter(){var e=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});ta.dispatchEvent(e);return e.defaultPrevented;}" +
            "set.call(ta,'');ta.dispatchEvent(new Event('input',{bubbles:true}));" +
            // Start composing (IME candidate window open).
            "ta.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));" +
            "var word='\\u0e17\\u0e35\\u0e48\\u0e19\\u0e35\\u0e48';" + // ที่นี่
            "for(var i=0;i<word.length;i++){type(word[i]);}" +
            "var midValue=ta.value;" +
            // Enter DURING composition must NOT submit (must be prevented/blocked).
            "var submittedDuring=(ta.value===''&&!ta.value);var enterDuringPrevented=enter();var valueAfterEnterDuring=ta.value;" +
            // End composition, then Enter commits/sends.
            "ta.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:word}));" +
            "var enterAfterPrevented=enter();" +
            "return JSON.stringify({typedWord:midValue,typedComplete:midValue===word,valueStillPresentDuringComposition:valueAfterEnterDuring===word,enterDuringComposition_prevented:enterDuringPrevented,enterAfterComposition_prevented:enterAfterPrevented});" +
            "})();";
        var result = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(script)).Task.Unwrap();
        _logger.LogInformation("PROBE ime: {Result}", result);
    }

    /// <summary>
    /// Exit criterion 4 (Phase 4): find-in-page, print, zoom. Find is the
    /// injected bar (Ctrl+F); print + zoom come from the WebView2 accelerator
    /// keys / controller. Dispatches synthetic events (not physical input, per
    /// the no-input-injection rule) to exercise the DOM-level behaviour, and
    /// reads/sets ZoomFactor to prove the zoom control. Physical Ctrl+P (opens
    /// a modal) and physical keyboard find remain owner hand-checks.
    /// </summary>
    private async Task RunChromeProbesAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE chrome: find / print / zoom");
        // 1. Find bar: dispatch a synthetic Ctrl+F keydown on document; the
        //    injected listener should build + show the bar.
        var findResult = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){document.dispatchEvent(new KeyboardEvent('keydown',{key:'f',ctrlKey:true,bubbles:true}));" +
            "var bar=document.querySelector('[data-sran-findbar]');" +
            "if(!bar)return 'no-findbar';var inp=bar.querySelector('input');inp.value='Chat';" +
            "var found=window.find?window.find('Chat',false,false,true):null;" +
            "return JSON.stringify({barVisible:bar.style.display!=='none',hasInput:!!inp,windowFind:found});})();")).Task.Unwrap();
        _logger.LogInformation("PROBE chrome find: {Result}", findResult);

        // 2. Print availability (physical Ctrl+P opens a native modal; here just
        //    confirm the page-level print entry point exists).
        var printResult = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "JSON.stringify({windowPrint: typeof window.print})")).Task.Unwrap();
        _logger.LogInformation("PROBE chrome print: {Result} (accelerator Ctrl+P → native print; AreBrowserAcceleratorKeysEnabled={Acc})",
            printResult, core.Settings.AreBrowserAcceleratorKeysEnabled);

        // 3. Zoom: read, set 1.25, read back — proves the zoom control (Ctrl+/-
        //    drive the same ZoomFactor natively via accelerator keys).
        await Dispatcher.InvokeAsync(() =>
        {
            var before = Browser.ZoomFactor;
            Browser.ZoomFactor = 1.25;
            var after = Browser.ZoomFactor;
            _logger.LogInformation("PROBE chrome zoom: ZoomFactor {Before} → {After} (Ctrl+/- drive this natively)", before, after);
            Browser.ZoomFactor = before; // restore
        });
    }

    /// <summary>
    /// Exit criterion 4.3 (live paths): post malformed inbound messages from
    /// the real page context and show each is dropped and logged. Covers wrong
    /// nonce, unknown type, and a path-shaped attachment handle. (Wrong e.Source
    /// cannot be produced from the real same-origin page — that one stays a unit
    /// test, stated as such in the report.)
    /// </summary>
    private async Task RunRejectionProbesAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE reject: posting malformed inbound messages; each must be DROPPED + logged");
        // 1. Wrong nonce.
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "window.chrome.webview.postMessage({nonce:'WRONG-NONCE-000',type:'ui.ready',payload:{}});" +
            "console.log('[reject-probe] posted wrong-nonce ui.ready');"));
        await Task.Delay(400);
        // 2. Unknown type (valid nonce).
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=window.__SRAN_BRIDGE__;window.chrome.webview.postMessage({nonce:b.nonce,type:'turn.frobnicate',payload:{}});" +
            "console.log('[reject-probe] posted unknown-type turn.frobnicate');})();"));
        await Task.Delay(400);
        // 3. turn.submit with a path-shaped attachment handle (valid nonce).
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=window.__SRAN_BRIDGE__;window.chrome.webview.postMessage({nonce:b.nonce,type:'turn.submit'," +
            "payload:{conversationId:'x',text:'hi',attachmentHandles:['C:\\\\Users\\\\secret.txt']}});" +
            "console.log('[reject-probe] posted turn.submit with a filesystem path handle');})();"));
        await Task.Delay(600);
        _logger.LogInformation("PROBE reject: all three posted; expect BRIDGE INBOUND DROPPED lines (nonce-mismatch, unknown-type, attachmentHandle-path-shaped) and NO turn started");
    }

    /// <summary>Phase 5 §2/§1 — post real host.query messages (valid nonce,
    /// allow-listed origin) through the running BridgeChannel → dispatcher →
    /// real services. Refusals AND a valid round-trip, so an empty refusal log
    /// is not mistaken for a dead channel. All ops chosen are non-interactive
    /// (folders.pickAndGrant opens a native picker and is excluded here — its
    /// no-path-argument property is structural and unit-pinned).</summary>
    private async Task RunHostQueryProbeAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE hostquery: exercising the control channel live (expect refusals + one valid round-trip)");
        // A page-side listener echoes each host.result to the console so the
        // outcome is visible from the page too, not only the host log.
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "window.chrome.webview.addEventListener('message',function(e){if(e.data&&e.data.type==='host.result')" +
            "console.log('[hq-result] '+JSON.stringify(e.data.payload));});"));

        async Task Post(string label, string op, string argsJson)
        {
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var b=window.__SRAN_BRIDGE__;window.chrome.webview.postMessage(" +
                $"{{nonce:b.nonce,type:'host.query',payload:{{requestId:'probe-{label}',op:'{op}',args:{argsJson}}}}});" +
                $"console.log('[hq] posted {label} ({op})');}})();"));
            await Task.Delay(500);
        }

        // Valid round-trips first (prove the channel records arrivals and returns
        // data). Reads only — the probe must not mutate the owner's real settings.
        await Post("get", "settings.get", "{}");
        await Post("skills", "skills.list", "{}");
        await Post("folders", "folders.list", "{}");
        // Refusals — the exact inputs a hostile page would send (well-formed but not permitted).
        await Post("grantkey", "settings.set", "{key:'granted_folders',value:['C:\\\\']}");
        await Post("apprkey", "settings.set", "{key:'approval_mode',value:'skip'}");
        await Post("badval", "settings.set", "{key:'theme',value:'Neon'}");
        await Post("unknownop", "skills.wipe", "{}");
        await Post("revokeghost", "folders.revoke", "{folder:'C:\\\\Never\\\\Granted'}");
        _logger.LogInformation("PROBE hostquery: done. Expect in the log: BRIDGE HOST.QUERY lines; 'host.query SettingsGet/SkillsList/... ok=True'; " +
            "'settings.set REFUSED: key granted_folders'/'approval_mode'; ok=False error=invalid for theme=Neon; " +
            "'BRIDGE INBOUND DROPPED: host.query invalid: unknown-op:skills.wipe'; ok=False error=not-found for the revoke.");
    }

    /// <summary>Phase 5 §4 — install the SAME global handlers startBridge()
    /// installs (window.onerror + onunhandledrejection → host.query
    /// diag.reportRendererError), then throw for real and reject a promise for
    /// real. Faithful to the hosted page's mechanism even on a non-hosted build,
    /// because it injects the identical handler code.</summary>
    private async Task RunRendererErrorProbeAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE renderer-error: installing window.onerror/onunhandledrejection → diag.reportRendererError, then throwing");
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=window.__SRAN_BRIDGE__;function report(kind,message,stack){" +
            "window.chrome.webview.postMessage({nonce:b.nonce,type:'host.query',payload:{requestId:'probe-crash-'+kind," +
            "op:'diag.reportRendererError',args:{kind:kind,message:message,stack:stack}}});}" +
            "window.addEventListener('error',function(e){report('error',e.message,e.error&&e.error.stack);});" +
            "window.addEventListener('unhandledrejection',function(e){var r=e.reason;report('unhandledrejection',(r&&r.message)||String(r),r&&r.stack);});" +
            "console.log('[crash-probe] handlers installed');})();"));
        await Task.Delay(300);
        // A real throw from a timer (reaches window.onerror) and a real rejected promise.
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "setTimeout(function(){throw new Error('probe synthetic render error');},0);" +
            "Promise.reject(new Error('probe synthetic unhandled rejection'));"));
        await Task.Delay(800);
        _logger.LogInformation("PROBE renderer-error: done. Expect two 'Renderer error reported (kind=error/unhandledrejection)' lines from the host, and host.query DiagReportRendererError ok=True.");
    }

    /// <summary>Phase 6a S6 — send a multi-step task, let a long tool start, click
    /// Stop while it is visibly running (cancel mid-tool), screenshot the cancelled
    /// state, then send a follow-up to continue. No RSS-sampling block, so the
    /// cancel lands on time.</summary>
    private async Task RunCancelContinueProbeAsync(CoreWebView2 core, string dir)
    {
        Directory.CreateDirectory(dir);
        var promptPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SRAN Desktop", "probe-prompt.txt");
        var prompt = File.Exists(promptPath) ? (await File.ReadAllTextAsync(promptPath)).Trim() : "Run: ping -n 20 127.0.0.1";
        _logger.LogWarning("PROBE cancel-continue: send task, cancel mid-tool, then continue");
        await Dispatcher.InvokeAsync(() => core.Navigate("http://localhost:3002/chat"));
        await Task.Delay(4500);

        async Task Type(string text)
        {
            var json = JsonSerializer.Serialize(text);
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var ta=document.querySelector('textarea');if(!ta)return;" +
                "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;" +
                "set.call(ta," + json + ");ta.dispatchEvent(new Event('input',{bubbles:true}));ta.focus();})();"));
            await Task.Delay(400);
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
                "var s=bs.find(function(b){return /^(Send|\\u0e2a\\u0e48\\u0e07)$/.test((b.getAttribute('aria-label')||'').trim());});" +
                "if(s&&!s.disabled)s.click();})();"));
        }

        await Type(prompt);
        await Task.Delay(13000); // let the plan render + the ping start and be visibly running
        var stopped = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
            "var s=bs.find(function(b){var l=(b.getAttribute('aria-label')||'').trim();" +
            "return /stop/i.test(l)||/\\u0e2b\\u0e22\\u0e38\\u0e14/.test(l);});" +
            "if(s){s.click();return 'clicked: '+s.getAttribute('aria-label');}return 'NO STOP BUTTON';})();")).Task.Unwrap();
        _logger.LogWarning("PROBE cancel-continue: stop -> {Stopped}", stopped);
        await Task.Delay(1500);
        await using (var fs = File.Create(Path.Combine(dir, "s6-cancelled.png")))
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);

        await Type("continue where you left off and finish the task");
        await Task.Delay(12000);
        await using var fs2 = File.Create(Path.Combine(dir, "s6-continued.png"));
        await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs2);
        var tail = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=document.body?document.body.innerText:'';return b.slice(-700);})();")).Task.Unwrap();
        _logger.LogWarning("PROBE cancel-continue: transcript tail = {Tail}", tail);
        _logger.LogInformation("PROBE cancel-continue: done ({Dir})", dir);
    }

    /// <summary>Turn-parity §1 — send two sequential turns to the SAME
    /// conversation (from probe-turn1.txt / probe-turn2.txt) and screenshot the
    /// transcript, so the two-turn memory test can be run live. Reveals whether
    /// turn 2 carries any of turn 1 (it does not, until history is wired).</summary>
    private async Task RunMultiTurnProbeAsync(CoreWebView2 core, string dir)
    {
        Directory.CreateDirectory(dir);
        var baseDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SRAN Desktop");
        var t1 = File.Exists(Path.Combine(baseDir, "probe-turn1.txt"))
            ? (await File.ReadAllTextAsync(Path.Combine(baseDir, "probe-turn1.txt"))).Trim() : "Remember the number 424242.";
        var t2 = File.Exists(Path.Combine(baseDir, "probe-turn2.txt"))
            ? (await File.ReadAllTextAsync(Path.Combine(baseDir, "probe-turn2.txt"))).Trim() : "What number did I ask you to remember? Reply with just the number.";
        _logger.LogWarning("PROBE multiturn: two sequential turns in one conversation (memory across turns)");
        await Dispatcher.InvokeAsync(() => core.Navigate("http://localhost:3002/chat"));
        await Task.Delay(4500);

        async Task SendAsync(string text, string label)
        {
            var json = JsonSerializer.Serialize(text);
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var ta=document.querySelector('textarea');if(!ta)return;" +
                "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;" +
                "set.call(ta," + json + ");ta.dispatchEvent(new Event('input',{bubbles:true}));ta.focus();})();"));
            await Task.Delay(500);
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
                "var s=bs.find(function(b){return /^(Send|\\u0e2a\\u0e48\\u0e07)$/.test((b.getAttribute('aria-label')||'').trim());});" +
                "if(s&&!s.disabled)s.click();})();"));
            _logger.LogInformation("PROBE multiturn: sent {Label}", label);
        }

        // Browser/computer turns run a multi-tool chain — 9s is fine for the
        // memory test but truncates those, so honour an optional per-turn wait
        // (--probe-multiturn passes just a dir; the wait is read from a sidecar
        // file so the signature stays put).
        var waitMs = 9000;
        var waitPath = Path.Combine(baseDir, "probe-turn-wait.txt");
        if (File.Exists(waitPath) && int.TryParse((await File.ReadAllTextAsync(waitPath)).Trim(), out var w) && w is > 0 and <= 120000)
            waitMs = w;
        await SendAsync(t1, "turn 1");
        await Task.Delay(waitMs);
        await SendAsync(t2, "turn 2");
        await Task.Delay(waitMs);
        await using var fs = File.Create(Path.Combine(dir, "multiturn.png"));
        await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
        var answer = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=document.body?document.body.innerText:'';return b.slice(-600);})();")).Task.Unwrap();
        _logger.LogWarning("PROBE multiturn: transcript tail = {Tail}", answer);
        _logger.LogInformation("PROBE multiturn: done ({Dir})", dir);
    }

    /// <summary>Phase 5 §5.8 — force a pause (run with --force-step-limit so
    /// maxIterations=1), then attempt a resume. Drives a multi-step prompt so the
    /// turn pauses after step 1 (AgentPausedEvent → "paused" turn.event → the
    /// paused card), screenshots it, then posts a "continue" turn.submit (exactly
    /// what the Continue button does) and screenshots the result — which reveals
    /// whether resume carries the paused task's context or starts fresh.</summary>
    private async Task RunPauseResumeProbeAsync(CoreWebView2 core, string dir)
    {
        Directory.CreateDirectory(dir);
        _logger.LogWarning("PROBE pause-resume: forcing a pause, then attempting a Continue-style resume");
        await Dispatcher.InvokeAsync(() => core.Navigate("http://localhost:3002/chat"));
        await Task.Delay(4500);
        const string prompt = "Do this in two dependent steps, using tools, no confirmation: " +
            "step 1, run_python to compute sum(range(1,101)) and print it. " +
            "step 2, AFTER you see that number, use run_command to run: echo the-number-was <that number>.";
        var promptJson = JsonSerializer.Serialize(prompt);
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var ta=document.querySelector('textarea');if(!ta)return;" +
            "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;" +
            "set.call(ta," + promptJson + ");ta.dispatchEvent(new Event('input',{bubbles:true}));ta.focus();})();"));
        await Task.Delay(500);
        var convId = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
            "var s=bs.find(function(b){return /^(Send|\\u0e2a\\u0e48\\u0e07)$/.test((b.getAttribute('aria-label')||'').trim());});" +
            "if(s&&!s.disabled)s.click();return location.pathname;})();")).Task.Unwrap();
        _logger.LogInformation("PROBE pause-resume: sent multi-step prompt (path {Path})", convId);
        await Task.Delay(14000); // let step 1 run and the turn pause at the 1-step limit
        await using (var fs = File.Create(Path.Combine(dir, "paused.png")))
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
        var paused = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var p=document.querySelector('[data-testid=\"paused-card\"]');" +
            "return p?('PAUSED CARD: '+p.textContent):'no paused card';})();")).Task.Unwrap();
        _logger.LogWarning("PROBE pause-resume: {Paused}", paused);

        // Resume exactly as the Continue button does: a fresh turn.submit with
        // "continue" for this conversation. BuildMessages(text) sends only
        // [system, "continue"] — no paused tool-chain — so this reveals whether
        // the coordinator actually resumes or starts a context-free turn.
        var cid = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var m=location.pathname.match(/\\/chat\\/(.+)$/);return m?m[1]:'';})();")).Task.Unwrap();
        cid = cid.Trim('"');
        if (cid.Length > 0)
        {
            _logger.LogWarning("PROBE pause-resume: posting Continue (turn.submit 'continue') for {Cid}", cid);
            await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                "(function(){var b=window.__SRAN_BRIDGE__;window.chrome.webview.postMessage(" +
                $"{{nonce:b.nonce,type:'turn.submit',payload:{{conversationId:'{cid}',text:'continue',attachmentHandles:[],modelId:null}}}});}})();"));
            await Task.Delay(12000);
            await using var fs2 = File.Create(Path.Combine(dir, "resume-attempt.png"));
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs2);
        }
        _logger.LogInformation("PROBE pause-resume: done ({Dir})", dir);
    }

    /// <summary>Phase 5 §1 — visit each desktop-only screen in the running shell
    /// and screenshot it (proves it RENDERS with real data from a live host.query
    /// round-trip, not just that a route exists), then drive one real primary
    /// action through a screen control (a skills toggle) and reload to prove the
    /// change persisted.</summary>
    private async Task RunScreensProbeAsync(CoreWebView2 core, string dir)
    {
        Directory.CreateDirectory(dir);
        _logger.LogWarning("PROBE screens: visiting the five desktop-only screens and screenshotting each");

        async Task Shot(string route, string name)
        {
            await Dispatcher.InvokeAsync(() => core.Navigate($"http://localhost:3002{route}"));
            await Task.Delay(3500); // nav + React mount + host.query data load
            try
            {
                await using var fs = File.Create(Path.Combine(dir, name + ".png"));
                await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
                var title = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    "(function(){var h=document.querySelector('h1');return h?h.textContent:document.title;})();")).Task.Unwrap();
                _logger.LogInformation("PROBE screens: {Route} → {Name}.png (h1={Title})", route, name, title);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "PROBE screens: capture failed for {Route}", route);
            }
        }

        await Shot("/chat/settings", "settings");
        await Shot("/chat/skills", "skills");
        await Shot("/chat/tasks", "tasks");
        await Shot("/chat/connectors", "connectors");
        await Shot("/chat/writing-style", "writing-style");

        // Primary action + persistence: on Skills, read the first toggle's state,
        // flip it, reload, and confirm the new state survived (a real write round
        // trip through a screen control, not just a screenshot).
        await Dispatcher.InvokeAsync(() => core.Navigate("http://localhost:3002/chat/skills"));
        await Task.Delay(3500);
        var before = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var t=document.querySelector('input[type=checkbox],[role=switch]');" +
            "if(!t)return 'no-toggle';var checked=t.getAttribute('aria-checked')||t.checked;t.click();" +
            "return 'flipped from '+checked;})();")).Task.Unwrap();
        _logger.LogInformation("PROBE screens: skills toggle {Before}", before);
        await Task.Delay(1200);
        await using (var fs = File.Create(Path.Combine(dir, "skills-toggled.png")))
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
        await Dispatcher.InvokeAsync(() => core.Reload());
        await Task.Delay(3500);
        var after = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var t=document.querySelector('input[type=checkbox],[role=switch]');" +
            "if(!t)return 'no-toggle';return 'after reload: '+(t.getAttribute('aria-checked')||t.checked);})();")).Task.Unwrap();
        _logger.LogInformation("PROBE screens: skills toggle persisted check → {After}", after);
        await using (var fs2 = File.Create(Path.Combine(dir, "skills-after-reload.png")))
            await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs2);
        _logger.LogInformation("PROBE screens: done ({Dir})", dir);
    }

    /// <summary>Phase 5 §6 — run the migrator against the REAL local store on
    /// this machine (not a synthetic DB). Logs counts before/after and timing.</summary>
    private async Task RunMigrateProbeAsync()
    {
        var exportDir = System.IO.Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SRAN Desktop", "migration-export");
        _logger.LogWarning("PROBE migrate: running ShellConversationMigrator against the REAL local store");
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var result = await _migrator.MigrateAsync(exportDir);
        sw.Stop();
        _logger.LogInformation(
            "PROBE migrate RESULT: total={Total} onServer={OnServer} migratedUp={Migrated} localOnlyRemaining={Remaining} exportPath={Path} elapsedMs={Ms}",
            result.Total, result.OnServer, result.MigratedUp, result.LocalOnlyRemaining, result.ExportPath ?? "(none)", sw.ElapsedMilliseconds);
    }

    /// <summary>
    /// Drive the real composer (ChatInput textarea + Send button) so a real
    /// agent turn runs end to end through the bridge and RENDERS in the page —
    /// the transport swap proven with the shipped renderer untouched. The
    /// prompt is read from %APPDATA%\SRAN Desktop\probe-prompt.txt to avoid
    /// command-line escaping of Thai / spaces / quotes.
    /// </summary>
    private async Task RunComposeProbeAsync(CoreWebView2 core, string conversationId)
    {
        var promptPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SRAN Desktop", "probe-prompt.txt");
        var prompt = File.Exists(promptPath) ? (await File.ReadAllTextAsync(promptPath)).Trim() : "Say hello in one short sentence.";
        _logger.LogInformation("PROBE compose: {ToolCount} local tools registered (e.g. {Sample})",
            _tools.All.Count, string.Join(", ", _tools.All.Take(6).Select(t => t.Name)));
        // "new" → the root composer, which creates a fresh conversation on send.
        var origin = NavigationPolicy.OriginOf(new Uri(_options.PageUrl));
        var url = conversationId == "new" ? $"{origin}/chat" : $"{origin}/chat/{conversationId}";
        _logger.LogWarning("PROBE compose: navigating to {Url} and sending a {Len}-char prompt", SafeDiagnosticUrl(url), prompt.Length);
        await Dispatcher.InvokeAsync(() => core.Navigate(url));
        await Task.Delay(4500);

        var promptJson = JsonSerializer.Serialize(prompt);
        var typeResult = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var ta=document.querySelector('textarea');if(!ta)return 'no-textarea';" +
            "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;" +
            "set.call(ta," + promptJson + ");ta.dispatchEvent(new Event('input',{bubbles:true}));" +
            "ta.focus();return 'typed';})();")).Task.Unwrap();
        _logger.LogInformation("PROBE compose: type result = {Result}", typeResult);
        await Task.Delay(500);
        var sendResult = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
            "var s=bs.find(function(b){return /^(Send|\\u0e2a\\u0e48\\u0e07)$/.test((b.getAttribute('aria-label')||'').trim());});" +
            "if(s&&!s.disabled){s.click();return 'sent';}return s?'send-disabled':'no-send-button';})();")).Task.Unwrap();
        _logger.LogInformation("PROBE compose: send result = {Result}", sendResult);

        // §5.10 — sample RSS across the turn (which should carry tool cards +
        // streaming shell output) so a real PEAK is captured, not just idle.
        double peakHost = 0, peakEdge = 0, peakTotal = 0;
        for (var i = 0; i < 18; i++)
        {
            await Task.Delay(1500);
            using var host = Process.GetCurrentProcess();
            var edge = Process.GetProcessesByName("msedgewebview2");
            var hostMb = host.WorkingSet64 / 1048576.0;
            var edgeMb = edge.Sum(p => p.WorkingSet64) / 1048576.0;
            foreach (var p in edge) p.Dispose();
            var total = hostMb + edgeMb;
            if (total > peakTotal) { peakTotal = total; peakHost = hostMb; peakEdge = edgeMb; }
        }
        _logger.LogWarning("PROBE compose RSS PEAK during turn: host {Host:F1} MB + webview2 {Edge:F1} MB = {Total:F1} MB total",
            peakHost, peakEdge, peakTotal);
    }

    /// <summary>
    /// Exit criterion 4.1 (real-artifact path): open an EXISTING conversation
    /// that already contains an executable canvas artifact, click "Open in
    /// Canvas" to mount the genuine ExecutableFrame, and let the
    /// --probe-canvas-escape document-created probe attempt the escape from
    /// inside that real model-authored frame. Then post a valid TOP-document
    /// message so the inbound log is proven to record arrivals. No new turn is
    /// created — the artifact already exists.
    /// </summary>
    private async Task RunOpenRealCanvasProbeAsync(CoreWebView2 core, string conversationId)
    {
        var url = $"{NavigationPolicy.OriginOf(new Uri(_options.PageUrl))}/chat/{conversationId}";
        _logger.LogWarning("PROBE open-canvas: navigating to the existing conversation {Url}", SafeDiagnosticUrl(url));
        await Dispatcher.InvokeAsync(() => core.Navigate(url));
        await Task.Delay(4500); // messages load + render the code block

        // Listen (top document, captured) for the real canvas frame's own
        // parent.postMessage. The shipped CANVAS_BOOTSTRAP posts
        // {__sranCanvas:true,type:'ready'|'error'} to parent — so seeing it
        // proves the REAL ExecutableFrame mounted and its content executed.
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "window.addEventListener('message',function(e){try{" +
            "if(e.data&&e.data.__sranCanvas)console.log('[top-listener] REAL CANVAS FRAME ran: type='+e.data.type);" +
            "}catch(x){}});"));

        // Click the "Open in Canvas" button (en or th aria-label). This mounts
        // the real ExecutableFrame.
        var clickResult = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
            "var t=bs.find(function(b){return /Open in Canvas|\\u0e40\\u0e1b\\u0e34\\u0e14\\u0e43\\u0e19 Canvas/i.test(b.getAttribute('aria-label')||'');});" +
            "if(t){t.scrollIntoView();t.click();return 'clicked';}return 'not-found:buttons='+bs.length;})();")).Task.Unwrap();
        _logger.LogInformation("PROBE open-canvas: click result = {Result}", clickResult);
        await Task.Delay(3000); // ExecutableFrame mounts

        // Confirm the real sandboxed frame exists in the DOM and report its
        // sandbox attribute (must be exactly the executable-canvas value).
        var frameInfo = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var fs=Array.from(document.querySelectorAll('iframe'));" +
            "return JSON.stringify(fs.map(function(f){return {sandbox:f.getAttribute('sandbox'),hasSrcdoc:!!f.getAttribute('srcdoc'),testid:f.getAttribute('data-testid')};}));})();")).Task.Unwrap();
        _logger.LogInformation("PROBE open-canvas: iframes now in DOM = {Frames}", frameInfo);

        _logger.LogInformation("PROBE open-canvas: real canvas frame mounted (see [top-listener] REAL CANVAS FRAME + iframe sandbox above). Any 'BRIDGE INBOUND' carrying a canvas-child probe would be a FAILURE. Now posting a VALID top-document message:");
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=window.__SRAN_BRIDGE__;" +
            "if(!b){console.log('[top-probe] no bridge handshake present');return;}" +
            "window.chrome.webview.postMessage({nonce:b.nonce,type:'ui.ready',payload:{probe:'top-after-canvas'}});" +
            "console.log('[top-probe] posted valid ui.ready from TOP document');})();"));
        await Task.Delay(1200);
    }
#endif

    private const string FindBarScript = """
        (function () {
          if (window !== window.top) return;
          var bar = null, input = null, count = null;
          function build() {
            bar = document.createElement('div');
            bar.setAttribute('data-sran-findbar', '1');
            bar.style.cssText = 'position:fixed;top:12px;right:12px;z-index:2147483647;display:flex;align-items:center;gap:6px;'
              + 'background:#2a2a2a;color:#eee;border:1px solid #555;border-radius:8px;padding:6px 8px;font:13px sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.4)';
            input = document.createElement('input');
            input.type = 'text'; input.placeholder = 'Find in page';
            input.style.cssText = 'background:#1e1e1e;color:#eee;border:1px solid #555;border-radius:4px;padding:3px 6px;outline:none;width:180px';
            count = document.createElement('span'); count.style.cssText = 'color:#aaa;min-width:20px;text-align:center';
            function mkbtn(txt, fn) { var b = document.createElement('button'); b.textContent = txt;
              b.style.cssText = 'background:#3a3a3a;color:#eee;border:1px solid #555;border-radius:4px;padding:2px 8px;cursor:pointer'; b.onclick = fn; return b; }
            var prev = mkbtn('▲', function(){ doFind(true); });
            var next = mkbtn('▼', function(){ doFind(false); });
            var close = mkbtn('✕', hide);
            bar.appendChild(input); bar.appendChild(count); bar.appendChild(prev); bar.appendChild(next); bar.appendChild(close);
            document.body.appendChild(bar);
            input.addEventListener('keydown', function(e){
              if (e.key === 'Enter') { e.preventDefault(); doFind(e.shiftKey); }
              else if (e.key === 'Escape') { e.preventDefault(); hide(); }
            });
            input.addEventListener('input', function(){ doFind(false, true); });
          }
          function doFind(backwards, fromStart) {
            var q = input.value; if (!q) { count.textContent = ''; return; }
            try {
              if (fromStart) { var sel = window.getSelection(); if (sel) sel.removeAllRanges(); }
              var found = window.find(q, false, backwards, true, false, false, false);
              count.textContent = found ? '•' : '0';
            } catch (e) { count.textContent = '?'; }
          }
          function show() { if (!bar) build(); bar.style.display = 'flex'; input.focus(); input.select(); }
          function hide() { if (bar) bar.style.display = 'none'; var s = window.getSelection(); if (s) s.removeAllRanges(); }
          document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); show(); }
          }, true);
        })();
        """;

    private const string CanvasEscapeProbeScript = """
        (function () {
          try {
            var isTop = (window === window.top);
            if (isTop) return; // only child frames are the escape surface
            var href = location.href;
            var body = document.body ? document.body.innerHTML.slice(0, 80) : '(no body yet)';
            console.log('[canvas-escape] CHILD frame href=' + href + ' body=' + body);
            if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
              window.chrome.webview.postMessage({ probe: 'from-canvas-child', href: href });
              console.log('[canvas-escape] CHILD postMessage did NOT throw (delivery is frame-scoped; the host must still receive nothing)');
            } else {
              console.log('[canvas-escape] no chrome.webview.postMessage in CHILD frame');
            }
          } catch (e) { console.log('[canvas-escape] CHILD error: ' + e); }
        })();
        """;

    private const string PasteProbeScript = """
        (function () {
          try {
            document.addEventListener('paste', function (e) {
              var cd = e.clipboardData;
              var types = cd ? Array.from(cd.types) : null;
              var items = cd ? Array.from(cd.items).map(function (i) { return i.kind + ':' + i.type; }) : null;
              var files = cd ? cd.files.length : -1;
              console.log('[shell-probe] paste event fired: target=' + (e.target && e.target.tagName)
                + ' types=' + JSON.stringify(types) + ' items=' + JSON.stringify(items) + ' files=' + files);
              if (navigator.clipboard && navigator.clipboard.read) {
                navigator.clipboard.read().then(function (cs) {
                  console.log('[shell-probe] navigator.clipboard.read RESOLVED: ' + JSON.stringify(cs.map(function (c) { return c.types; })));
                }, function (err) {
                  console.log('[shell-probe] navigator.clipboard.read REJECTED: ' + err.name + ' - ' + err.message);
                });
              } else {
                console.log('[shell-probe] navigator.clipboard.read unavailable');
              }
            }, true);
            window.addEventListener('error', function (e) {
              console.error('[shell-probe] window.error: ' + e.message + ' @ ' + e.filename + ':' + e.lineno);
            });
            window.addEventListener('unhandledrejection', function (e) {
              var r = e.reason; console.error('[shell-probe] unhandledrejection: ' + (r && (r.stack || r.message || String(r))));
            });
            console.log('[shell-probe] instrumentation installed on ' + location.href);
          } catch (err) { console.error('[shell-probe] install failed: ' + err); }
        })();
        """;

    private static string SafeDiagnosticText(string value)
    {
        var redacted = SensitiveDiagnosticValue.Replace(value, "$1$2[REDACTED]");
        redacted = BearerValue.Replace(redacted, "Bearer [REDACTED]");
        redacted = JwtValue.Replace(redacted, "[REDACTED JWT]");
        redacted = PrefixedKeyValue.Replace(redacted, "[REDACTED KEY]");
        return redacted.Length <= 600 ? redacted : redacted[..600] + "...";
    }

    private static string SafeDiagnosticUrl(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri))
            return SafeDiagnosticText(value ?? "");
        if (uri.Scheme is not ("http" or "https"))
            return $"{uri.Scheme}:[details omitted]";
        return NavigationPolicy.OriginOf(uri) + uri.AbsolutePath;
    }

    private void ReportCrashBestEffort(Exception exception)
    {
        try
        {
            if (_crashReports.IsEnabled)
                _ = _crashReports.ReportAsync(exception);
        }
        catch
        {
            // Never crash the crash path.
        }
    }

    // ------------------------------------------------------------------
    // Navigation completed: measurements + debug probes
    // ------------------------------------------------------------------

    private void OnNavigationCompleted(CoreWebView2 core, CoreWebView2NavigationCompletedEventArgs e)
    {
        var uri = core.Source;
        // The shell-owned view stays up until the page actually renders —
        // "no spinner over a blank WebView" (§2.1).
        if (e.IsSuccess && _signedIn && uri != "about:blank")
        {
            HideSessionOverlay();
            // C-3: mint + inject a fresh nonce into the TOP document after every
            // successful page navigation. ExecuteScriptAsync runs in the top
            // document only, so a child artifact frame never sees the nonce.
            if (_bridgeAttached && _policy.IsTopLevelAllowed(uri))
                _ = _bridge.InjectNonceAsync();
        }
        if (!_firstNavigationLogged)
        {
            _firstNavigationLogged = true;
            var elapsed = DateTime.UtcNow - App.ProcessStartUtc;
            var host = Process.GetCurrentProcess();
            var edge = Process.GetProcessesByName("msedgewebview2")
                .Where(p => { try { return p.StartTime.ToUniversalTime() >= App.ProcessStartUtc; } catch { return false; } })
                .ToList();
            _logger.LogInformation(
                "FIRST NAVIGATION {Status} {Uri} | cold start {Elapsed} ms | host RSS {HostMb:F1} MB | webview2 procs {Count} totalling {EdgeMb:F1} MB (started after host)",
                e.IsSuccess ? "OK" : $"FAILED ({e.WebErrorStatus})", uri, (long)elapsed.TotalMilliseconds,
                host.WorkingSet64 / 1048576.0, edge.Count, edge.Sum(p => p.WorkingSet64) / 1048576.0);

            _ = RunDebugProbesAsync(core);
        }
        else
        {
            _logger.LogInformation("Navigation {Status} {Uri}", e.IsSuccess ? "OK" : $"FAILED ({e.WebErrorStatus})", uri);
        }
    }

    /// <summary>Debug-only command-line probes used to produce Phase 1+2 evidence:
    ///   --probe-navigate &lt;url&gt;   ask the WebView to go somewhere (expect refusal when off-list)
    ///   --probe-clipboard         execute read_clipboard through the registry (verifies Application.Current)
    ///   --probe-jar-hash          log name + sha256/8 + count of the jar's session cookies (never values)
    ///   --probe-rotate-now        force one host rotation, then log the jar and have the page hit /portal/me
    ///   --probe-corrupt-access &lt;ms&gt;  after a delay, overwrite the jar's access cookie with garbage and
    ///                             reload — drives the criterion-5 portal-redirect interception
    ///   --probe-signout &lt;ms&gt;     after a delay, host-initiated sign-out (criterion 6)
    ///   --exit-after &lt;ms&gt;        close the shell after a delay (measurement automation)
    /// Absent in Release builds.</summary>
    private async Task RunDebugProbesAsync(CoreWebView2 core)
    {
#if DEBUG
        try
        {
            var navTo = ArgAfter("--probe-navigate");
            if (navTo is not null)
            {
            _logger.LogInformation("PROBE: requesting navigation to {Uri}", SafeDiagnosticUrl(navTo));
                await Dispatcher.InvokeAsync(() => core.Navigate(navTo));
                // Give the (possibly cancelled) navigation a moment, then report where we are.
                await Task.Delay(1500);
                _logger.LogInformation("PROBE: after request, core.Source = {Source}", SafeDiagnosticUrl(core.Source));
            }

            if (_args.Contains("--probe-clipboard"))
            {
                var tool = _tools.All.FirstOrDefault(t => t.Name == "read_clipboard");
                if (tool is null)
                {
                    _logger.LogWarning("PROBE: read_clipboard not registered");
                }
                else
                {
                    // Only READ; a write would clobber the user's clipboard. The
                    // result text is never logged — only whether the STA hop worked.
                    using var doc = JsonDocument.Parse("{}");
                    var result = await tool.ExecuteAsync(doc.RootElement, CancellationToken.None);
                    var dispatcherAvailable = !(result.Error?.Contains("no UI dispatcher", StringComparison.Ordinal) ?? false);
                    _logger.LogInformation("PROBE: read_clipboard executed — success={Success} dispatcherAvailable={Dispatcher} error={Error} (Application.Current is {AppCurrent})",
                        result.Success, dispatcherAvailable, result.Error ?? "", System.Windows.Application.Current is null ? "null" : "live");
                }
            }

            if (_args.Contains("--probe-jar-hash"))
                await LogJarFingerprintsAsync(core);

            if (_args.Contains("--probe-iframe-escape"))
                await RunIframeEscapeProbeAsync(core);

            var openCanvasConv = ArgAfter("--probe-open-canvas");
            if (openCanvasConv is not null)
                await RunOpenRealCanvasProbeAsync(core, openCanvasConv);

            var composeConv = ArgAfter("--probe-compose");
            if (composeConv is not null)
                await RunComposeProbeAsync(core, composeConv);

            if (_args.Contains("--probe-reject"))
                await RunRejectionProbesAsync(core);

            if (_args.Contains("--probe-chrome"))
                await RunChromeProbesAsync(core);

            if (_args.Contains("--probe-ime"))
                await RunImeProbeAsync(core);

            // Click a rendered consent card's button (data-testid) after a
            // delay — tests the FULL visual consent round-trip (card → button →
            // resolveConsent → bridge.respondConsent). Format "<delayMs>:<testid>".
            var consentClick = ArgAfter("--probe-consent-click");
            if (consentClick is not null)
            {
                var parts = consentClick.Split(':', 2);
                if (parts.Length == 2 && int.TryParse(parts[0], out var ccDelay))
                {
                    await Task.Delay(ccDelay);
                    var r = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                        $"(function(){{var b=document.querySelector('[data-testid=\"{parts[1]}\"]');if(b){{b.click();return 'clicked';}}return 'no-consent-button';}})();")).Task.Unwrap();
                    _logger.LogInformation("PROBE consent-click {Testid}: {Result}", parts[1], r);
                }
            }

            var reloadAfter = ArgAfter("--probe-reload");
            if (reloadAfter is not null && int.TryParse(reloadAfter, out var reloadMs))
            {
                await Task.Delay(reloadMs);
                _logger.LogWarning("PROBE reload: reloading the page mid-turn (4.11)");
                await Dispatcher.InvokeAsync(() => core.Reload());
            }

            var cancelAfter = ArgAfter("--probe-cancel");
            if (cancelAfter is not null && int.TryParse(cancelAfter, out var cancelMs))
            {
                await Task.Delay(cancelMs);
                _logger.LogWarning("PROBE cancel: clicking Stop mid-turn (4.6)");
                await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    "(function(){var bs=Array.from(document.querySelectorAll('button[aria-label]'));" +
                    "var s=bs.find(function(b){return /^(Stop generation|\\u0e2b\\u0e22\\u0e38\\u0e14)$/.test((b.getAttribute('aria-label')||'').trim());});" +
                    "if(s){s.click();return 'stopped';}return 'no-stop-button';})();"));
            }

            var shotSpec = ArgAfter("--probe-screenshot"); // "<delayMs>:<path>"
            if (shotSpec is not null)
            {
                var parts = shotSpec.Split(':', 2);
                if (parts.Length == 2 && int.TryParse(parts[0], out var shotDelay))
                {
                    await Task.Delay(shotDelay);
                    try
                    {
                        await using var fs = File.Create(parts[1]);
                        await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
                        _logger.LogInformation("PROBE screenshot: wrote {Path}", parts[1]);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogWarning(ex, "PROBE screenshot failed");
                    }
                }
            }

            var domCheck = ArgAfter("--probe-domcheck"); // delayMs — log the last assistant bubble's text length
            if (domCheck is not null && int.TryParse(domCheck, out var domDelay))
            {
                await Task.Delay(domDelay);
                var dom = await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    "(function(){var ms=Array.from(document.querySelectorAll('[data-testid=\"message\"],.message,article'));" +
                    "var txt=document.body?document.body.innerText:'';" +
                    "return JSON.stringify({bodyLen:txt.length,tail:txt.slice(-160)});})();")).Task.Unwrap();
                _logger.LogInformation("PROBE domcheck: {Dom}", SafeDiagnosticText(dom));
            }

            if (_args.Contains("--probe-rotate-now"))
            {
                _logger.LogInformation("PROBE: forcing one host rotation now");
                var outcome = await _sessionController.RefreshOnceAsync("probe");
                _logger.LogInformation("PROBE: rotation outcome = {Outcome}", outcome);
                await LogJarFingerprintsAsync(core);
                // §2.4 observation: after the rotation, make the PAGE issue a
                // request; the jar holds exactly one access cookie (logged
                // above), so the cookie that goes out is that one.
                var gatewayBase = _settings.Current.GatewayBaseUrl.TrimEnd('/');
                await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    $"fetch('{gatewayBase}/portal/me',{{credentials:'include',cache:'no-store'}})" +
                    ".then(r=>console.log('[shell-probe] post-rotation /portal/me status='+r.status))" +
                    ".catch(e=>console.log('[shell-probe] post-rotation /portal/me failed: '+e))"));
                await Task.Delay(2000);
            }

            var corruptAfter = ArgAfter("--probe-corrupt-access");
            if (corruptAfter is not null && int.TryParse(corruptAfter, out var corruptMs))
            {
                await Task.Delay(corruptMs);
                _logger.LogInformation("PROBE: corrupting the jar's access cookie (simulated server-side invalidation), then reloading");
                var gateway = new Uri(_settings.Current.GatewayBaseUrl);
                await Dispatcher.InvokeAsync(() =>
                    WriteCookie(core, "portal_access_token", $"corrupted-{Guid.NewGuid():N}", gateway));
                await LogJarFingerprintsAsync(core);
                await Dispatcher.InvokeAsync(() => core.Reload());
            }

            // Criterion 6, server-side direction: the page (not the host)
            // revokes the session at the gateway — "token revoked elsewhere".
            // The host's own machinery must then discover the loss at its next
            // rotation and land on the signed-out view without help.
            var revokeAfter = ArgAfter("--probe-server-revoke");
            if (revokeAfter is not null && int.TryParse(revokeAfter, out var revokeMs))
            {
                await Task.Delay(revokeMs);
                var gatewayBase = _settings.Current.GatewayBaseUrl.TrimEnd('/');
                _logger.LogInformation("PROBE: page-side POST /portal/auth/logout (server-side revocation of the shell's session)");
                await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    $"fetch('{gatewayBase}/portal/auth/logout',{{method:'POST',credentials:'include',headers:{{'Content-Type':'application/json'}}}})" +
                    ".then(r=>console.log('[shell-probe] revoke logout status='+r.status))" +
                    ".catch(e=>console.log('[shell-probe] revoke logout failed: '+e))"));
                // Give the revocation a moment, then reload so the PAGE is the
                // one to discover the loss (§2.5: its next request 401s and
                // tries to redirect; §2.2 catches it; the host's refresh is
                // then rejected because the token really is revoked).
                await Task.Delay(3000);
                await Dispatcher.InvokeAsync(() => core.Reload());
            }

            var signOutAfter = ArgAfter("--probe-signout");
            if (signOutAfter is not null && int.TryParse(signOutAfter, out var signOutMs))
            {
                await Task.Delay(signOutMs);
                _logger.LogInformation("PROBE: host-initiated sign-out");
                await _sessionController.SignOutAsync("probe");
            }

            // ── Phase 5 live probes (host.query control channel, §2/§4/§6) ──
            if (_args.Contains("--probe-native"))
            {
                // Restore any skill the screens probe toggled off, then open the
                // native add-custom (spawn-consent) dialog so §3.5 can be captured.
                await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    "(function(){var b=window.__SRAN_BRIDGE__;" +
                    "window.chrome.webview.postMessage({nonce:b.nonce,type:'host.query',payload:{requestId:'restore-docx',op:'skills.setEnabled',args:{skillId:'docx',enabled:true}}});})();"));
                await Task.Delay(1200);
                _logger.LogWarning("PROBE native: opening the NATIVE add-custom (spawn-consent) dialog via connectors.addCustomDialog — expect a native window, NOT a page render");
                await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
                    "(function(){var b=window.__SRAN_BRIDGE__;" +
                    "window.chrome.webview.postMessage({nonce:b.nonce,type:'host.query',payload:{requestId:'addcustom',op:'connectors.addCustomDialog',args:{}}});})();"));
            }
            var screensDir = ArgAfter("--probe-screens");
            if (screensDir is not null)
                await RunScreensProbeAsync(core, screensDir);
            var pauseDir = ArgAfter("--probe-pause-resume");
            if (pauseDir is not null)
                await RunPauseResumeProbeAsync(core, pauseDir);
            var multiDir = ArgAfter("--probe-multiturn");
            if (multiDir is not null)
                await RunMultiTurnProbeAsync(core, multiDir);
            var cancelDir = ArgAfter("--probe-cancel-continue");
            if (cancelDir is not null)
                await RunCancelContinueProbeAsync(core, cancelDir);
            if (_args.Contains("--probe-hostquery"))
                await RunHostQueryProbeAsync(core);
            if (_args.Contains("--probe-renderer-error"))
                await RunRendererErrorProbeAsync(core);
            if (_args.Contains("--probe-migrate"))
                await RunMigrateProbeAsync();

            var exitAfter = ArgAfter("--exit-after");
            if (exitAfter is not null && int.TryParse(exitAfter, out var ms))
            {
                await Task.Delay(ms);
                _logger.LogInformation("PROBE: --exit-after {Ms} ms elapsed, shutting down", ms);
                await Dispatcher.InvokeAsync(() => System.Windows.Application.Current?.Shutdown());
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "PROBE failed");
        }
#else
        await Task.CompletedTask;
#endif
    }

    private string? ArgAfter(string flag)
    {
        var i = Array.IndexOf(_args, flag);
        return i >= 0 && i + 1 < _args.Length ? _args[i + 1] : null;
    }

#if DEBUG
    /// <summary>
    /// Exit criterion 4.1 — re-run the spike's finding-6 probe against the REAL
    /// shell + real BridgeChannel. Injects a sandboxed opaque-origin srcdoc
    /// iframe matching web-chat's executable-canvas sandbox
    /// (canvasUtil.ts: CANVAS_EXEC_SANDBOX = "allow-scripts", exec CSP), whose
    /// OWN inline script proves which frame it is (location.href +
    /// body.innerHTML slice) and then calls
    /// window.chrome.webview.postMessage(...). The host must receive NOTHING —
    /// BridgeChannel registers only the top-level handler, so a child frame's
    /// message goes to the unregistered CoreWebView2Frame.WebMessageReceived
    /// and is dropped. Then a valid TOP-document message is posted to prove the
    /// inbound log records arrivals (an always-empty log is not evidence).
    ///
    /// Substitution, stated plainly (same as the spike): this reproduces the
    /// canvas SANDBOX rather than routing a model-authored canvas turn (the
    /// mock cannot emit one). The property under test — can a sandboxed
    /// opaque-origin srcdoc frame's own script reach the host — is a WebView2
    /// property this exercises faithfully.
    /// </summary>
    private async Task RunIframeEscapeProbeAsync(CoreWebView2 core)
    {
        _logger.LogWarning("PROBE iframe-escape: injecting a sandboxed canvas-equivalent iframe; the host must receive NOTHING from it");
        // First install a TOP-document listener for the child's parent.postMessage.
        // The child's own console is NOT captured for a sandboxed opaque-origin
        // srcdoc frame (its execution context is separate), so the child proves
        // it RAN by messaging its parent — which the top document (whose console
        // IS captured) logs. This closes the "did the child even execute?" gap:
        // containment is only meaningful if the child actually attempted the
        // escape. The alive-signal (window.postMessage to parent) is a DIFFERENT
        // channel from the escape (chrome.webview.postMessage to host).
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "window.addEventListener('message',function(e){try{" +
            "if(e.data&&e.data.probe==='child-alive')" +
            "console.log('[top-listener] CHILD RAN: href='+e.data.href+' body='+e.data.body);" +
            "}catch(x){}});"));

        // The child's inline script (part of the srcdoc content — the only kind
        // that runs in a sandboxed frame). Mirrors the spike: signal alive to
        // parent, then attempt the host escape.
        const string childScript =
            "try{" +
            "parent.postMessage({probe:'child-alive',href:location.href,body:(document.body?document.body.innerHTML.slice(0,60):'')},'*');" +
            "if(window.chrome&&window.chrome.webview){" +
            "window.chrome.webview.postMessage({probe:'from-iframe-child',href:location.href});" +
            "}" +
            "}catch(e){parent.postMessage({probe:'child-alive',href:'error:'+e},'*');}";
        // Build the srcdoc with the exact exec sandbox + a matching CSP, then
        // inject via ExecuteScriptAsync on the TOP document (as the spike did).
        var srcdoc = "<!doctype html><meta http-equiv=\"Content-Security-Policy\" "
            + "content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'\">"
            + "<body><h1>SPIKE-CANVAS-CHILD</h1><script>" + childScript + "<\\/script></body>";
        var srcdocJson = JsonSerializer.Serialize(srcdoc);
        var inject =
            "(function(){var f=document.createElement('iframe');" +
            "f.setAttribute('sandbox','allow-scripts');" + // CANVAS_EXEC_SANDBOX
            "f.setAttribute('referrerpolicy','no-referrer');" +
            "f.srcdoc=" + srcdocJson + ";" +
            "f.style.position='fixed';f.style.left='-9999px';f.width='300';f.height='120';" +
            "document.body.appendChild(f);" +
            "console.log('[iframe-probe] injected sandbox=allow-scripts iframe into top doc');" +
            "})();";
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(inject));
        // Give the child a beat to run + attempt its escape.
        await Task.Delay(1500);
        _logger.LogInformation("PROBE iframe-escape: child has run IF a [top-listener] CHILD RAN line appears above. Any 'BRIDGE INBOUND' carrying probe 'from-iframe-child' would be a FAILURE. Now posting a VALID top-document message to prove the channel records arrivals:");
        // Valid top-document message with the current nonce — proves the log works.
        await Dispatcher.InvokeAsync(() => core.ExecuteScriptAsync(
            "(function(){var b=window.__SRAN_BRIDGE__;" +
            "if(!b){console.log('[top-probe] no bridge handshake present');return;}" +
            "window.chrome.webview.postMessage({nonce:b.nonce,type:'ui.ready',payload:{probe:'top-doc'}});" +
            "console.log('[top-probe] posted valid ui.ready from TOP document');})();"));
        await Task.Delay(1000);
    }

    /// <summary>Names, counts and SHA-256/8 fingerprints of the session
    /// cookies currently in the jar — proves which value the jar holds (and
    /// that AddOrUpdateCookie REPLACED rather than duplicated) without a
    /// byte of either token being logged.</summary>
    private async Task LogJarFingerprintsAsync(CoreWebView2 core)
    {
        // RunDebugProbesAsync starts on the dispatcher thread and its awaits
        // resume there (WPF sync context), so WebView2 calls are direct.
        var gatewayBase = _settings.Current.GatewayBaseUrl;
        var cookies = await core.CookieManager.GetCookiesAsync(gatewayBase);
        var session = cookies.Where(c => c.Name is "portal_access_token" or "portal_refresh_token").ToList();
        foreach (var c in session)
            _logger.LogInformation("PROBE jar cookie: {Name} domain={Domain} httpOnly={HttpOnly} sameSite={SameSite} secure={Secure} sha256/8={Fp}",
                c.Name, c.Domain, c.IsHttpOnly, c.SameSite, c.IsSecure, Fingerprint(c.Value));
        _logger.LogInformation("PROBE jar summary: {AccessCount} access cookie(s), {RefreshCount} refresh cookie(s) for {Base}",
            session.Count(c => c.Name == "portal_access_token"),
            session.Count(c => c.Name == "portal_refresh_token"),
            gatewayBase);
    }
#endif

    // ------------------------------------------------------------------
    // Crash overlay / reload
    // ------------------------------------------------------------------

    private void ShowCrashOverlay(string detail)
    {
        CrashDetail.Text = detail;
        CrashOverlay.Visibility = Visibility.Visible;
    }

    private async void ReloadButton_Click(object sender, RoutedEventArgs e)
    {
        CrashOverlay.Visibility = Visibility.Collapsed;
        _logger.LogInformation("Reload requested after ProcessFailed");
        try
        {
            if (Browser.CoreWebView2 is { } core)
            {
                core.Reload();
                return;
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Reload on the existing CoreWebView2 failed — recreating the control");
        }
        // Browser process is gone: the control must be recreated.
        var parent = (System.Windows.Controls.Grid)Browser.Parent;
        var index = parent.Children.IndexOf(Browser);
        parent.Children.Remove(Browser);
        Browser.Dispose();
        var fresh = new WebView2 { Name = "Browser" };
        parent.Children.Insert(index, fresh);
        Browser = fresh;
        await InitializeWebViewAsync();
    }

    protected override void OnClosed(EventArgs e)
    {
        _sessionController.StateChanged -= OnSessionStateChanged;
        // WebView2 owns native browser resources that outlive the HWND unless
        // the control is disposed explicitly. Leaving them alive keeps this
        // WinExe process resident after Application.Shutdown().
        Browser.Dispose();
        _environment = null;
        base.OnClosed(e);
    }
}
