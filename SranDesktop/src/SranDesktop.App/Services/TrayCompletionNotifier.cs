using System.Windows;
using H.NotifyIcon;
using H.NotifyIcon.Core;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.App.Services;

/// <summary>
/// OS-level completion notification via the existing H.NotifyIcon tray icon.
/// Balloon notifications work unpackaged/unsigned with zero registration —
/// unlike Action Center toasts, which need an AUMID + Start-menu shortcut a
/// dev-run build doesn't have. Registered in DI at build time; the tray icon
/// and main window only exist after startup, so App.OnStartup calls
/// <see cref="Attach"/> once they do. Until then (and after
/// <see cref="MarkShuttingDown"/>) every call is a silent no-op.
/// </summary>
public sealed class TrayCompletionNotifier : ICompletionNotifier, IWindowStateProvider
{
    private TaskbarIcon? _trayIcon;
    private Window? _mainWindow;
    private Action<string>? _openConversation;
    private volatile bool _shuttingDown;

    /// <summary>The conversation the most recent balloon was about — the click
    /// target. A single field is enough: a newer balloon replaces the older
    /// one on screen, so the click always means "take me to the latest".</summary>
    private string? _lastNotifiedConversationId;

    /// <summary>Wire the live tray icon + main window (App.OnStartup).
    /// <paramref name="openConversation"/> restores the window and opens the
    /// clicked notification's conversation.</summary>
    public void Attach(TaskbarIcon trayIcon, Window mainWindow, Action<string> openConversation)
    {
        _trayIcon = trayIcon;
        _mainWindow = mainWindow;
        _openConversation = openConversation;
        trayIcon.TrayBalloonTipClicked += (_, _) => OnBalloonClicked();
    }

    /// <summary>Stop notifying (app teardown; the tray icon is being disposed).</summary>
    public void MarkShuttingDown() => _shuttingDown = true;

    /// <inheritdoc/>
    public bool IsWindowActive
    {
        get
        {
            if (_shuttingDown || _mainWindow is not { } window)
                return false;
            bool Read() => window.IsVisible
                           && window.WindowState != WindowState.Minimized
                           && window.IsActive;
            // Window state is UI-thread affine; turn completions already run
            // there, but marshal defensively for any off-thread caller.
            return window.Dispatcher.CheckAccess() ? Read() : window.Dispatcher.Invoke(Read);
        }
    }

    /// <inheritdoc/>
    public void NotifyTurnCompleted(string conversationId, string? conversationTitle, bool isError)
    {
        if (_shuttingDown || _trayIcon is not { } icon)
            return;

        _lastNotifiedConversationId = conversationId;

        // Looked up at show-time so a locale switch is honored immediately.
        var title = LocaleService.Instance.T(isError ? "notify.turnError" : "notify.turnDone");
        var body = string.IsNullOrWhiteSpace(conversationTitle)
            ? LocaleService.Instance.T("notify.clickToOpen")
            : conversationTitle!;

        try
        {
            void Show() => icon.ShowNotification(
                title: title,
                message: body,
                icon: isError ? NotificationIcon.Warning : NotificationIcon.Info);
            if (icon.Dispatcher.CheckAccess())
                Show();
            else
                icon.Dispatcher.Invoke(Show);
        }
        catch (Exception)
        {
            // Best-effort: a balloon failure (icon torn down mid-call, shell
            // quirks) must never surface into the chat turn.
        }
    }

    private void OnBalloonClicked()
    {
        if (_shuttingDown)
            return;
        if (_lastNotifiedConversationId is { } id)
            _openConversation?.Invoke(id);
    }
}
