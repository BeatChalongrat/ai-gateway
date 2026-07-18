using System.Windows;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>"Add custom connector" dialog (custom-connector UI). All logic
/// lives in <see cref="AddCustomConnectorViewModel"/>; this code-behind only
/// bridges the PasswordBox (no bindable Password by design) and the
/// close-on-request handshake.</summary>
public partial class AddCustomConnectorDialog : Window
{
    public AddCustomConnectorDialog(AddCustomConnectorViewModel viewModel)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        DataContext = viewModel;
        viewModel.RequestClose += (_, added) =>
        {
            DialogResult = added;
        };
        NameBox.Focus();
    }

    private void ApiKeyBox_PasswordChanged(object sender, RoutedEventArgs e)
    {
        if (DataContext is AddCustomConnectorViewModel vm)
            vm.ApiKey = ApiKeyBox.Password;
    }
}
