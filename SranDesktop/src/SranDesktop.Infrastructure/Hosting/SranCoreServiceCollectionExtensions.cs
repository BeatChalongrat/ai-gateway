using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Api;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Platform;
using SranDesktop.Infrastructure.Storage;

namespace SranDesktop.Infrastructure.Hosting;

/// <summary>
/// The presentation-free half of the desktop composition root — everything
/// both hosts (the WPF app and the WebView2 shell) share. Moved verbatim from
/// <c>SranDesktop.App/Hosting/AppHostBuilder.cs:44-359</c> (Shell Phase 1;
/// sizing in the shell's phase-0 sizing record, Q1).
///
/// What this deliberately does NOT register — each host must supply its own
/// implementation, and both hosts build with <c>ValidateOnBuild = true</c> so
/// a missing one fails at startup, not on first use:
///   * <see cref="IDialogService"/>        (WPF app: WpfDialogService; shell: ShellDialogService)
///   * <see cref="ICompletionNotifier"/>   (WPF app: TrayCompletionNotifier; shell: its own tray)
///   * <see cref="IWindowStateProvider"/>  (same instance as the notifier in both hosts)
///
/// Two further host responsibilities that are not DI registrations at all:
///   * a tool-consent handler subscribed to <see cref="IChatOrchestrator.ConsentRequested"/>
///     (unsubscribed = deny, which is the safe default);
///   * a sink for <see cref="ToastService.Changed"/> — the queue is pure and
///     four non-Presentation services raise toasts into it.
///
/// Known leak kept as-is: <see cref="ReadClipboardTool"/> / <see cref="WriteClipboardTool"/>
/// resolve <c>System.Windows.Application.Current?.Dispatcher</c> statically, so
/// they need the host to be a live WPF Application. Both hosts are.
/// </summary>
public static class SranCoreServiceCollectionExtensions
{
    public static IServiceCollection AddSranCore(this IServiceCollection services, IConfiguration configuration)
    {
        // User settings: appsettings.json supplies the defaults,
        // %APPDATA%\SRAN Desktop\settings.json overrides them.
        var configDefaults = new UserSettings
        {
            ChatEdgeBaseUrl = configuration["Sran:ChatEdgeBaseUrl"] ?? "http://localhost:4000",
            GatewayBaseUrl = configuration["Sran:GatewayBaseUrl"] ?? "http://localhost:8080",
            DefaultModel = configuration["Sran:DefaultModel"] ?? "gpt-4o-mini"
        };
        services.AddSingleton<ISettingsService>(sp => new JsonSettingsService(
            configDefaults,
            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<JsonSettingsService>>()));

        // Platform integration (IStartupManager, ICredentialStore, the
        // browser-extension installer, screen/foreground state, speech and the
        // platform tool packs) is registered by the per-OS packs — see
        // IPlatformToolProvider. Infrastructure split, 2026-08-25.

        // Toasts (Phase 8): one app-wide queue; the same instance backs
        // the static ToastService.Instance used by UI code-behind.
        services.AddSingleton<IToastService>(ToastService.Instance);
        // IDialogService is host-supplied (see class summary).

        // Storage + auth session (ICredentialStore comes from the platform pack)
        services.AddSingleton<ITokenSession, TokenSession>();
        services.AddSingleton<IConversationStore, SqliteConversationStore>();

        // Portal auth client (:8080). UseCookies=false: tokens are read
        // straight from Set-Cookie headers and refresh is body-based.
        services.AddHttpClient<IPortalAuthClient, PortalAuthClient>((sp, client) =>
            {
                client.BaseAddress = new Uri(sp.GetRequiredService<ISettingsService>().Current.GatewayBaseUrl);
                client.Timeout = TimeSpan.FromSeconds(30);
            })
            .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
            {
                UseCookies = false
            });

        // chat-edge client (:4000), Bearer-authenticated with 401 refresh-retry
        services.AddTransient<AuthTokenHandler>();
        services.AddHttpClient<IChatEdgeApiClient, ChatEdgeApiClient>((sp, client) =>
            {
                client.BaseAddress = new Uri(sp.GetRequiredService<ISettingsService>().Current.ChatEdgeBaseUrl);
                // Streaming turns can run for minutes; HttpClient.Timeout
                // would abort the SSE read mid-stream. Cancellation is
                // per-call via CancellationTokens instead.
                client.Timeout = Timeout.InfiniteTimeSpan;
            })
            .AddHttpMessageHandler<AuthTokenHandler>();

        // Agent Mode — key provisioning goes through portal routes on
        // the gateway (portal-JWT auth via AuthTokenHandler)…
        services.AddHttpClient<IAgentKeyProvider, AgentKeyProvider>((sp, client) =>
            {
                client.BaseAddress = new Uri(sp.GetRequiredService<ISettingsService>().Current.GatewayBaseUrl);
                client.Timeout = TimeSpan.FromSeconds(30);
            })
            .AddHttpMessageHandler<AuthTokenHandler>();

