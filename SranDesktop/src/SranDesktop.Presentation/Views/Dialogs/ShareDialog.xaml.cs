using System.Windows;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>Share-link dialog: copy the URL, pick an expiry, or revoke
/// sharing (R9 K3; expiry added in Batch 2 T3 for web parity).</summary>
public partial class ShareDialog : Window
{
    private readonly Func<Task>? _revoke;
    private readonly Func<string, Task<string>>? _recreate;
    private bool _expiryReady;

    public ShareDialog(
        string shareUrl,
        Func<Task>? revoke = null,
        Func<string, Task<string>>? recreate = null)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        UrlBox.Text = shareUrl;
        _revoke = revoke;
        _recreate = recreate;
        // SelectionChanged fires for the initial IsSelected — only user
        // changes after load should re-create the link.
        _expiryReady = true;
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == System.Windows.Input.Key.Escape)
                Close();
        };
    }

    private async void Expiry_Changed(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (!_expiryReady || _recreate is null || !IsLoaded)
            return;
        var tag = (ExpiryBox.SelectedItem as System.Windows.Controls.ComboBoxItem)?.Tag as string;
        if (string.IsNullOrEmpty(tag))
            return;
        try
        {
            // Server semantics: one link per conversation — re-creating
            // rotates the token, applies the expiry, refreshes the snapshot.
            UrlBox.Text = await _recreate(tag);
            CopyLabel.Text = LocaleService.Instance.T("share.copyLink");
        }
        catch (Exception)
        {
            // Network hiccup — the previous URL stays valid and visible.
        }
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
