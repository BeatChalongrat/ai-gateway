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

                // MCP tools
                services.AddSingleton(sp =>
                {
                    var registry = new McpToolRegistry();
                    registry.Register(new ReadFileTool());
                    registry.Register(new WriteFileTool());
                    registry.Register(new ListDirectoryTool());
                    registry.Register(new SearchFilesTool());
                    registry.Register(new OpenUrlTool());
                    registry.Register(new ReadClipboardTool());
                    registry.Register(new WriteClipboardTool());
                    registry.Register(new RunCommandTool(
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<RunCommandTool>>()));
                    // Web search proxies to the gateway's /v1/search (R9 C);
                    // only listed while the composer toggle is on.
                    registry.Register(new WebSearchTool(
                        sp.GetRequiredService<IGatewayApiClient>(),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<WebSearchTool>>()));
                    // Model-driven skill loading (Phase 6 Task 2) — reads
                    // bundled SKILL.md on demand; no side effects (AllowAlways).
                    registry.Register(new UseSkillTool(sp.GetRequiredService<ISkillManager>()));
                    // Computer use (Claude-style: screenshot → look → act → verify)
                    registry.Register(new ComputerScreenshotTool());
                    registry.Register(new ComputerClickTool());
                    registry.Register(new ComputerTypeTool());
                    registry.Register(new ComputerKeyTool());
                    registry.Register(new ComputerScrollTool());
                    registry.Register(new ComputerDragTool());
                    registry.Register(new OpenApplicationTool());
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
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILoggerFactory>()));
                    return registry;
                });
                services.AddSingleton<IMcpToolHost>(sp => new McpToolHost(
                    sp.GetRequiredService<McpToolRegistry>(),
                    sp.GetRequiredService<IMcpConnectorService>()));
                services.AddSingleton<IPermissionService, PermissionService>();
                // Explicit factory: the ctor's optional cap parameters (used by
                // run_subagent's nested instance) must stay at their defaults
                // for the app-wide singleton.
                services.AddSingleton<IChatOrchestrator>(sp => new ChatOrchestrator(
                    sp.GetRequiredService<IGatewayApiClient>(),
                    sp.GetRequiredService<IMcpToolHost>(),
                    sp.GetRequiredService<IPermissionService>(),
                    sp.GetRequiredService<IConversationStore>(),
                    sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<ChatOrchestrator>>()));

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
                // Phase 12 E: on-device Office plan executors (opt-in via
                // Settings → PreferLocalOfficeGeneration; pptx stays server-side).
                services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalExcelPlanExecutor>();
                services.AddSingleton<ILocalPlanExecutor, SranDesktop.Infrastructure.Office.LocalDocPlanExecutor>();
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
