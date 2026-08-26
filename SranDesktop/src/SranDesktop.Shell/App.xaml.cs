using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using H.NotifyIcon;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Platform;
using SranDesktop.Shell.Hosting;
using SranDesktop.Shell.Seams;

namespace SranDesktop.Shell;

/// <summary>
/// The shell is a WPF <see cref="System.Windows.Application"/> on purpose
/// (DESKTOP_SHELL_PLAN.md Phase 1 §1): Infrastructure already carries UseWPF,
/// and the two clipboard tools resolve <c>Application.Current.Dispatcher</c>.
/// One window, one WebView2, the existing services. No chat, no bridge.
/// </summary>
public partial class App : System.Windows.Application
{
    /// <summary>Distinct from the WPF app's "SranDesktop" so the two hosts never
    /// share a single-instance mutex or pipe.</summary>
    private const string InstanceName = "SranDesktopShell";

    private IHost? _host;
    private ILogger<App>? _logger;
    private TaskbarIcon? _trayIcon;
    private GlobalHotkey? _hotkey;
    private ShellWindow? _window;
    private ShellCompletionNotifier? _notifier;
    private bool _shuttingDown;

    public static readonly DateTime ProcessStartUtc = System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime();

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        if (!SingleInstance.Claim(e.Args, InstanceName))
        {
            Shutdown();
            return;
        }

        RegisterGlobalExceptionHandlers();

        try
        {
            // ValidateOnBuild: a missing seam throws HERE.
            _host = ShellHostBuilder.Build(e.Args);
        }
        catch (Exception ex)
        {
            MessageBox.Show($"The shell could not compose its services:\n\n{ex.Message}",
                "SRAN Desktop Shell", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
            return;
        }
        await _host.StartAsync();
        _logger = _host.Services.GetRequiredService<ILogger<App>>();
        _logger.LogInformation("Shell starting (pid {Pid}, args: {Args})",
            Environment.ProcessId, string.Join(' ', e.Args.Where(a => !a.StartsWith("sran://", StringComparison.OrdinalIgnoreCase))));

        // Phase 3: consent is now the bridge round-trip (ShellTurnCoordinator
        // subscribes ConsentRequested when its bridge attaches, inside the
        // window). The Phase 1 deny-all ShellConsentHandler is no longer
        // attached — a second subscriber would double-answer the single
        // awaited delegate. ToastLogSink still drains the static toast queue.
        _ = _host.Services.GetRequiredService<ToastLogSink>();

        // Same startup responsibilities the WPF app has (App.xaml.cs), minus the UI.
        var settings = _host.Services.GetRequiredService<ISettingsService>();
        SranDesktop.Infrastructure.Mcp.Tools.PathGuard.ConfigureGrantedRoots(AgentRoots(settings));
        settings.SettingsChanged += (_, _) =>
            SranDesktop.Infrastructure.Mcp.Tools.PathGuard.ConfigureGrantedRoots(AgentRoots(settings));
        var browserBridge = _host.Services.GetRequiredService<IBrowserBridgeService>();
        browserBridge.Start();
        ProvisionBrowserPairingBestEffort(browserBridge, settings);
        RegisterSranSchemeBestEffort();

        _window = _host.Services.GetRequiredService<ShellWindow>();
        MainWindow = _window;
        _window.Closing += Window_Closing;

        _trayIcon = CreateTrayIcon();
        _notifier = _host.Services.GetRequiredService<ShellCompletionNotifier>();
        _notifier.Attach(_trayIcon, _window);

        try
        {
            _hotkey = new GlobalHotkey(_window, ToggleWindow);
            if (!_hotkey.Register())
                _logger.LogWarning("Global hotkey Ctrl+Shift+Space is unavailable (held by another app — likely the WPF app).");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to register global hotkey");
        }

        SingleInstance.ListenForActivation(ShowWindow, DispatchDeepLink); // pipe name set by Claim(InstanceName)
        var coldDeepLink = e.Args.FirstOrDefault(a => a.StartsWith("sran://", StringComparison.OrdinalIgnoreCase));
        if (coldDeepLink is not null)
            DispatchDeepLink(coldDeepLink);

        if (!e.Args.Contains("--minimized"))
            _window.Show();
        else
            _logger.LogInformation("Started minimized to tray");
    }

