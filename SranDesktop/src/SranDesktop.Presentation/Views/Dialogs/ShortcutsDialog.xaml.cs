using System.Windows;

namespace SranDesktop.Presentation.Views.Dialogs;

public partial class ShortcutsDialog : Window
{
    public ShortcutsDialog()
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
    }
}
