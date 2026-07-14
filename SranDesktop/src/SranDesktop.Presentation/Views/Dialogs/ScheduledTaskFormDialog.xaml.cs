using System.Windows;
using System.Windows.Controls;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>Create/edit form for a scheduled task (R9 G3).</summary>
public partial class ScheduledTaskFormDialog : Window
{
    public string TaskName => NameBox.Text.Trim();
    public string TaskPrompt => PromptBox.Text.Trim();
    public string CronExpression => CronBox.Text.Trim();

    public ScheduledTaskFormDialog()
    {
        InitializeComponent();
        Helpers.DarkTitleBarHelper.Attach(this);
        NameBox.Focus();
        UpdateValidity();
    }

    /// <summary>Edit mode.</summary>
    public ScheduledTaskFormDialog(string name, string prompt, string cron) : this()
    {
        NameBox.Text = name;
        PromptBox.Text = prompt;
        CronBox.Text = cron;
        // Select the matching preset, else "custom".
        foreach (var item in PresetBox.Items.OfType<ComboBoxItem>())
        {
            if (item.Tag as string == cron)
            {
                PresetBox.SelectedItem = item;
                return;
            }
        }
        PresetBox.SelectedIndex = PresetBox.Items.Count - 1;
    }

    private void Preset_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (CronBox is null) return; // fires during InitializeComponent
        if (PresetBox.SelectedItem is ComboBoxItem { Tag: string cron } && cron.Length > 0)
            CronBox.Text = cron;
        UpdateValidity();
    }

    private void Fields_Changed(object sender, TextChangedEventArgs e) => UpdateValidity();

    private void UpdateValidity()
    {
        if (SaveButton is null || CronHint is null) return;

        var next = Application.Services.CronHelper.NextOccurrence(CronExpression);
        CronHint.Text = next is { } n
            ? $"{LocaleService.Instance.T("task.nextRun")}: {n.LocalDateTime:g}"
            : "Invalid cron expression";
        SaveButton.IsEnabled = next is not null
            && TaskName.Length > 0
            && TaskPrompt.Length > 0;
    }

    private void Save_Click(object sender, RoutedEventArgs e) => DialogResult = true;
}
