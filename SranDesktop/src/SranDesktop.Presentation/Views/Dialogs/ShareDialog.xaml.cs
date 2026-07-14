using System.Windows;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>Share-link dialog: copy the URL or revoke sharing (R9 K3).</summary>
public partial class ShareDialog : Window
{
    private readonly Func<Task>? _revoke;

    public ShareDialog(string shareUrl, Func<Task>? revoke = null)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        UrlBox.Text = shareUrl;
        _revoke = revoke;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == System.Windows.Input.Key.Escape)
                Close();
        };
    }

    private void Copy_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Clipboard.SetText(UrlBox.Text);
            CopyLabel.Text = LocaleService.Instance.T("share.copied");
        }
        catch
        {
            // Clipboard locked — the URL stays selectable in the box.
        }
    }

    private async void Revoke_Click(object sender, RoutedEventArgs e)
    {
        if (_revoke is not null)
        {
            try
            {
                await _revoke();
            }
            catch (Exception)
            {
                // Revoke is best-effort from this dialog.
            }
        }
        Close();
    }
}
