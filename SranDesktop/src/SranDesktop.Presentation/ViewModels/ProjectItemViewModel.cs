using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// A sidebar project — the ONE organiser since Phase 9 (chat-edge migration
/// 031 merged folders into projects). Wraps the server ProjectDto and
/// exposes a live-filtered view over the shared conversation collection
/// (members = ProjectId match).
///
/// Instructions / knowledge base are NOT surfaced here: a project used as
/// plain grouping (the migrated folder) must render exactly like the old
/// folder header, and the extra context is edited on the web Projects page.
/// </summary>
public partial class ProjectItemViewModel : ObservableObject
{
    public string Id { get; }

    /// <summary>The server row this item was built from — the settings dialog
    /// prefills instructions + KB from it (Phase 12).</summary>
    public ProjectDto Dto { get; }

    [ObservableProperty]
    private string _name;

    [ObservableProperty]
    private bool _isExpanded = true;

    /// <summary>True while the project name is an inline-edit TextBox (web-style rename).</summary>
    [ObservableProperty]
    private bool _isRenaming;

    /// <summary>Scratch text bound to the inline-rename TextBox.</summary>
    [ObservableProperty]
    private string _editingName = string.Empty;

    /// <summary>True while a dragged conversation hovers this project header
    /// (Phase 8 drag-drop) — drives the drop highlight.</summary>
    [ObservableProperty]
    private bool _isDropTarget;

    /// <summary>Conversations filed in this project (live-filtered, newest first).</summary>
    public ICollectionView Conversations { get; }

    public ProjectItemViewModel(
        ProjectDto dto,
        ObservableCollection<ConversationItemViewModel> allConversations,
        Func<ConversationItemViewModel, bool> searchMatch)
    {
        Id = dto.Id;
        Dto = dto;
        _name = string.IsNullOrWhiteSpace(dto.Name) ? "Untitled project" : dto.Name!;

        var view = new ListCollectionView(allConversations)
        {
            Filter = o => o is ConversationItemViewModel c && c.ProjectId == Id && searchMatch(c),
            IsLiveFiltering = true,
            IsLiveSorting = true
        };
        view.LiveFilteringProperties.Add(nameof(ConversationItemViewModel.ProjectId));
        view.SortDescriptions.Add(new SortDescription(
            nameof(ConversationItemViewModel.UpdatedAt), ListSortDirection.Descending));
        view.LiveSortingProperties.Add(nameof(ConversationItemViewModel.UpdatedAt));
        Conversations = view;
    }

    [RelayCommand]
    private void ToggleExpand() => IsExpanded = !IsExpanded;

    /// <summary>Re-runs the membership/search filter (e.g. after SearchText changes).</summary>
    public void RefreshFilter() => Conversations.Refresh();
}
