using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// Shown for unhandled exceptions (R11 A3d): error details collapsed, explicit
/// "Send crash report" (works even when the opt-in toggle is off — an explicit
/// click IS consent), restart, or continue.
/// </summary>
public partial class CrashDialog : Window
{
    private readonly Exception _exception;
    private readonly ICrashReportService? _reporter;

    public CrashDialog(Exception exception, ICrashReportService? reporter)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        _exception = exception;
        _reporter = reporter;
        DetailsText.Text = $"{exception.GetType().FullName}: {exception.Message}\n\n{exception.StackTrace}";
        if (_reporter is null)
            SendButton.IsEnabled = false;
    }

    private async void Send_Click(object sender, RoutedEventArgs e)
    {
        if (_reporter is null)
            return;
        SendButton.IsEnabled = false;
        var ok = await _reporter.ReportAsync(_exception, forceSend: true);
        SendResult.Text = ok
            ? LocaleService.Instance.T("crash.sent")
            : LocaleService.Instance.T("error.serverUnreachable");
        SendResult.Visibility = Visibility.Visible;
    }

    private void Restart_Click(object sender, RoutedEventArgs e)
    {
        var exePath = Environment.ProcessPath;
        if (exePath is not null)
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(exePath) { UseShellExecute = true });
        System.Windows.Application.Current.Shutdown();
    }
}
