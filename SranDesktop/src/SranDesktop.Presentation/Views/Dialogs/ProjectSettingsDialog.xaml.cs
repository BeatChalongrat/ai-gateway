using System.Windows;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

public partial class ProjectSettingsDialog : Window
{
    private readonly ProjectSettingsViewModel _viewModel;

    public ProjectSettingsDialog(ProjectSettingsViewModel viewModel)
    {
        InitializeComponent();
        _viewModel = viewModel;
        DataContext = viewModel;
        // The view model closes the window itself on a successful save; it
        // owns no window reference, so the view supplies the close.
        viewModel.CloseRequested += (_, _) => DialogResult = true;
        Loaded += async (_, _) => await viewModel.LoadAsync();
    }

    /// <summary>True when the project was saved (the caller then refreshes).</summary>
    public bool Saved => _viewModel.Saved;
}
