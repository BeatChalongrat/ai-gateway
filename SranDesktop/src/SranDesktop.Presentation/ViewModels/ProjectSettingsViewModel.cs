using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One knowledge base as a picker row (including the "None" entry).</summary>
public sealed record KnowledgeBaseChoice(string? Id, string Name)
{
    public override string ToString() => Name;
}

/// <summary>
/// Project settings — shared instructions + knowledge base (Phase 12).
///
/// Parity with the web Projects page (apps/web-chat/src/app/chat/projects):
/// SERVER-backed through the same endpoints (PUT /projects/{id} and
/// PUT /projects/{id}/knowledge-base), so a change made here shows up on web
/// and vice versa. Phase 11 retired the desktop's old LOCAL project store;
/// this is what replaces the editing it used to offer, on the shared data.
///
/// Deliberately NOT a re-implementation of the contract: the 8000-character
/// instructions cap and the one-KB-per-project rule are the server's, and the
/// only thing enforced client-side is the same cap for a friendly counter.
/// </summary>
public partial class ProjectSettingsViewModel : ObservableObject
{
    /// <summary>Mirrors the server's maxProjectInstructionsLen so the counter
    /// turns red before a save can fail, not after.</summary>
    public const int MaxInstructions = 8000;

    private readonly IChatEdgeApiClient _api;
    private readonly ILogger _logger;
    private readonly string _projectId;

    public ProjectSettingsViewModel(
        IChatEdgeApiClient api, ILogger logger, ProjectDto project)
    {
        _api = api;
        _logger = logger;
        _projectId = project.Id;
        ProjectName = string.IsNullOrWhiteSpace(project.Name) ? "Project" : project.Name!;
        _instructions = project.Instructions ?? string.Empty;
        _selectedKnowledgeBase = null;
        _initialKnowledgeBaseId = project.KnowledgeBaseId;
    }

    public string ProjectName { get; }

    private readonly string? _initialKnowledgeBaseId;

    [ObservableProperty]
    private string _instructions;

    [ObservableProperty]
    private KnowledgeBaseChoice? _selectedKnowledgeBase;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanSave))]
    private bool _isBusy;

    /// <summary>Save is disabled while a request is in flight so a double
    /// click cannot fire two PUTs.</summary>
    public bool CanSave => !IsBusy;

    [ObservableProperty]
    private string? _statusMessage;

    /// <summary>True once a save succeeded — the caller refreshes its list.</summary>
    public bool Saved { get; private set; }

    public System.Collections.ObjectModel.ObservableCollection<KnowledgeBaseChoice> KnowledgeBases { get; } = new();

    public int InstructionsLength => Instructions?.Length ?? 0;

    public bool IsTooLong => InstructionsLength > MaxInstructions;

    partial void OnInstructionsChanged(string value)
    {
        OnPropertyChanged(nameof(InstructionsLength));
        OnPropertyChanged(nameof(IsTooLong));
    }

    /// <summary>Loads the KB list and preselects the project's current one.
    /// A failure here leaves the instructions editable — the two halves of
    /// this dialog are independent, and losing the KB list must not block
    /// someone who came to edit text.</summary>
    public async Task LoadAsync()
    {
        KnowledgeBases.Clear();
        KnowledgeBases.Add(new KnowledgeBaseChoice(null, LocaleService.Instance.T("project.kb.none")));
        try
        {
            var response = await _api.ListKnowledgeBasesAsync();
            foreach (var kb in response.KnowledgeBases)
                KnowledgeBases.Add(new KnowledgeBaseChoice(kb.Id, kb.Name ?? kb.Id));
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not load knowledge bases for the project dialog");
            StatusMessage = LocaleService.Instance.T("project.kb.loadFailed");
        }

        SelectedKnowledgeBase =
            KnowledgeBases.FirstOrDefault(k => k.Id == _initialKnowledgeBaseId)
            ?? KnowledgeBases[0];
    }

    /// <summary>Saves both halves. Instructions first: if the KB call fails
    /// the text the user typed is already safe, which is the half they are
    /// most likely to have spent effort on.</summary>
    [RelayCommand]
    private async Task SaveAsync()
    {
        if (IsBusy) return;
        if (IsTooLong)
        {
            StatusMessage = LocaleService.Instance.T("project.instructions.tooLong");
            return;
        }

        IsBusy = true;
        StatusMessage = null;
        try
        {
            await _api.UpdateProjectInstructionsAsync(_projectId, Instructions ?? string.Empty);

            var nextKb = SelectedKnowledgeBase?.Id;
            if (nextKb != _initialKnowledgeBaseId)
                await _api.SetProjectKnowledgeBaseAsync(_projectId, nextKb);

            Saved = true;
            CloseRequested?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to save project {Id}", _projectId);
            StatusMessage = LocaleService.Instance.T("project.saveFailed");
        }
        finally
        {
            IsBusy = false;
        }
    }

    public event EventHandler? CloseRequested;
}
