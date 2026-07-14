using System.Windows;
using System.Windows.Input;
using SranDesktop.Presentation.Helpers;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

/// <summary>In-app knowledge base management dialog (R7 Feature D).</summary>
public partial class KnowledgeBaseView : Window
{
    public KnowledgeBaseView(KnowledgeBaseViewModel viewModel)
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
