using System.Windows;
using System.Windows.Controls;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>Create/edit form for a memory (R7 E3).</summary>
public partial class MemoryFormDialog : Window
{
    public string MemoryContent => ContentBox.Text.Trim();

    /// <summary>Server value: explicit_fact | response_preference | ongoing_interest | confirmed_instruction.</summary>
    public string MemoryType =>
        (TypeBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "explicit_fact";

    public string? Category =>
        string.IsNullOrWhiteSpace(CategoryBox.Text) ? null : CategoryBox.Text.Trim();

    public MemoryFormDialog()
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        ContentBox.Focus();
    }

    /// <summary>Edit mode: prefill fields and switch the title.</summary>
    public MemoryFormDialog(string content, string memoryType, string? category) : this()
    {
        Title = LocaleService.Instance.T("memory.form.titleEdit");
        ContentBox.Text = content;
        CategoryBox.Text = category ?? "";
        foreach (var item in TypeBox.Items.OfType<ComboBoxItem>())
        {
            if (item.Tag as string == memoryType
                // inferred_preference edits save back as response_preference
                || (memoryType == "inferred_preference" && item.Tag as string == "response_preference"))
            {
                TypeBox.SelectedItem = item;
                break;
            }
        }
        ContentBox.CaretIndex = ContentBox.Text.Length;
    }

    private void ContentBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        CounterText.Text = $"{ContentBox.Text.Length} / 1500";
        SaveButton.IsEnabled = ContentBox.Text.Trim().Length > 0;
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        if (MemoryContent.Length == 0)
            return;
        DialogResult = true;
    }
}
