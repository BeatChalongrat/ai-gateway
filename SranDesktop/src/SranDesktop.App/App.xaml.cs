using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using H.NotifyIcon;
using H.NotifyIcon.Core;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using SranDesktop.App.Hosting;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.ViewModels;
using SranDesktop.Presentation.Views;
using SranDesktop.Presentation.Views.Dialogs;

namespace SranDesktop.App;

public partial class App : System.Windows.Application
{
    private IHost? _host;
    private ILogger<App>? _logger;
    private TaskbarIcon? _trayIcon;
    private GlobalHotkey? _hotkey;
    private MainWindow? _mainWindow;
    private ISettingsService? _settings;
    private bool _shuttingDown;
    private bool _trayTipShown;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // Second instance: forward our args (incl. any sran:// deep link the
        // OS routed here) to the first instance and quietly exit.
        if (!SingleInstance.Claim(e.Args))
        {
            Shutdown();
            return;
        }

        RegisterGlobalExceptionHandlers();

        _host = AppHostBuilder.Build(e.Args);
        await _host.StartAsync();
        _logger = _host.Services.GetRequiredService<ILogger<App>>();

        _settings = _host.Services.GetRequiredService<ISettingsService>();
        SranDesktop.Presentation.Services.ThemeManager.Apply(_settings.Current.Theme);

        // Apply the user's folder grants to the agent's PathGuard now, and on
        // every settings change (Phase 6 Task 1). A grant only widens WHERE
        // file tools may operate — consent still gates each write. The
        // composer's working folder counts as an extra granted root.
        SranDesktop.Infrastructure.Mcp.Tools.PathGuard.ConfigureGrantedRoots(AgentRoots(_settings));
        _settings.SettingsChanged += (_, _) =>
            SranDesktop.Infrastructure.Mcp.Tools.PathGuard.ConfigureGrantedRoots(AgentRoots(_settings));

        // Housekeeping: sweep leaked screenshot temp files (Phase 6 Task 4).
        CleanupOldScreenshots();

        // sran:// URI scheme (Phase 7 C1): the installer registers it; this
        // dev fallback covers running from the IDE / a loose build.
        RegisterSranSchemeBestEffort();

