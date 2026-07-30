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
using SranDesktop.Infrastructure.Workers;
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
                        new RedactingTextFormatter(),
                        Path.Combine(logDir, "app-.log"),
                        rollingInterval: RollingInterval.Day,
                        retainedFileCountLimit: 7);
            })
            .ConfigureServices((context, services) =>
            {
                var computerUseOptions = context.Configuration
                    .GetSection("ComputerUse:Actions")
                    .Get<SranDesktop.Infrastructure.Native.ComputerUseOptions>()
                    ?? new SranDesktop.Infrastructure.Native.ComputerUseOptions();
                var targetResolverOptions = context.Configuration
                    .GetSection("ComputerUse:TargetResolver")
                    .Get<SranDesktop.Infrastructure.Native.TargetResolverOptions>()
                    ?? new SranDesktop.Infrastructure.Native.TargetResolverOptions();
                var screenSecurityOptions = context.Configuration
                    .GetSection("ComputerUse:ScreenSecurity")
                    .Get<SranDesktop.Infrastructure.Native.ScreenCaptureSecurityOptions>()
                    ?? new SranDesktop.Infrastructure.Native.ScreenCaptureSecurityOptions();
                services.AddSingleton(computerUseOptions);
                services.AddSingleton(targetResolverOptions);
                services.AddSingleton(screenSecurityOptions);
                services.AddSingleton(context.Configuration
                    .GetSection("RuntimeStabilization")
                    .Get<RuntimeStabilizationOptions>()
                    ?? new RuntimeStabilizationOptions());
                services.AddSingleton<SranDesktop.Infrastructure.Native.ComputerUseMetrics>();
                services.AddSingleton<SranDesktop.Infrastructure.Native.ApplicationReadinessService>();

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
                // Sran:DataDirectory redirects the app's own databases. It is a
                // deployment option (a portable install, or a redirected profile),
                // and it is also what lets an end-to-end test drive THIS
                // composition without writing into the user's real data.
                var dataDirectory = context.Configuration["Sran:DataDirectory"];
                services.AddSingleton<IConversationStore>(_ =>
                    dataDirectory is { Length: > 0 }
                        ? new SqliteConversationStore(Path.Combine(dataDirectory, "data.db"))
                        : new SqliteConversationStore());
                services.AddSingleton<IAgentTaskStore>(_ =>
                    dataDirectory is { Length: > 0 }
                        ? new SranDesktop.Infrastructure.Storage.SqliteAgentTaskStore(
                            Path.Combine(dataDirectory, "data.db"))
                        : new SranDesktop.Infrastructure.Storage.SqliteAgentTaskStore());
                services.AddSingleton<GoalInterpreter>();
                services.AddSingleton<OutcomeCompiler>();
                services.AddSingleton<PlanCoverageValidator>();
                services.AddSingleton<SemanticOutcomeMatcher>();
                services.AddSingleton<GoalRelevanceGate>();
                services.AddSingleton<CapabilityRouter>();
                services.AddSingleton<RealWorldSkillRuntime>();
                services.AddSingleton<ISkillRegistry>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<ISkillResolver>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<ICapabilityInventory>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<IEnvironmentObserver>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<ISkillVerifier>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<ISkillExecutor>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<ICompensationCoordinator>(sp =>
                    sp.GetRequiredService<RealWorldSkillRuntime>());
                services.AddSingleton<IDurableTaskRuntime, DurableTaskRuntime>();
                services.AddTransient<MockLongHorizonHarness>();
                services.AddTransient<RealRuntimeIntegrationRunner>();

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
                services.AddSingleton<SranDesktop.Infrastructure.Native.IScreenCaptureSecurityPolicy>(
                    sp => new SranDesktop.Infrastructure.Native.ScreenCaptureSecurityPolicy(
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.ScreenCaptureSecurityOptions>()));
                services.AddSingleton<SranDesktop.Infrastructure.Native.IScreenImageRedactor,
                    SranDesktop.Infrastructure.Native.SolidScreenImageRedactor>();
                services.AddSingleton<SranDesktop.Infrastructure.Native.IScreenCaptureIndicator,
                    SranDesktop.Infrastructure.Native.ScreenCaptureIndicator>();
                services.AddSingleton<SranDesktop.Infrastructure.Native.Uia.ITargetAcquisitionRuntime>(sp =>
                    new SranDesktop.Infrastructure.Native.Uia.WindowsTargetAcquisitionRuntime(
                        options: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.TargetResolverOptions>(),
                        browser: sp.GetRequiredService<IBrowserBridgeService>()));
                // Consent-flow foreground restore (self-interference fix): the
                // consent dialog steals focus from the agent's target window.
                services.AddSingleton<IForegroundTargetTracker,
                    SranDesktop.Infrastructure.Native.ForegroundTargetTracker>();
                // UIA element snapshot shared by read_screen_elements → uia_click/uia_set_value.
                services.AddSingleton<SranDesktop.Infrastructure.Native.Uia.UiaSnapshotStore>();
                services.AddSingleton<
                    SranDesktop.Infrastructure.Native.Perception.WindowsOcrService>();
                services.AddSingleton<
                    SranDesktop.Infrastructure.Native.Perception.OcrSnapshotStore>();
                services.AddSingleton<
                    SranDesktop.Infrastructure.Native.Perception.VisualSnapshotStore>();

                // Per-conversation shell state (PART 3): retained working dir.
                services.AddSingleton<ShellSession>();
                // Folder-scoped consent (PART 4): writes → project folder, shell → cwd.
                services.AddSingleton<IToolScopeResolver>(sp =>
                    new FolderScopeResolver(sp.GetRequiredService<ShellSession>()));

                // Isolated workers (Phase 4). Document work runs out of process;
                // if the worker image is missing the capability fails closed
                // rather than falling back into this privileged process.
                services.AddSingleton<IWorkerImageResolver>(
                    _ => new WorkerImageResolver());
                services.AddSingleton<IIsolatedWorkerHost>(sp => new IsolatedWorkerHost(
                    sp.GetRequiredService<IWorkerImageResolver>(),
                    logger: sp.GetRequiredService<
                        Microsoft.Extensions.Logging.ILogger<IsolatedWorkerHost>>()));
                services.AddSingleton(sp => new DocumentWorkerService(
                    sp.GetRequiredService<IIsolatedWorkerHost>(),
                    WorkspaceFileBroker.Shared));
                services.AddSingleton<IIsolatedPlanExecutor>(sp => new IsolatedPlanExecutor(
                    sp.GetRequiredService<IIsolatedWorkerHost>(),
                    sp.GetRequiredService<IWorkerImageResolver>()));

                // MCP tools
                services.AddSingleton(sp =>
                {
                    var registry = new McpToolRegistry();
                    registry.Register(new ReadFileTool());
                    registry.Register(new WriteFileTool());
                    // Phase A PART 2: targeted edit + filesystem primitives a real
                    // agent needs (all PathGuard-confined + consent-gated).
                    registry.Register(new EditFileTool());
                    registry.Register(new CreateDirectoryTool());
                    registry.Register(new MoveFileTool());
                    registry.Register(new ListDirectoryTool());
                    registry.Register(new SearchFilesTool());
                    // Documents always go through the isolated worker.
                    registry.Register(new DocumentTool(
                        sp.GetRequiredService<DocumentWorkerService>()));
                    // Shows the user a link in their default browser. Reports
                    // "dispatched", never "verified" — browser_navigate is the
                    // tool for driving a page and checking the outcome (P3-5).
                    registry.Register(new OpenUrlTool(
                        sp.GetRequiredService<IBrowserLauncher>()));
                    registry.Register(new ReadClipboardTool());
                    registry.Register(new WriteClipboardTool());
                    // Streams stdout/stderr. Two lanes: the AppContainer sandbox
                    // (default) and, when the user has opted in via
                    // shell_execution_mode, the real host. The lane is chosen from
                    // settings before dispatch — a sandbox failure never falls
                    // through to the host. Tests may inject IProcessRunner.
                    registry.Register(new RunCommandTool(
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<RunCommandTool>>(),
                        sp.GetRequiredService<ISettingsService>(),
                        sp.GetRequiredService<ShellSession>(),
                        processRunner: null,
                        securityEvents: sp.GetService<ISecurityEventLogger>()));
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
                    // accuracy overhaul: coordinates auto-map through ScreenContext).
                    // STRUCTURED-FIRST ordering (Phase 0, UIA-first plan): the observation
                    // and Windows UI Automation tools are registered — and therefore listed
                    // to the model (see McpToolHost.GetToolDefinitions, which preserves
                    // registration order) — BEFORE the coordinate computer_* tools, so the
                    // model reaches for element-level, foreground-free activation first and
                    // treats pixel coordinates as the last resort.
                    var screenContext = sp.GetRequiredService<SranDesktop.Infrastructure.Native.ScreenContext>();
                    var foregroundTracker = sp.GetRequiredService<IForegroundTargetTracker>();
                    var readiness = sp.GetRequiredService<
                        SranDesktop.Infrastructure.Native.ApplicationReadinessService>();
                    registry.Register(new ComputerScreenshotTool(
                        screenContext,
                        foregroundTracker,
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenCaptureSecurityPolicy>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenImageRedactor>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenCaptureIndicator>(),
                        sp.GetRequiredService<ISecurityEventLogger>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Perception.VisualSnapshotStore>()));
                    registry.Register(new ComputerOcrTool(
                        screenContext,
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Perception.WindowsOcrService>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Perception.OcrSnapshotStore>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenCaptureSecurityPolicy>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenImageRedactor>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.IScreenCaptureIndicator>()));
                    // Windows UI Automation layer: element-level control, far more accurate
                    // than pixel clicking where apps expose UIA — listed ahead of computer_*.
                    var uiaSnapshots = sp.GetRequiredService<SranDesktop.Infrastructure.Native.Uia.UiaSnapshotStore>();
                    registry.Register(new GetComputerStateTool());
                    registry.Register(new ReadScreenElementsTool(uiaSnapshots));
                    registry.Register(new UiaClickTool(uiaSnapshots));
                    registry.Register(new UiaSetValueTool(uiaSnapshots));
                    // Coordinate computer-use tools (last-resort activation; the semantic
                    // target_description path is still UIA-first inside ComputerClickTool).
                    registry.Register(new ComputerClickTool(
                        screenContext,
                        foregroundTracker,
                        options: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.ComputerUseOptions>(),
                        targets: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Uia.ITargetAcquisitionRuntime>(),
                        metrics: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.ComputerUseMetrics>(),
                        ocrSnapshots: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Perception.OcrSnapshotStore>(),
                        visualSnapshots: sp.GetRequiredService<
                            SranDesktop.Infrastructure.Native.Perception.VisualSnapshotStore>()));
                    registry.Register(new ComputerTypeTool(screenContext, readiness));
                    registry.Register(new ComputerKeyTool(screenContext, readiness));
                    registry.Register(new ComputerScrollTool(
                        screenContext,
                        sp.GetRequiredService<SranDesktop.Infrastructure.Native.ApplicationReadinessService>()));
                    registry.Register(new ComputerDragTool(
                        screenContext,
                        sp.GetRequiredService<SranDesktop.Infrastructure.Native.ApplicationReadinessService>()));
                    registry.Register(new ComputerZoomTool(screenContext));
                    registry.Register(new OpenApplicationTool(readiness));
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
                    registry.Register(new BrowserUploadTool(browserBridge));
                    registry.Register(new BrowserDownloadTool(browserBridge));
                    registry.Register(new BrowserScreenshotTool(browserBridge));
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
                        sp.GetRequiredService<IToolScopeResolver>(),
                        sp.GetRequiredService<IDurableTaskRuntime>(),
                        sp.GetRequiredService<ISecurityEventLogger>()));
                    sp.GetRequiredService<ICapabilityInventory>().Synchronize(
                        registry.All.Select(tool => (
                            tool.Name,
                            tool.Name is "run_command" or "write_file" or "move_file"
                                or "computer_click" or "computer_type" or "computer_key"
                                or "computer_scroll" or "computer_drag" or "open_application"
                                or "uia_click" or "uia_set_value" or "read_screen_elements"
                                or "browser_read_page" or "browser_click" or "browser_type")));
                    return registry;
                });
                services.AddSingleton<IMcpToolHost>(sp => new McpToolHost(
                    sp.GetRequiredService<McpToolRegistry>(),
                    sp.GetRequiredService<IMcpConnectorService>()));
                services.AddSingleton<IPermissionService, PermissionService>();
                services.AddSingleton<ISecurityEventLogger,
                    SranDesktop.Infrastructure.Services.SecurityEventLogger>();
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
                    durableRuntime: sp.GetRequiredService<IDurableTaskRuntime>(),
                    securityEvents: sp.GetRequiredService<ISecurityEventLogger>()));

                // Application services
                services.AddSingleton<IAuthService, AuthService>();
                services.AddSingleton<IChatService, ChatService>();
                services.AddSingleton<IDurableMediaTurnService, DurableMediaTurnService>();
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
                // Phase 1 containment: do not register model-authored Office
                // plan interpreters in the privileged desktop process. The UI
                // uses the chat-edge sandbox and fails closed when unavailable.
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
                // MCP connectors. The durable gateway is the only way in — for the
                // marketplace's remote servers as much as for locally installed
                // ones — which is what makes "every MCP operation is an AgentStep"
                // a property of the application rather than of one call site's
                // good manners.
                //
                // The graph has one deliberate cycle, broken with Func<>: the
                // session provider needs endpoint credentials, which the connector
                // service holds, and the connector service dispatches through the
                // gateway, which owns the session provider.
                //
                // Sran:ConnectorRoot overrides the managed installation location.
                // It is a deployment decision (a managed install may put connectors
                // on a read-only share), and it is also what lets the end-to-end
                // tests exercise THIS composition instead of a parallel one.
                var connectorRoot = context.Configuration["Sran:ConnectorRoot"]
                                    ?? Path.Combine(
                                        Environment.GetFolderPath(
                                            Environment.SpecialFolder.LocalApplicationData),
                                        "SRAN Desktop", "connectors");
                var installationPolicy = new ConnectorInstallationPolicy(
                [
                    AppContext.BaseDirectory,
                    connectorRoot
                ]);
                services.AddSingleton(installationPolicy);

                services.AddSingleton<IConnectorTrustStore>(sp =>
                    new SranDesktop.Infrastructure.Mcp.Connectors.JsonConnectorTrustStore(
                        context.Configuration["Sran:ConnectorTrustFile"]
                        // Never under the connector root: a server that could
                        // rewrite the file authorising it would need one launch
                        // to authorise itself.
                        ?? (dataDirectory is { Length: > 0 }
                            ? Path.Combine(dataDirectory, "connector-trust.json")
                            : SranDesktop.Infrastructure.Mcp.Connectors
                                .JsonConnectorTrustStore.DefaultPath),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));
                services.AddSingleton<SranDesktop.Infrastructure.Mcp.Connectors
                    .TransientConnectorGrants>();

                // Connector credentials. Two stores on purpose: the account index
                // is listable metadata, the tokens go to DPAPI. Neither is the
                // application database — a token in SQLite ends up in every
                // backup and diagnostic dump anybody ever takes.
                services.AddSingleton<IConnectorCredentialStore>(sp =>
                    new SranDesktop.Infrastructure.Credentials.DpapiConnectorCredentialStore(
                        dataDirectory is { Length: > 0 }
                            ? Path.Combine(dataDirectory, "connector-accounts.json")
                            : SranDesktop.Infrastructure.Credentials
                                .DpapiConnectorCredentialStore.DefaultIndexPath,
                        sp.GetRequiredService<ICredentialStore>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));
                services.AddSingleton<SranDesktop.Infrastructure.Credentials.IOAuthTokenClient>(
                    sp => new SranDesktop.Infrastructure.Credentials.OAuthTokenClient(
                        logger: sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));
                services.AddSingleton(new OAuthAttemptRegistry());
                // Provider endpoints are deployment configuration. An empty map
                // means no connector can start an OAuth flow, which is the right
                // default: the alternative is guessing an endpoint.
                var oauthProviders = context.Configuration
                    .GetSection("Sran:OAuthProviders")
                    .Get<Dictionary<string, OAuthProviderConfig>>()
                    ?? new Dictionary<string, OAuthProviderConfig>(StringComparer.Ordinal);
                services.AddSingleton<Func<string, OAuthProviderConfig?>>(_ =>
                    connectorId => oauthProviders.TryGetValue(connectorId, out var provider)
                        ? provider with { ConnectorId = connectorId }
                        : null);
                services.AddSingleton<ICredentialBroker>(sp =>
                    new SranDesktop.Infrastructure.Credentials.CredentialBroker(
                        sp.GetRequiredService<IConnectorCredentialStore>(),
                        sp.GetRequiredService<SranDesktop.Infrastructure.Credentials
                            .IOAuthTokenClient>(),
                        sp.GetRequiredService<Func<string, OAuthProviderConfig?>>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));
                services.AddSingleton<IOAuthAuthorizationService>(sp =>
                    new SranDesktop.Infrastructure.Credentials.OAuthAuthorizationService(
                        sp.GetRequiredService<IConnectorCredentialStore>(),
                        sp.GetRequiredService<SranDesktop.Infrastructure.Credentials
                            .IOAuthTokenClient>(),
                        sp.GetRequiredService<IBrowserLauncher>(),
                        sp.GetRequiredService<OAuthAttemptRegistry>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));

                services.AddSingleton<IMcpConnectorService>(sp =>
                    new SranDesktop.Infrastructure.Services.McpConnectorService(
                        sp.GetRequiredService<ICredentialStore>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Services.McpConnectorService>>(),
                        context.Configuration["Sran:McpCatalogUrl"],
                        sp.GetRequiredService<ITokenSession>(),
                        stateFilePath: dataDirectory is { Length: > 0 }
                            ? Path.Combine(dataDirectory, "connectors.json")
                            : null,
                        // How long one connector's whole dial may take before the
                        // sweep gives up on it. Deployment-tunable because a
                        // server behind a slow VPN and one that is simply dead
                        // look identical until the clock runs out.
                        dialTimeout: double.TryParse(
                            context.Configuration["Sran:ConnectorDialTimeoutSeconds"],
                            out var dialSeconds) && dialSeconds > 0
                            ? TimeSpan.FromSeconds(dialSeconds)
                            : null,
                        gateway: () => sp.GetService<IMcpDurableGateway>(),
                        candidates: sp.GetRequiredService<SranDesktop.Infrastructure.Mcp
                            .Connectors.TransientConnectorGrants>(),
                        trustAdministration: sp.GetRequiredService<IConnectorTrustAdministration>(),
                        trustStore: sp.GetRequiredService<IConnectorTrustStore>(),
                        sessions: () => sp.GetService<SranDesktop.Infrastructure.Mcp.Remote
                            .IMcpConnectorSessionProvider>(),
                        broker: sp.GetRequiredService<ICredentialBroker>()));

                services.AddSingleton<IConnectorManifestResolver>(sp =>
                    new SranDesktop.Infrastructure.Mcp.Connectors.ConnectorManifestResolver(
                        connectorRoot,
                        sp.GetRequiredService<IConnectorTrustStore>(),
                        installationPolicy,
                        externalDeclarations: () =>
                            sp.GetRequiredService<IMcpConnectorService>()
                                is SranDesktop.Infrastructure.Services.McpConnectorService service
                                ? service.InstalledDeclarations()
                                : [],
                        candidates: sp.GetRequiredService<SranDesktop.Infrastructure.Mcp
                            .Connectors.TransientConnectorGrants>(),
                        logger: sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));
                services.AddSingleton<IConnectorTrustAdministration>(sp =>
                    new SranDesktop.Infrastructure.Mcp.Connectors.ConnectorTrustAdministration(
                        sp.GetRequiredService<IConnectorManifestResolver>(),
                        sp.GetRequiredService<IConnectorTrustStore>(),
                        installationPolicy,
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));

                services.AddSingleton<IMcpDispatchJournal>(_ =>
                    new SranDesktop.Infrastructure.Mcp.Remote.McpDispatchJournal(
                        Path.Combine(connectorRoot, "mcp-intents.json")));
                services.AddSingleton<
                    SranDesktop.Infrastructure.Mcp.Remote.IMcpConnectorSessionProvider>(sp =>
                    new SranDesktop.Infrastructure.Mcp.Remote.IsolatedMcpConnectorSessionProvider(
                        sp.GetRequiredService<IConnectorManifestResolver>(),
                        new SranDesktop.Infrastructure.Mcp.Remote.IsolatedMcpServerLauncher(
                            Path.Combine(connectorRoot, "workspaces"),
                            () => sp.GetRequiredService<IMcpConnectorService>()
                                as SranDesktop.Infrastructure.Mcp.Remote
                                    .IConnectorEndpointAuthority,
                            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                                SranDesktop.Infrastructure.Services.McpConnectorService>>()),
                        sp.GetRequiredService<IConnectorTrustStore>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>(),
                        // Resolved per acquisition so an account connected after
                        // a session exists is picked up without a reconnect.
                        endpointAuthority: () =>
                            sp.GetRequiredService<IMcpConnectorService>()
                                as SranDesktop.Infrastructure.Mcp.Remote
                                    .IConnectorEndpointAuthority));
                services.AddSingleton<IMcpDurableGateway>(sp =>
                    new SranDesktop.Infrastructure.Mcp.Remote.McpDurableGateway(
                        sp.GetRequiredService<IDurableTaskRuntime>(),
                        sp.GetRequiredService<IMcpDispatchJournal>(),
                        sp.GetRequiredService<
                            SranDesktop.Infrastructure.Mcp.Remote.IMcpConnectorSessionProvider>(),
                        installation: installationPolicy,
                        conversations: sp.GetRequiredService<IConversationStore>(),
                        // A server that accepts a request and never answers would
                        // otherwise hold the run open indefinitely.
                        operationTimeout: double.TryParse(
                            context.Configuration["Sran:McpOperationTimeoutSeconds"],
                            out var operationSeconds) && operationSeconds > 0
                            ? TimeSpan.FromSeconds(operationSeconds)
                            : null,
                        logger: sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<
                            SranDesktop.Infrastructure.Services.McpConnectorService>>()));

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
