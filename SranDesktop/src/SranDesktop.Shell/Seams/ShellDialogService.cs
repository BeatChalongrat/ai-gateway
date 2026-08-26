using System.Windows;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;

namespace SranDesktop.Shell.Seams;

/// <summary>
/// Seam 1 — <see cref="IDialogService"/>. The WPF app's <c>WpfDialogService</c>
/// is in Presentation, which the shell may not reference. Phase 1 needs only a
/// minimal, shell-owned WPF dialog: a plain MessageBox on the UI thread.
/// Nothing in Phase 1 calls it (no chat runs); it exists so the graph validates
/// and so a future caller gets a real prompt rather than a silent "true".
/// </summary>
public sealed class ShellDialogService : IDialogService
{
    private readonly ILogger<ShellDialogService> _logger;

    public ShellDialogService(ILogger<ShellDialogService> logger)
    {
        _logger = logger;
    }

    public Task<bool> ConfirmAsync(string title, string message, bool destructive = true)
    {
        bool Show()
        {
            var owner = System.Windows.Application.Current?.MainWindow;
            var result = owner is { IsLoaded: true }
                ? MessageBox.Show(owner, message, title, MessageBoxButton.OKCancel,
                    destructive ? MessageBoxImage.Warning : MessageBoxImage.Question,
                    // Safe default for a destructive prompt is cancel (matches WpfDialogService).
                    destructive ? MessageBoxResult.Cancel : MessageBoxResult.OK)
                : MessageBox.Show(message, title, MessageBoxButton.OKCancel,
                    destructive ? MessageBoxImage.Warning : MessageBoxImage.Question,
                    destructive ? MessageBoxResult.Cancel : MessageBoxResult.OK);
            var confirmed = result == MessageBoxResult.OK;
            _logger.LogInformation("Confirm dialog '{Title}' → {Result}", title, confirmed ? "confirmed" : "cancelled");
            return confirmed;
        }

        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null || dispatcher.CheckAccess())
            return Task.FromResult(Show());
        return dispatcher.InvokeAsync(Show).Task;
    }

    public void NotifyError(string message)
    {
        // Same routing as WpfDialogService: an error notice is a toast, and the
        // shell's toast sink (ToastLogSink) drains it to the log.
        _logger.LogError("Dialog error notice: {Message}", message);
        ToastService.Instance.Show(ToastKind.Error, "dialog.error", message);
    }
}
