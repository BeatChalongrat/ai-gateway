using System.Collections.ObjectModel;
using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Detail editor for one project (R9 Feature B5).</summary>
public partial class ProjectDetailViewModel : ObservableObject
{
    private readonly IProjectStore _store;
    private readonly ILogger _logger;

    public Project Project { get; }

    [ObservableProperty] private string _name;
    [ObservableProperty] private string _instructions;

    public ObservableCollection<string> Files { get; } = new();
    public ObservableCollection<ConversationItemViewModel> Conversations { get; } = new();

    /// <summary>MainViewModel hooks this to create + open a chat inside the project.</summary>
    public Func<Project, Task>? StartChatRequested { get; set; }

    /// <summary>MainViewModel hooks this to refresh the sidebar after delete.</summary>
    public event EventHandler? ProjectDeleted;

    public ProjectDetailViewModel(IProjectStore store, Project project, ILogger logger)
    {
        _store = store;
        Project = project;
        _logger = logger;
        _name = project.Name;
        _instructions = project.Instructions ?? "";
        foreach (var file in project.Files)
            Files.Add(file);
    }

    /// <summary>Persist name/instructions (called on dialog close).</summary>
    public async Task SaveAsync()
    {
        Project.Name = Name.Trim();
        Project.Instructions = string.IsNullOrWhiteSpace(Instructions) ? null : Instructions.Trim();
        await _store.UpsertAsync(Project);
    }

    [RelayCommand]
    private async Task AddFileAsync()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Multiselect = true,
            Title = LocaleService.Instance.T("project.addFile")
        };
        if (dialog.ShowDialog() != true)
            return;

        foreach (var path in dialog.FileNames)
        {
            try
            {
                var stored = await _store.AddFileAsync(Project, path);
                if (!Files.Contains(stored))
                    Files.Add(stored);
            }
            catch (IOException ex)
            {
                _logger.LogWarning(ex, "Project file add failed for {Path}", path);
            }
        }
    }

    [RelayCommand]
    private async Task RemoveFileAsync(string? storedPath)
    {
        if (storedPath is null)
            return;
        await _store.RemoveFileAsync(Project, storedPath);
        Files.Remove(storedPath);
    }

    [RelayCommand]
    private async Task StartChatAsync()
    {
        await SaveAsync();
        if (StartChatRequested is not null)
            await StartChatRequested(Project);
    }

    [RelayCommand]
    private async Task DeleteAsync()
    {
        var confirmed = System.Windows.MessageBox.Show(
            LocaleService.Instance.T("project.deleteConfirm", Name),
            LocaleService.Instance.T("project.delete"),
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning) == System.Windows.MessageBoxResult.Yes;
        if (!confirmed)
            return;

        await _store.DeleteAsync(Project.Id);
        ProjectDeleted?.Invoke(this, EventArgs.Empty);
    }
}
