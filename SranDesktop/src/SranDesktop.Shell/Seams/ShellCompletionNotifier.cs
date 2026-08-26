using System.Windows;
using H.NotifyIcon;
using H.NotifyIcon.Core;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Shell.Seams;

/// <summary>
/// Seam 2 — the <c>TrayCompletionNotifier</c> trio (<see cref="ICompletionNotifier"/>
/// + <see cref="IWindowStateProvider"/>). The WPF app's version lives in the App
/// project and pulls <c>Presentation.Localization</c>; the shell owns its own
/// tray, so it owns its own notifier. Same shape: registered at build time,
/// <see cref="Attach"/>ed once the tray icon and window exist, silent no-op
/// before that and after <see cref="MarkShuttingDown"/>.
/// </summary>
public sealed class ShellCompletionNotifier : ICompletionNotifier, IWindowStateProvider
{
    private readonly ILogger<ShellCompletionNotifier> _logger;
    private TaskbarIcon? _trayIcon;
    private Window? _window;
    private volatile bool _shuttingDown;

    public ShellCompletionNotifier(ILogger<ShellCompletionNotifier> logger)
    {
        _logger = logger;
    }

    public void Attach(TaskbarIcon trayIcon, Window window)
    {
        _trayIcon = trayIcon;
        _window = window;
    }

    public void MarkShuttingDown() => _shuttingDown = true;

    public bool IsWindowActive
    {
        get
        {
            var w = _window;
            if (w is null)
                return false;
            bool Query() => w.IsVisible && w.WindowState != WindowState.Minimized && w.IsActive;
            return w.Dispatcher.CheckAccess() ? Query() : w.Dispatcher.Invoke(Query);
        }
    }

    public void NotifyTurnCompleted(string conversationId, string? conversationTitle, bool isError)
    {
        _logger.LogInformation("Turn completed (conversation {Id}, error={IsError}) — window active={Active}",
            conversationId, isError, IsWindowActive);
        if (_shuttingDown || _trayIcon is not { } icon)
            return;
        try
        {
            void Show() => icon.ShowNotification(
                title: isError ? "SRAN Desktop — error" : "SRAN Desktop",
                message: string.IsNullOrWhiteSpace(conversationTitle) ? "Reply ready" : conversationTitle,
                icon: isError ? NotificationIcon.Error : NotificationIcon.Info);
            if (icon.Dispatcher.CheckAccess())
                Show();
            else
                icon.Dispatcher.Invoke(Show);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Tray balloon failed");
        }
    }
}
