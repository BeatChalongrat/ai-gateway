using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

public partial class FirstRunDialog : Window
{
    private readonly FirstRunViewModel _viewModel;

    public FirstRunDialog(ISettingsService settings, IStartupManager startupManager)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        _viewModel = new FirstRunViewModel(settings, startupManager);
        DataContext = _viewModel;
        // Esc = accept defaults and continue (same as Get Started).
        PreviewKeyDown += (_, e) =>
        {
            if (e.Key == System.Windows.Input.Key.Escape)
            {
                e.Handled = true;
                GetStarted_Click(this, new RoutedEventArgs());
            }
        };
    }

    private void GetStarted_Click(object sender, RoutedEventArgs e)
    {
        _viewModel.Commit();
        DialogResult = true;
    }
}
