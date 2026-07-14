using System.Windows;
using System.Windows.Input;
using SranDesktop.Presentation.Helpers;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

/// <summary>Skill management dialog (R9 Feature H).</summary>
public partial class SkillsView : Window
{
    public SkillsView(SkillsViewModel viewModel)
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