        // Connector OAuth broker client (Phase 7 C3) — same portal-JWT
        // wiring as AgentKeyProvider; only tickets / connection state
        // ever travel here, never provider tokens.
        services.AddHttpClient<IConnectorAuthClient, ConnectorAuthClient>((sp, client) =>
            {
                client.BaseAddress = new Uri(sp.GetRequiredService<ISettingsService>().Current.GatewayBaseUrl);
                client.Timeout = TimeSpan.FromSeconds(30);
            })
            .AddHttpMessageHandler<AuthTokenHandler>();
        services.AddSingleton<IBrowserLauncher, SystemBrowserLauncher>();
        services.AddSingleton<ConnectorOAuthCoordinator>();
        // SSO Phase 2: Google sign-in via system browser + one-time
        // ticket deep link (nonce-bound; see LoginOAuthCoordinator).
        services.AddSingleton<LoginOAuthCoordinator>();

        // …while completions use the provisioned gw_ API key directly
        // (the /v1 endpoint rejects portal JWTs).
        services.AddHttpClient<IGatewayApiClient, GatewayApiClient>((sp, client) =>
            {
                client.BaseAddress = new Uri(sp.GetRequiredService<ISettingsService>().Current.GatewayBaseUrl);
                client.Timeout = Timeout.InfiniteTimeSpan; // streaming
            });

