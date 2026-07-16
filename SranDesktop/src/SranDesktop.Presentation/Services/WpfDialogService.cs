using System.Threading.Tasks;
using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Views.Dialogs;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// WPF implementation of <see cref="IDialogService"/> (Phase C.5): shows the
/// themed <see cref="ConfirmDialog"/> on the UI thread and routes error notices
/// to the toast system (a non-blocking error toast is more consistent with the
/// rest of the app than a modal). ViewModels depend only on the interface.
/// </summary>
public sealed class WpfDialogService : IDialogService
{
    public Task<bool> ConfirmAsync(string title, string message, bool destructive = true)
    {
        bool ShowDialog() => ConfirmDialog.Show(ActiveOwner(), title, message, destructive);

        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null || dispatcher.CheckAccess())
            return Task.FromResult(ShowDialog());
        return dispatcher.InvokeAsync(ShowDialog).Task;
    }

    public void NotifyError(string message) =>
        ToastService.Instance.Show(ToastKind.Error, "dialog.error", message);

    private static Window? ActiveOwner()
    {
        var app = System.Windows.Application.Current;
        if (app is null)
            return null;
        foreach (Window w in app.Windows)
            if (w.IsActive)
                return w;
        return app.MainWindow;
    }
}
