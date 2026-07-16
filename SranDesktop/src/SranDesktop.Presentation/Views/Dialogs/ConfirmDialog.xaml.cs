using System.Windows;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// Themed replacement for a native MessageBox Yes/No confirmation (Phase C.5).
/// Mirrors <see cref="InputDialog"/>: custom window chrome, theme brushes, dark
/// caption. A destructive prompt styles the confirm button red ("Delete") and
/// leaves cancel as the safe default (no Enter-to-confirm); a neutral prompt
/// makes confirm the default ("OK").
/// </summary>
public partial class ConfirmDialog : Window
{
    public ConfirmDialog()
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
    }

    public static bool Show(Window? owner, string title, string message, bool destructive)
    {
        var dialog = new ConfirmDialog { Owner = owner };
        dialog.TitleText.Text = title;
        dialog.MessageText.Text = message;
        dialog.CancelButton.Content = LocaleService.Instance.T("dialog.cancel");
        dialog.ConfirmButton.Content = LocaleService.Instance.T(
            destructive ? "dialog.delete" : "dialog.confirm");

        if (destructive)
        {
            dialog.ConfirmButton.SetResourceReference(StyleProperty, "DangerButton");
            // Safe default for a destructive action: Esc/Enter cancels; the user
            // must click Delete deliberately.
        }
        else
        {
            dialog.ConfirmButton.SetResourceReference(StyleProperty, "AccentButton");
            dialog.ConfirmButton.IsDefault = true;
        }

        return dialog.ShowDialog() == true;
    }

    private void Confirm_Click(object sender, RoutedEventArgs e) => DialogResult = true;
}