        // web_fetch named client: 30s timeout; auto-redirect OFF so the
        // tool can SSRF-re-check every hop before following it.
        services.AddHttpClient(WebFetchTool.HttpClientName, client =>
            {
                client.Timeout = TimeSpan.FromSeconds(30);
            })
            .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
            {
                AllowAutoRedirect = false,
                AutomaticDecompression = System.Net.DecompressionMethods.All
            });

        // Browser bridge (Chrome extension): pipe server + pairing;
        // Start() is called at app startup so the relay can connect
        // whenever Chrome launches it.
        services.AddSingleton<IBrowserBridgeService>(sp =>
            new SranDesktop.Infrastructure.Services.BrowserBridgeService(
                sp.GetRequiredService<ICredentialStore>(),
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                    SranDesktop.Infrastructure.Services.BrowserBridgeService>>()));
        // IBrowserExtensionInstaller, ScreenContext, IForegroundTargetTracker and
        // UiaSnapshotStore are registered by the platform packs (per-OS cluster).

        // Per-conversation shell state (PART 3): retained working dir.
        services.AddSingleton<ShellSession>();
        // Folder-scoped consent (PART 4): writes → project folder, shell → cwd.
        services.AddSingleton<IToolScopeResolver>(sp =>
            new FolderScopeResolver(sp.GetRequiredService<ShellSession>()));

        // MCP tools
        services.AddSingleton(sp =>
        {
            var registry = new McpToolRegistry();
            registry.Register(new ReadFileTool());
            // Local document text extraction (PDF/Word/Excel/PowerPoint).
            // Registered right after read_file so the model meets the
            // document-capable reader before it settles for the base64
            // one. Same PathGuard + SensitiveReadGuard gates.
            // Scanned PDFs go to chat-edge's OCR; settings + toasts are
            // only there for the one-time "this is read on the server"
            // notice, not to gate anything.
            registry.Register(new ReadDocumentTool(
                sp.GetRequiredService<IScannedDocumentOcr>(),
                sp.GetRequiredService<ISettingsService>(),
                sp.GetRequiredService<IToastService>()));
            registry.Register(new WriteFileTool());
            // Phase A PART 2: targeted edit + filesystem primitives a real
            // agent needs (all PathGuard-confined + consent-gated).
            registry.Register(new EditFileTool());
            registry.Register(new CreateDirectoryTool());
            registry.Register(new MoveFileTool());
            registry.Register(new ListDirectoryTool());
            registry.Register(new SearchFilesTool());
            // Platform slot: read_clipboard / write_clipboard (WindowsDesktop pack).
            RegisterPlatformTools(registry, sp, PlatformToolSlot.Clipboard);
            // Phase B PART 1: streams stdout/stderr live via the default
            // SystemProcessRunner (tests inject a fake IProcessRunner).
            registry.Register(new RunCommandTool(
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<RunCommandTool>>(),
                sp.GetRequiredService<ISettingsService>(),
                sp.GetRequiredService<ShellSession>()));
            // Phase A PART 5: model-maintained plan / todo (side-effect
            // free → AllowAlways; renders as a live checklist).
            registry.Register(new UpdatePlanTool());
            // Phase B PART 3: cross-session agent memory notes (durable,
            // conversation-scoped; reads the ambient conversation id).
            registry.Register(new RememberTool(
                sp.GetRequiredService<IConversationStore>(),
                sp.GetRequiredService<ShellSession>()));
            // Web search proxies to the gateway's /v1/search (R9 C);
            // only listed while the composer toggle is on.
            registry.Register(new WebSearchTool(
                sp.GetRequiredService<IGatewayApiClient>(),
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<WebSearchTool>>()));
            // web_fetch reads page content after web_search (read-only,
            // SSRF-guarded). ALWAYS listed — unlike web_search it is
            // not gated by the composer web-search toggle.
            registry.Register(new WebFetchTool(
                sp.GetRequiredService<IHttpClientFactory>(),
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<WebFetchTool>>()));
            // Model-driven skill loading (Phase 6 Task 2) — reads
            // bundled SKILL.md on demand; no side effects (AllowAlways).
            registry.Register(new UseSkillTool(sp.GetRequiredService<ISkillManager>()));
            // Platform slots: computer use (screenshot → look → act → verify;
            // Windows pack) then UI Automation (WindowsDesktop pack) — the same
            // tools in the same order the inline registrations had.
            RegisterPlatformTools(registry, sp, PlatformToolSlot.ComputerUse);
            RegisterPlatformTools(registry, sp, PlatformToolSlot.UiAutomation);
            // Browser tools (Chrome extension bridge): DOM-level
            // control; all fail softly while the extension is absent.
            var browserBridge = sp.GetRequiredService<IBrowserBridgeService>();
            registry.Register(new BrowserListTabsTool(browserBridge));
            registry.Register(new BrowserTabsTool(browserBridge));
            registry.Register(new BrowserNavigateTool(browserBridge));
            registry.Register(new BrowserReadPageTool(browserBridge));
            registry.Register(new BrowserFindTool(browserBridge));
            registry.Register(new BrowserClickTool(browserBridge));
            registry.Register(new BrowserTypeTool(browserBridge));
            registry.Register(new BrowserScreenshotTool(browserBridge));
            // open_url AFTER the browser_* tools (the model reads the list
            // in order) and bridge-aware: while the extension is connected
            // it opens a Chrome tab, never the OS default browser.
            registry.Register(new OpenUrlTool(
                browserBridge, sp.GetRequiredService<IBrowserLauncher>()));
            // Phase 13 Part B: code interpreter. Thin client → chat-edge's
            // /code/run (server-side hardened sandbox). DefaultPermission
            // is Ask (deny-without-handler): it runs model code + can read
            // conversation files, but never touches the real machine.
            registry.Register(new RunPythonTool(
                sp.GetRequiredService<IChatEdgeApiClient>()));
            // Phase 12 F: sub-agent delegation. DefaultPermission is
            // AllowAlways because the tool only delegates — every tool
            // the nested run calls still goes through the shared
            // PermissionService + the parent turn's consent handler.
            // The tool host / orchestrator accessors are lazy Funcs to
            // break the construction cycle (registry → this tool →
            // orchestrator → tool host → registry); they resolve only
            // at execution time, when everything is built.
            registry.Register(new SpawnAgentTool(
                sp.GetRequiredService<IGatewayApiClient>(),
                sp.GetRequiredService<IPermissionService>(),
                sp.GetRequiredService<IConversationStore>(),
                () => sp.GetRequiredService<IMcpToolHost>(),
                () => sp.GetRequiredService<IChatOrchestrator>(),
                sp.GetRequiredService<ISettingsService>(),
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILoggerFactory>(),
                sp.GetRequiredService<ShellSession>(),
                sp.GetRequiredService<IToolScopeResolver>()));
            return registry;
        });
        services.AddSingleton<IMcpToolHost>(sp => new McpToolHost(
            sp.GetRequiredService<McpToolRegistry>(),
            sp.GetRequiredService<IMcpConnectorService>()));
        services.AddSingleton<IPermissionService, PermissionService>();
        // FIX-F: out-of-scope reads (outside working/granted folders) ask first.
        services.AddSingleton<ISensitiveReadGuard, SensitiveReadGuard>();
        // Explicit factory: the ctor's optional cap parameters (used by
        // run_subagent's nested instance) must stay at their defaults
        // for the app-wide singleton.
        services.AddSingleton<IChatOrchestrator>(sp => new ChatOrchestrator(
            sp.GetRequiredService<IGatewayApiClient>(),
            sp.GetRequiredService<IMcpToolHost>(),
            sp.GetRequiredService<IPermissionService>(),
            sp.GetRequiredService<IConversationStore>(),
            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<ChatOrchestrator>>(),
            shellSession: sp.GetRequiredService<ShellSession>(),
            scopeResolver: sp.GetRequiredService<IToolScopeResolver>(),
            settings: sp.GetRequiredService<ISettingsService>(),
            readGuard: sp.GetRequiredService<ISensitiveReadGuard>()));

        // Shared agent-turn prompt assembler (SHELL_TURN_PARITY_REPORT.md §2):
        // the single source of truth for a turn's system prompt + extras, so the
        // WPF app and the WebView2 shell build identical prompts. Singleton — its
        // only state is a 60s server-memory cache, which is global_user-scoped and
        // correctly shared across conversations.
        services.AddSingleton<AgentTurnAssembler>();

        // Application services
        services.AddSingleton<IAuthService, AuthService>();
        services.AddSingleton<IChatService, ChatService>();
        services.AddSingleton<IFileService, FileService>();
        // Scanned PDFs have no text layer, so read_document falls back
        // to chat-edge's OCR (Thai + English) — the same pipeline the
        // web client uses. Consent for the upload lives in the tool.
        // Explicit factory: the ctor's poll-interval / timeout
        // parameters are optional (tests shrink them) and must keep
        // their production defaults here.
        services.AddSingleton<IScannedDocumentOcr>(sp => new ScannedDocumentOcrService(
            sp.GetRequiredService<IFileService>(),
            sp.GetRequiredService<IChatEdgeApiClient>(),
            sp.GetRequiredService<IConversationStore>(),
            sp.GetRequiredService<ShellSession>(),
            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<ScannedDocumentOcrService>>()));
        services.AddSingleton<IConversationSyncService, ConversationSyncService>();
        // Phase 3 D: one-way push backup of agent-mode conversations.
        // Shared between ChatViewModel (marks a conversation busy
        // while its uploads/turn run) and ConversationListViewModel
        // (refuses to prune a busy conversation). Singleton: both
        // sides must see the same counts.
        services.AddSingleton<SranDesktop.Application.Services.IConversationActivityTracker,
            SranDesktop.Application.Services.ConversationActivityTracker>();
        services.AddSingleton<IAgentConversationSyncService,
            SranDesktop.Infrastructure.Services.AgentConversationSyncService>();

        // R9: projects (local-first), scheduler, skills, connectors
        services.AddSingleton<IProjectStore, SranDesktop.Infrastructure.Storage.JsonProjectStore>();
        services.AddSingleton<IScheduledTaskStore, SranDesktop.Infrastructure.Storage.SqliteScheduledTaskStore>();
        services.AddSingleton<ITaskSchedulerService, SranDesktop.Infrastructure.Services.TaskSchedulerService>();
        services.AddSingleton<ISkillManager>(sp => new SranDesktop.Infrastructure.Services.SkillManager(
            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.SkillManager>>()));
        // ISpeechService (R10 Fix 2 composite) is registered by the platform pack.
        // Phase 12 E / 16 B: on-device Office plan executors (opt-in via
        // Settings → PreferLocalOfficeGeneration). Charts and edit-mode
        // plans still fall back to chat-edge per executor's CanExecute.
        services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalExcelPlanExecutor>();
        services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalDocPlanExecutor>();
        services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalPptxPlanExecutor>();
        // R11 A1: auto-update (manifest URL is deploy-time config).
        services.AddSingleton<IUpdateService>(sp =>
            new SranDesktop.Infrastructure.Services.UpdateService(
                configuration["Sran:UpdateManifestUrl"],
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.UpdateService>>()));
        // R11 A3: opt-in crash telemetry.
        services.AddSingleton<ICrashReportService>(sp =>
            new SranDesktop.Infrastructure.Services.CrashReportService(
                sp.GetRequiredService<ISettingsService>(),
                configuration["Sran:CrashReportUrl"],
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.CrashReportService>>()));
        // Catalog URL is deploy-time config (R10 1D): set Sran:McpCatalogUrl
        // in appsettings.json to a hosted catalog; embedded otherwise.
        services.AddSingleton<IMcpConnectorService>(sp =>
            new SranDesktop.Infrastructure.Services.McpConnectorService(
                sp.GetRequiredService<ICredentialStore>(),
                sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.McpConnectorService>>(),
                configuration["Sran:McpCatalogUrl"],
                sp.GetRequiredService<ITokenSession>()));

        return services;
    }

    /// <summary>Asks every registered platform pack for its tools at one slot,
    /// in registration order, so the model-visible tool order is exactly what
    /// the inline registrations produced before the split.</summary>
    private static void RegisterPlatformTools(McpToolRegistry registry, IServiceProvider sp, PlatformToolSlot slot)
    {
        foreach (var provider in sp.GetServices<IPlatformToolProvider>())
            foreach (var tool in provider.CreateTools(slot))
                registry.Register(tool);
    }
}
