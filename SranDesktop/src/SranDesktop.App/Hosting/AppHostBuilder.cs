using System.IO;
using System.Net.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Api;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Platform;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using SranDesktop.Presentation.Views;

namespace SranDesktop.App.Hosting;

public static class AppHostBuilder
{
    public static IHost Build(string[] args)
    {
        return Host.CreateDefaultBuilder(args)
            .UseContentRoot(AppContext.BaseDirectory)
            .UseSerilog((context, loggerConfiguration) =>
            {
                var logDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "SRAN Desktop", "logs");
                Directory.CreateDirectory(logDir);

                loggerConfiguration
                    .MinimumLevel.Debug()
                    .WriteTo.File(
                        Path.Combine(logDir, "app-.log"),
                        rollingInterval: RollingInterval.Day,
                        retainedFileCountLimit: 7);
            })
            .ConfigureServices((context, services) =>
            {
                // User settings: appsettings.json supplies the defaults,
                // %APPDATA%\SRAN Desktop\settings.json overrides them.
                var configDefaults = new UserSettings
                {
                    ChatEdgeBaseUrl = context.Configuration["Sran:ChatEdgeBaseUrl"] ?? "http://localhost:4000",
                    GatewayBaseUrl = context.Configuration["Sran:GatewayBaseUrl"] ?? "http://localhost:8080",
                    DefaultModel = context.Configuration["Sran:DefaultModel"] ?? "gpt-4o-mini"
                };
                services.AddSingleton<ISettingsService>(sp => new JsonSettingsService(
                    configDefaults,
                    sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<JsonSettingsService>>()));

                // Platform integration
                services.AddSingleton<IStartupManager, WindowsStartupManager>();

                // Toasts (Phase 8): one app-wide queue; the same instance backs
                // the static ToastService.Instance used by UI code-behind.
                services.AddSingleton<IToastService>(ToastService.Instance);
                // Themed confirm/error dialogs (Phase C.5) — VMs depend on the
                // interface, not System.Windows.MessageBox.
                services.AddSingleton<IDialogService,
                    SranDesktop.Presentation.Services.WpfDialogService>();

                // Storage + auth session
                services.AddSingleton<ICredentialStore, DpapiCredentialStore>();
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
                services.AddSingleton<IBrowserExtensionInstaller,
                    SranDesktop.Infrastructure.Services.BrowserExtensionInstaller>();

                // Computer-use screenshot→screen coordinate mapping (accuracy
                // overhaul): screenshot writes it, click/scroll/drag/zoom read it.
                services.AddSingleton<SranDesktop.Infrastructure.Native.ScreenContext>();
                // Consent-flow foreground restore (self-interference fix): the
                // consent dialog steals focus from the agent's target window.
                services.AddSingleton<IForegroundTargetTracker,
                    SranDesktop.Infrastructure.Native.ForegroundTargetTracker>();
                // UIA element snapshot shared by read_screen_elements → uia_click/uia_set_value.
                services.AddSingleton<SranDesktop.Infrastructure.Native.Uia.UiaSnapshotStore>();

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
                    registry.Register(new ReadDocumentTool());
                    registry.Register(new WriteFileTool());
                    // Phase A PART 2: targeted edit + filesystem primitives a real
                    // agent needs (all PathGuard-confined + consent-gated).
                    registry.Register(new EditFileTool());
                    registry.Register(new CreateDirectoryTool());
                    registry.Register(new MoveFileTool());
                    registry.Register(new ListDirectoryTool());
                    registry.Register(new SearchFilesTool());
                    registry.Register(new ReadClipboardTool());
                    registry.Register(new WriteClipboardTool());
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
                    // Computer use (Claude-style: screenshot → look → act → verify;
                    // accuracy overhaul: coordinates auto-map through ScreenContext)
                    var screenContext = sp.GetRequiredService<SranDesktop.Infrastructure.Native.ScreenContext>();
                    var foregroundTracker = sp.GetRequiredService<IForegroundTargetTracker>();
                    registry.Register(new ComputerScreenshotTool(screenContext, foregroundTracker));
                    registry.Register(new ComputerClickTool(screenContext, foregroundTracker));
                    registry.Register(new ComputerTypeTool());
                    registry.Register(new ComputerKeyTool());
                    registry.Register(new ComputerScrollTool(screenContext));
                    registry.Register(new ComputerDragTool(screenContext));
                    registry.Register(new ComputerZoomTool(screenContext));
                    registry.Register(new OpenApplicationTool());
                    // Windows UI Automation layer: element-level control, far
                    // more accurate than pixel clicking where apps expose UIA.
                    var uiaSnapshots = sp.GetRequiredService<SranDesktop.Infrastructure.Native.Uia.UiaSnapshotStore>();
                    registry.Register(new GetComputerStateTool());
                    registry.Register(new ReadScreenElementsTool(uiaSnapshots));
                    registry.Register(new UiaClickTool(uiaSnapshots));
                    registry.Register(new UiaSetValueTool(uiaSnapshots));
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

                // Application services
                services.AddSingleton<IAuthService, AuthService>();
                services.AddSingleton<IChatService, ChatService>();
                services.AddSingleton<IFileService, FileService>();
                services.AddSingleton<IConversationSyncService, ConversationSyncService>();
                // Phase 3 D: one-way push backup of agent-mode conversations.
                services.AddSingleton<IAgentConversationSyncService,
                    SranDesktop.Infrastructure.Services.AgentConversationSyncService>();

                // R9: projects (local-first), scheduler, skills, connectors
                services.AddSingleton<IProjectStore, SranDesktop.Infrastructure.Storage.JsonProjectStore>();
                services.AddSingleton<IScheduledTaskStore, SranDesktop.Infrastructure.Storage.SqliteScheduledTaskStore>();
                services.AddSingleton<ITaskSchedulerService, SranDesktop.Infrastructure.Services.TaskSchedulerService>();
                services.AddSingleton<ISkillManager>(sp => new SranDesktop.Infrastructure.Services.SkillManager(
                    sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.SkillManager>>()));
                // R10 Fix 2: composite speech — Azure (Thai) when a key is stored, Windows otherwise.
                services.AddSingleton<ISpeechService, SranDesktop.Infrastructure.Speech.CompositeSpeechService>();
                // Phase 12 E / 16 B: on-device Office plan executors (opt-in via
                // Settings → PreferLocalOfficeGeneration). Charts and edit-mode
                // plans still fall back to chat-edge per executor's CanExecute.
                services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalExcelPlanExecutor>();
                services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalDocPlanExecutor>();
                services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalPptxPlanExecutor>();
                // R11 A1: auto-update (manifest URL is deploy-time config).
                services.AddSingleton<IUpdateService>(sp =>
                    new SranDesktop.Infrastructure.Services.UpdateService(
                        context.Configuration["Sran:UpdateManifestUrl"],
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.UpdateService>>()));
                // R11 A3: opt-in crash telemetry.
                services.AddSingleton<ICrashReportService>(sp =>
                    new SranDesktop.Infrastructure.Services.CrashReportService(
                        sp.GetRequiredService<ISettingsService>(),
                        context.Configuration["Sran:CrashReportUrl"],
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.CrashReportService>>()));
                // Catalog URL is deploy-time config (R10 1D): set Sran:McpCatalogUrl
                // in appsettings.json to a hosted catalog; embedded otherwise.
                services.AddSingleton<IMcpConnectorService>(sp =>
                    new SranDesktop.Infrastructure.Services.McpConnectorService(
                        sp.GetRequiredService<ICredentialStore>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.McpConnectorService>>(),
                        context.Configuration["Sran:McpCatalogUrl"],
                        sp.GetRequiredService<ITokenSession>()));

                // ViewModels
                services.AddSingleton<LoginViewModel>();
                services.AddSingleton<ConversationListViewModel>();
                services.AddSingleton<ChatViewModel>();
                services.AddSingleton<MainViewModel>();
                services.AddTransient<SettingsViewModel>();
                services.AddTransient<KnowledgeBaseViewModel>();
                services.AddTransient<MemoryViewModel>();
                services.AddTransient<ScheduledTasksViewModel>();
                services.AddTransient<SkillsViewModel>();
                services.AddTransient<ConnectorMarketplaceViewModel>();
                services.AddTransient<WritingStyleViewModel>();

                // Views
                services.AddTransient<SettingsView>();
                services.AddSingleton<Func<SettingsView>>(sp => () => sp.GetRequiredService<SettingsView>());
                services.AddTransient<KnowledgeBaseView>();
                services.AddSingleton<Func<KnowledgeBaseView>>(sp => () => sp.GetRequiredService<KnowledgeBaseView>());
                services.AddTransient<MemoryView>();
                services.AddSingleton<Func<MemoryView>>(sp => () => sp.GetRequiredService<MemoryView>());
                services.AddTransient<ScheduledTasksView>();
                services.AddSingleton<Func<ScheduledTasksView>>(sp => () => sp.GetRequiredService<ScheduledTasksView>());
                services.AddTransient<SkillsView>();
                services.AddSingleton<Func<SkillsView>>(sp => () => sp.GetRequiredService<SkillsView>());
                services.AddTransient<ConnectorMarketplaceView>();
                services.AddSingleton<Func<ConnectorMarketplaceView>>(sp => () => sp.GetRequiredService<ConnectorMarketplaceView>());
                services.AddTransient<WritingStyleView>();
                services.AddSingleton<Func<WritingStyleView>>(sp => () => sp.GetRequiredService<WritingStyleView>());
                services.AddTransient<SranDesktop.Presentation.Views.Dialogs.VoiceSettingsDialog>();
                services.AddSingleton<Func<SranDesktop.Presentation.Views.Dialogs.VoiceSettingsDialog>>(
                    sp => () => sp.GetRequiredService<SranDesktop.Presentation.Views.Dialogs.VoiceSettingsDialog>());
                services.AddSingleton<MainWindow>();
            })
            .Build();
    }
}
