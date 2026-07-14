using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Models;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// A sidebar chat folder. Wraps the server FolderDto and exposes a live-filtered
/// view over the shared conversation collection (members = FolderId match).
/// </summary>
public partial class FolderItemViewModel : ObservableObject
{
    public string Id { get; }

    public int Position { get; }

    [ObservableProperty]
    private string _name;

    [ObservableProperty]
    private bool _isExpanded = true;

    /// <summary>True while the folder name is an inline-edit TextBox (web-style rename).</summary>
    [ObservableProperty]
    private bool _isRenaming;

    /// <summary>Scratch text bound to the inline-rename TextBox.</summary>
    [ObservableProperty]
    private string _editingName = string.Empty;

    /// <summary>True while a dragged conversation hovers this folder header
    /// (Phase 8 drag-drop) — drives the drop highlight.</summary>
    [ObservableProperty]
    private bool _isDropTarget;

    /// <summary>Conversations filed in this folder (live-filtered, newest first).</summary>
    public ICollectionView Conversations { get; }

    public FolderItemViewModel(
        FolderDto dto,
        ObservableCollection<ConversationItemViewModel> allConversations,
        Func<ConversationItemViewModel, bool> searchMatch)
    {
        Id = dto.Id;
        Position = dto.Position;
        _name = string.IsNullOrWhiteSpace(dto.Name) ? "Untitled folder" : dto.Name!;

        var view = new ListCollectionView(allConversations)
        {
            Filter = o => o is ConversationItemViewModel c && c.FolderId == Id && searchMatch(c),
            IsLiveFiltering = true,
            IsLiveSorting = true
        };
        view.LiveFilteringProperties.Add(nameof(ConversationItemViewModel.FolderId));
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
