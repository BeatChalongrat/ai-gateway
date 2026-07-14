using System.Windows;
using System.Windows.Input;
using SranDesktop.Presentation.Helpers;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

/// <summary>Project workspace dialog (R9 Feature B5). Saves on close.</summary>
public partial class ProjectView : Window
{
    private readonly ProjectDetailViewModel _viewModel;
    private bool _deleted;

    public ProjectView(ProjectDetailViewModel viewModel)
    {
        InitializeComponent();
        DarkTitleBarHelper.Attach(this);
        _viewModel = viewModel;
        DataContext = viewModel;
        viewModel.ProjectDeleted += (_, _) => { _deleted = true; Close(); };
        Closed += async (_, _) =>
        {
            if (!_deleted) // saving after delete would resurrect the project
                await _viewModel.SaveAsync();
        };
    }

    private void Window_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            e.Handled = true;
            Close();
        }
    }
}
