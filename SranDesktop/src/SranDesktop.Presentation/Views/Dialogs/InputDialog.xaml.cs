using System.Windows;
using System.Windows.Input;

namespace SranDesktop.Presentation.Views.Dialogs;

public partial class InputDialog : Window
{
    public InputDialog()
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
    }

    /// <summary>Shows a single-value prompt. Returns the entered text, or null if cancelled.</summary>
    public static string? Show(Window? owner, string title, string prompt, string initialValue = "")
    {
        var dialog = new InputDialog
        {
            Owner = owner,
            Title = title
        };
        dialog.PromptText.Text = prompt;
        dialog.ValueBox.Text = initialValue;
        dialog.ValueBox.SelectAll();
        dialog.ValueBox.Focus();

        return dialog.ShowDialog() == true ? dialog.ValueBox.Text : null;
    }

    private void Ok_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = true;
    }

    private void ValueBox_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
            DialogResult = true;
    }
}
