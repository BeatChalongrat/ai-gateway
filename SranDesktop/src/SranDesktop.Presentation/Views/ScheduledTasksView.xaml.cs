using System.Windows;
using System.Windows.Input;
using SranDesktop.Presentation.Helpers;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

/// <summary>Scheduled task management dialog (R9 Feature G).</summary>
public partial class ScheduledTasksView : Window
{
    public ScheduledTasksView(ScheduledTasksViewModel viewModel)
    {
        InitializeComponent();
        DarkTitleBarHelper.Attach(this);
        DataContext = viewModel;
        Loaded += async (_, _) => await viewModel.LoadAsync();
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