        // First run: onboarding dialog writes settings.json (and server URLs
        // used lazily by the HTTP clients that haven't been created yet).
        if (_settings.IsFirstRun)
        {
            try
            {
                var firstRun = new FirstRunDialog(
                    _settings, _host.Services.GetRequiredService<IStartupManager>());
                firstRun.ShowDialog();
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "First-run dialog failed");
            }
        }

        _mainWindow = _host.Services.GetRequiredService<MainWindow>();
        MainWindow = _mainWindow;

        _trayIcon = CreateTrayIcon();

        _mainWindow.Closing += MainWindow_Closing;

        // Register the global hotkey; EnsureHandle works before the window shows.
        try
        {
            _hotkey = new GlobalHotkey(_mainWindow, ToggleMainWindow);
            if (!_hotkey.Register())
                _logger.LogWarning("Global hotkey Ctrl+Shift+Space is unavailable (held by another app).");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to register global hotkey");
        }

        SingleInstance.ListenForActivation(ShowMainWindow, DispatchDeepLink);

        // Cold start via the sran:// scheme: the OS launched us with the
        // deep link as an argument (Phase 7 C2).
        var coldDeepLink = e.Args.FirstOrDefault(
            a => a.StartsWith("sran://", StringComparison.OrdinalIgnoreCase));
        if (coldDeepLink is not null)
            DispatchDeepLink(coldDeepLink);

        // --minimized (auto-start): create everything but stay in the tray.
        if (!e.Args.Contains("--minimized"))
            _mainWindow.Show();
        else
            _logger.LogInformation("Started minimized to tray");
    }

    /// <summary>Granted folders + the composer's working folder (when set) —
    /// the full list of extra roots the agent's file tools may reach.</summary>
    private static IReadOnlyList<string> AgentRoots(ISettingsService settings)
    {
        var current = settings.Current;
        if (string.IsNullOrWhiteSpace(current.WorkingFolder))
            return current.GrantedFolders;
        var roots = new List<string>(current.GrantedFolders) { current.WorkingFolder };
        return roots;
    }

    /// <summary>Route a ``sran://`` URI to the OAuth coordinator (Phase 7).
    /// Validation lives in OAuthDeepLink.TryParse — hostile input is logged
    /// and dropped there; this never throws.</summary>
    private void DispatchDeepLink(string uri)
    {
        try
        {
            _host?.Services
                .GetService<SranDesktop.Application.Services.ConnectorOAuthCoordinator>()
                ?.HandleDeepLink(uri);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "Deep link dispatch failed");
        }
    }

    /// <summary>Register HKCU\Software\Classes\sran when missing so a dev
    /// build (no installer) still receives OAuth callbacks. Best-effort:
    /// never touches an existing registration (the installer's wins) and
    /// never fails startup.</summary>
    private void RegisterSranSchemeBestEffort()
    {
        try
        {
            const string commandKeyPath = @"Software\Classes\sran\shell\open\command";
            using (var existing = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(commandKeyPath))
            {
                if (existing?.GetValue(null) is string)
                    return; // already registered (installer or a prior run)
            }
            var exe = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exe))
                return;
            using var root = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Classes\sran");
            root.SetValue(null, "URL:SRAN Desktop Protocol");
            root.SetValue("URL Protocol", "");
            using var command = root.CreateSubKey(@"shell\open\command");
            command.SetValue(null, $"\"{exe}\" \"%1\"");
            _logger?.LogInformation("Registered sran:// URI scheme (dev fallback)");
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "sran:// scheme registration failed");
        }
    }

    /// <summary>Fire-and-forget sweep of screenshot temp files older than 24h —
    /// one-time migration for files leaked before Phase 6 (the tool no longer
    /// writes them). Best-effort; a locked/missing file is fine.</summary>
    private void CleanupOldScreenshots()
    {
        _ = Task.Run(() =>
        {
            try
            {
                var cutoff = DateTime.Now.AddHours(-24);
                foreach (var file in System.IO.Directory.EnumerateFiles(
                             System.IO.Path.GetTempPath(), "sran-screenshot-*.png"))
                {
                    try
                    {
                        if (System.IO.File.GetLastWriteTime(file) < cutoff)
                            System.IO.File.Delete(file);
                    }
                    catch (Exception ex) when (ex is System.IO.IOException or UnauthorizedAccessException)
                    {
                        // Locked or vanished — skip.
                    }
                }
            }
            catch (Exception ex)
            {
                _logger?.LogDebug(ex, "Screenshot cleanup skipped");
            }
        });
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        // Persist a typed-but-unsent composer draft before teardown (Phase 3 C4).
        try
        {
            if (_host?.Services.GetService<ChatViewModel>() is { } chat)
                await chat.FlushDraftAsync();
        }
        catch (Exception ex)
        {
            _logger?.LogDebug(ex, "Draft flush on exit failed");
        }

        _hotkey?.Dispose();
        _trayIcon?.Dispose();
        SingleInstance.Release();

        if (_host is not null)
        {
            await _host.StopAsync(TimeSpan.FromSeconds(3));
            _host.Dispose();
        }

        base.OnExit(e);
    }

    // ---------- tray ----------

    private TaskbarIcon CreateTrayIcon()
    {
        var icon = new TaskbarIcon
        {
            ToolTipText = "SRAN Desktop",
            IconSource = new BitmapImage(
                new Uri("pack://application:,,,/SranDesktop;component/Assets/app.ico")),
            ContextMenu = CreateTrayContextMenu()
        };
        icon.TrayMouseDoubleClick += (_, _) => ShowMainWindow();
        icon.ForceCreate(enablesEfficiencyMode: false);
        return icon;
    }

    private ContextMenu CreateTrayContextMenu()
    {
        var menu = new ContextMenu();

        var show = new MenuItem { Header = "Show SRAN Desktop" };
        show.Click += (_, _) => ShowMainWindow();
        menu.Items.Add(show);

        var newChat = new MenuItem { Header = "New Chat" };
        newChat.Click += (_, _) =>
        {
            ShowMainWindow();
            if (MainWindow?.DataContext is MainViewModel vm
                && vm.Conversations.NewChatCommand.CanExecute(null))
            {
                vm.Conversations.NewChatCommand.Execute(null);
            }
        };
        menu.Items.Add(newChat);

        menu.Items.Add(new Separator());

        var exit = new MenuItem { Header = "Exit" };
        exit.Click += (_, _) => ExitApplication();
        menu.Items.Add(exit);

        return menu;
    }

    // ---------- window show / hide ----------

    private void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        // "Minimize to tray on close" (unless the user chose Exit, or turned it off).
        if (_shuttingDown || _settings?.Current.MinimizeToTray is false)
            return;

        e.Cancel = true;
        _mainWindow?.Hide();

        if (!_trayTipShown)
        {
            _trayTipShown = true;
            _trayIcon?.ShowNotification(
                title: "SRAN Desktop",
                message: "Still running in the background - click the tray icon to reopen.",
                icon: NotificationIcon.Info);
        }
    }

    private void ShowMainWindow()
    {
        if (_mainWindow is null) return;
        _mainWindow.Show();
        if (_mainWindow.WindowState == WindowState.Minimized)
            _mainWindow.WindowState = WindowState.Normal;
        _mainWindow.Activate();
        _mainWindow.Topmost = true;
        _mainWindow.Topmost = false;
    }

    private void ToggleMainWindow()
    {
        if (_mainWindow is not null && _mainWindow.IsVisible && _mainWindow.IsActive)
            _mainWindow.Hide();
        else
            ShowMainWindow();
    }

    private void ExitApplication()
    {
        _shuttingDown = true;
        _trayIcon?.Dispose();
        _trayIcon = null;
        Shutdown();
    }

    // ---------- global exception handling ----------

    private void RegisterGlobalExceptionHandlers()
    {
        DispatcherUnhandledException += (_, args) =>
        {
            _logger?.LogCritical(args.Exception, "Unhandled UI-thread exception");
            ReportCrashBestEffort(args.Exception);
            args.Handled = true; // keep the app alive
            ShowCrashDialog(args.Exception);
        };

        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            if (args.ExceptionObject is Exception ex)
            {
                _logger?.LogCritical(ex, "Unhandled domain exception (terminating={Terminating})",
                    args.IsTerminating);
                ReportCrashBestEffort(ex);
            }
        };

        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            _logger?.LogError(args.Exception, "Unobserved task exception");
            args.SetObserved();
        };
    }

    /// <summary>Auto-report only when the user opted in (R11 A3c).</summary>
    private void ReportCrashBestEffort(Exception exception)
    {
        try
        {
            var reporter = _host?.Services.GetService<SranDesktop.Application.Interfaces.ICrashReportService>();
            if (reporter is { IsEnabled: true })
                _ = reporter.ReportAsync(exception);
        }
        catch
        {
            // Never crash the crash path.
        }
    }

    private void ShowCrashDialog(Exception exception)
    {
        try
        {
            var reporter = _host?.Services.GetService<SranDesktop.Application.Interfaces.ICrashReportService>();
            new SranDesktop.Pr