    /// <summary>The shell has no Settings→Browser pane (that lived in the WPF
    /// Presentation layer the shell dropped), so it can't ask the user to copy
    /// the pairing token and paste it into the extension. Instead it drops the
    /// current token into the loaded extension's folder as pairing.json; the
    /// extension's service worker reads it and self-pairs — and re-reads it if
    /// its stored token was rejected, so a regenerated token self-heals with no
    /// user step. Folder = BrowserExtensionPath (the actually-loaded unpacked
    /// folder) if set, else the copy shipped beside the app. Never throws.</summary>
    private void ProvisionBrowserPairingBestEffort(IBrowserBridgeService bridge, ISettingsService settings)
    {
        try
        {
            var installer = _host!.Services.GetService<IBrowserExtensionInstaller>();
            if (installer is null)
                return;
            var folder = settings.Current.BrowserExtensionPath;
            if (string.IsNullOrWhiteSpace(folder))
                folder = Path.Combine(AppContext.BaseDirectory, "browser-extension");
            installer.ProvisionPairingToken(folder, bridge.GetOrCreatePairingToken());
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "Browser pairing provisioning failed");
        }
    }

    private static IReadOnlyList<string> AgentRoots(ISettingsService settings)
    {
        var current = settings.Current;
        if (string.IsNullOrWhiteSpace(current.WorkingFolder))
            return current.GrantedFolders;
        return new List<string>(current.GrantedFolders) { current.WorkingFolder };
    }

    /// <summary>sran:// routing — identical to the WPF app: both coordinators are
    /// offered every link; each claims only its own shape. Never throws.</summary>
    private void DispatchDeepLink(string uri)
    {
        try
        {
            _host?.Services.GetService<SranDesktop.Application.Services.LoginOAuthCoordinator>()?.HandleDeepLink(uri);
            _host?.Services.GetService<SranDesktop.Application.Services.ConnectorOAuthCoordinator>()?.HandleDeepLink(uri);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "Deep link dispatch failed");
        }
    }

    /// <summary>Dev fallback for the sran:// scheme. Never overrides an existing
    /// registration (the installer's, or the WPF app's) — so while the WPF app is
    /// installed, deep links keep arriving THERE, not here. Logged so nobody
    /// debugs a "missing" callback for an hour.</summary>
    private void RegisterSranSchemeBestEffort()
    {
        try
        {
            const string commandKeyPath = @"Software\Classes\sran\shell\open\command";
            using (var existing = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(commandKeyPath))
            {
                if (existing?.GetValue(null) is string cmd)
                {
                    _logger?.LogInformation("sran:// already registered to {Command} — leaving it (deep links go there, not to the shell)", cmd);
                    return;
                }
            }
            var exe = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exe))
                return;
            using var root = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Classes\sran");
            root.SetValue(null, "URL:SRAN Desktop Protocol");
            root.SetValue("URL Protocol", "");
            using var command = root.CreateSubKey(@"shell\open\command");
            command.SetValue(null, $"\"{exe}\" \"%1\"");
            _logger?.LogInformation("Registered sran:// URI scheme (dev fallback) to the shell");
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "sran:// scheme registration failed");
        }
    }

    // ---------- tray / window ----------

    private TaskbarIcon CreateTrayIcon()
    {
        var icon = new TaskbarIcon
        {
            ToolTipText = "SRAN Desktop (shell preview)",
            IconSource = new BitmapImage(new Uri("pack://application:,,,/SranDesktop.Shell;component/Assets/app.ico")),
            ContextMenu = CreateTrayMenu()
        };
        icon.TrayMouseDoubleClick += (_, _) => ShowWindow();
        icon.ForceCreate(enablesEfficiencyMode: false);
        return icon;
    }

    private ContextMenu CreateTrayMenu()
    {
        var menu = new ContextMenu();
        var show = new MenuItem { Header = "Show" };
        show.Click += (_, _) => ShowWindow();
        menu.Items.Add(show);
        menu.Items.Add(new Separator());
        // Phase 2 §2.5: host-initiated logout. Clears DPAPI + the WebView jar,
        // blanks the page, lands on the shell-owned signed-out view.
        var signOut = new MenuItem { Header = "Sign out" };
        signOut.Click += async (_, _) =>
        {
            try
            {
                var controller = _host?.Services.GetService<SranDesktop.Shell.Hosting.ShellSessionController>();
                if (controller is not null)
                    await controller.SignOutAsync();
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "Tray sign-out failed");
            }
        };
        menu.Items.Add(signOut);
        menu.Items.Add(new Separator());
        var exit = new MenuItem { Header = "Exit" };
        exit.Click += (_, _) => ExitApplication();
        menu.Items.Add(exit);
        return menu;
    }

    private void Window_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (_shuttingDown)
            return;
        // Same "minimize to tray on close" the WPF app has; honours the shared setting.
        var minimizeToTray = _host?.Services.GetService<ISettingsService>()?.Current.MinimizeToTray ?? true;
        if (!minimizeToTray)
        {
            ExitApplication();
            return;
        }
        e.Cancel = true;
        _window?.Hide();
    }

    private void ShowWindow()
    {
        if (_window is null) return;
        _window.Show();
        if (_window.WindowState == WindowState.Minimized)
            _window.WindowState = WindowState.Normal;
        _window.Activate();
        _window.Topmost = true;
        _window.Topmost = false;
    }

    private void ToggleWindow()
    {
        if (_window is { IsVisible: true, IsActive: true })
            _window.Hide();
        else
            ShowWindow();
    }

    private void ExitApplication()
    {
        _shuttingDown = true;
        _notifier?.MarkShuttingDown();
        _trayIcon?.Dispose();
        _trayIcon = null;
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _notifier?.MarkShuttingDown();
        _hotkey?.Dispose();
        _trayIcon?.Dispose();
        SingleInstance.Release();
        if (_host is not null)
        {
            try
            {
                // OnExit runs while the WPF dispatcher is being torn down.
                // Awaiting here can strand the continuation on that dispatcher,
                // so stop synchronously with the same three-second bound.
                _host.StopAsync(TimeSpan.FromSeconds(3)).GetAwaiter().GetResult();
            }
            finally
            {
                _host.Dispose();
            }
        }
        base.OnExit(e);
    }

    // ---------- global exception handling ----------

    private void RegisterGlobalExceptionHandlers()
    {
        DispatcherUnhandledException += (_, args) =>
        {
            _logger?.LogCritical(args.Exception, "Unhandled UI-thread exception");
            ReportCrashBestEffort(args.Exception);
            args.Handled = true;
            MessageBox.Show($"An unexpected error occurred:\n\n{args.Exception.Message}\n\nThe error has been logged.",
                "SRAN Desktop Shell", MessageBoxButton.OK, MessageBoxImage.Error);
        };
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            if (args.ExceptionObject is Exception ex)
            {
                _logger?.LogCritical(ex, "Unhandled domain exception (terminating={Terminating})", args.IsTerminating);
                ReportCrashBestEffort(ex);
            }
        };
        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            _logger?.LogError(args.Exception, "Unobserved task exception");
            args.SetObserved();
        };
    }

    public void ReportCrashBestEffort(Exception exception)
    {
        try
        {
            var reporter = _host?.Services.GetService<ICrashReportService>();
            if (reporter is { IsEnabled: true })
                _ = reporter.ReportAsync(exception);
        }
        catch
        {
            // Never crash the crash path.
        }
    }
}
