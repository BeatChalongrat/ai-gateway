using System.ComponentModel;
using System.Windows;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

public partial class SettingsView : Window
{
    private readonly SettingsViewModel _viewModel;
    private bool _saved;

    public SettingsView(SettingsViewModel viewModel)
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        _viewModel = viewModel;
        DataContext = viewModel;
        Loaded += async (_, _) => await viewModel.LoadAsync();
        Closing += OnClosing;
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        _viewModel.SaveCommand.Execute(null);
        _saved = true;
        DialogResult = true;
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        // Theme changes preview live; revert if the user cancels.
        if (!_saved)
            _viewModel.RevertTheme();
    }
}
