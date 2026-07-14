using System.Windows;
using System.Windows.Input;
using SranDesktop.Presentation.Helpers;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

/// <summary>"Learn my style" dialog (R9 Feature L).</summary>
public partial class WritingStyleView : Window
{
    public WritingStyleView(WritingStyleViewModel viewModel)
    {
        InitializeComponent();
        DarkTitleBarHelper.Attach(this);
        DataContext = viewModel;
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
