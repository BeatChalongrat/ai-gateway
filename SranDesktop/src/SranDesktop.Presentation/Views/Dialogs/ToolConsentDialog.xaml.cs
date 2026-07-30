using System.Windows;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

public partial class ToolConsentDialog : Window
{
    /// <summary>The user's decision; closing the dialog any other way means Deny.</summary>
    public ToolConsentResponse Response { get; private set; } = ToolConsentResponse.Deny;

    public ToolConsentDialog(ToolConsentRequest request)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        DataContext = new ToolConsentViewModel(request);
    }

    private void Deny_Click(object sender, RoutedEventArgs e)
    {
        Response = ToolConsentResponse.Deny;
        DialogResult = true;
    }

    private void AllowOnce_Click(object sender, RoutedEventArgs e)
    {
        Response = ToolConsentResponse.Once;
        DialogResult = true;
    }

    private void AllowAlways_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is ToolConsentViewModel { ShowAlwaysAllow: false })
        {
            Response = ToolConsentResponse.Deny;
            DialogResult = true;
            return;
        }
        Response = ToolConsentResponse.Always;
        DialogResult = true;
    }
}
