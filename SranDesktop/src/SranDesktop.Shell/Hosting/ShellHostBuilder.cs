using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Hosting;
using SranDesktop.Shell.Seams;

namespace SranDesktop.Shell.Hosting;

/// <summary>
/// Shell composition root. Calls the same <c>AddSranCore</c> the WPF app calls,
/// then registers the shell's own implementation of every host-supplied seam.
/// <c>ValidateOnBuild</c> makes a missing seam a startup failure.
/// </summary>
public static class ShellHostBuilder
{
    public static IHost Build(string[] args)
    {
        return Host.CreateDefaultBuilder(args)
            .UseContentRoot(AppContext.BaseDirectory)
            .UseDefaultServiceProvider(o => o.ValidateOnBuild = true)
            .UseSerilog((context, loggerConfiguration) =>
            {
                var logDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "SRAN Desktop", "logs");
                Directory.CreateDirectory(logDir);

                // Own file name: the WPF app writes app-.log to the same folder.
                loggerConfiguration
                    .MinimumLevel.Debug()
                    .WriteTo.File(
                        Path.Combine(logDir, "shell-.log"),
                        rollingInterval: RollingInterval.Day,
                        retainedFileCountLimit: 7);
            })
            .ConfigureServices((context, services) =>
            {
                services.AddSranCore(context.Configuration);
                services.AddSranWindows();
                services.AddSranWindowsDesktop();

                // Shell configuration (origin + allow-list as config, not constants).
                var options = context.Configuration.GetSection(ShellOptions.SectionName).Get<ShellOptions>()
                              ?? new ShellOptions();
                if (string.IsNullOrWhiteSpace(options.PageUrl))
                    throw new InvalidOperationException("Shell:PageUrl is not configured.");
                if (string.IsNullOrWhiteSpace(options.PortalOrigin))
                    throw new InvalidOperationException(
                        "Shell:PortalOrigin is not configured (Phase 2 §2.2 — the portal redirect is the re-auth signal).");
                if (options.SessionRefreshMinutes is < 1 or >= 60)
                    throw new InvalidOperationException(
                        $"Shell:SessionRefreshMinutes must be within [1, 59] — strictly shorter than the 60-minute access TTL (got {options.SessionRefreshMinutes}).");
                services.AddSingleton(options);
                // Throws on an empty list, a wildcard, or a portal origin that
                // is also allow-listed — before any window exists.
                var navigationPolicy = new NavigationPolicy(options.AllowedOrigins, options.PortalOrigin);
                if (!navigationPolicy.IsTopLevelAllowed(options.PageUrl))
                    throw new InvalidOperationException(
                        $"Shell:PageUrl '{options.PageUrl}' is outside Shell:AllowedOrigins.");
                if (options.UserDataFolderOverlapsWpfProfile())
                    throw new InvalidOperationException(
                        $"Shell:UserDataFolder '{options.ResolvedUserDataFolder}' overlaps the WPF app profile '{ShellOptions.SharedWpfUserDataFolder}'.");
                services.AddSingleton(navigationPolicy);

                // ── Phase 2: the shell's session is ITS OWN, not the WPF app's ──
                // Both hosts use TokenSession over ICredentialStore with the same
                // key names, and each caches tokens in memory after first read.
                // A shared credentials.dat would make the two RUNNING hosts a
                // fresh two-holder collision (spike finding 7, this time
                // WPF↔shell): whichever rotates first strands the other's cached
                // refresh token → 401 refresh_reuse. A separate DPAPI file per
                // host removes that by construction, and keeps Phase 2 testing
                // (clearing the shell's session) from signing the owner's WPF
                // app out. Registered AFTER AddSranCore so this wins resolution.
                services.AddSingleton<ICredentialStore>(sp =>
                    new SranDesktop.Infrastructure.Storage.DpapiCredentialStore(
                        Path.Combine(
                            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                            "SRAN Desktop", "shell-credentials.dat"),
                        sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Infrastructure.Storage.DpapiCredentialStore>>()));

                services.AddSingleton<ShellSessionController>();

                // ── Phase 3: the bridge and the host-side turn ──
                services.AddSingleton<SranDesktop.Shell.Bridge.BridgeChannel>();
                services.AddSingleton<SranDesktop.Shell.Bridge.ShellTurnCoordinator>();

                // ── Phase 5: the control vocabulary (host.query/host.result) ──
                // Native dialogs stay native (§2.4); the dispatcher maps the
                // closed op set to real services with the refusal rules (§2.3).
                services.AddSingleton<IShellNativeDialogs, ShellNativeDialogs>();
                services.AddSingleton<SranDesktop.Shell.Bridge.ShellHostQueryCoordinator>();

                // ── Phase 5 §4.1: one-shot local→server conversation migration ──
                services.AddSingleton<ShellConversationMigrator>();

#if DEBUG
                // §5.8 demo seam: --force-step-limit re-registers the orchestrator
                // with maxIterations=1 so any multi-tool turn pauses after one step
                // (AgentPausedEvent), letting the Continue path be exercised live.
                // DEBUG-only and never in the shipped graph.
                if (Environment.GetCommandLineArgs().Contains("--force-step-limit"))
                {
                    services.AddSingleton<SranDesktop.Application.Interfaces.IChatOrchestrator>(sp =>
                        new SranDesktop.Application.Services.ChatOrchestrator(
                            sp.GetRequiredService<SranDesktop.Application.Interfaces.IGatewayApiClient>(),
                            sp.GetRequiredService<SranDesktop.Application.Interfaces.IMcpToolHost>(),
                            sp.GetRequiredService<SranDesktop.Application.Interfaces.IPermissionService>(),
                            sp.GetRequiredService<SranDesktop.Application.Interfaces.IConversationStore>(),
                            sp.GetRequiredService<Microsoft.Extensions.Logging.ILogger<SranDesktop.Application.Services.ChatOrchestrator>>(),
                            maxIterations: 1,
                            shellSession: sp.GetRequiredService<SranDesktop.Application.Services.ShellSession>(),
                            scopeResolver: sp.GetRequiredService<SranDesktop.Application.Interfaces.IToolScopeResolver>(),
                            settings: sp.GetRequiredService<SranDesktop.Application.Interfaces.ISettingsService>(),
                            readGuard: sp.GetRequiredService<SranDesktop.Application.Interfaces.ISensitiveReadGuard>()));
                }
#endif

                // ── The host-supplied seams (SranCoreServiceCollectionExtensions summary) ──
                services.AddSingleton<IDialogService, ShellDialogService>();
                services.AddSingleton<ShellCompletionNotifier>();
                services.AddSingleton<ICompletionNotifier>(sp => sp.GetRequiredService<ShellCompletionNotifier>());
                services.AddSingleton<IWindowStateProvider>(sp => sp.GetRequiredService<ShellCompletionNotifier>());
                // Not DI seams, but owned here so they are constructed (and validated) with the graph:
                services.AddSingleton<ShellConsentHandler>();
                services.AddSingleton<ToastLogSink>();

                services.AddSingleton<ShellWindow>();
            })
            .Build();
    }
}
