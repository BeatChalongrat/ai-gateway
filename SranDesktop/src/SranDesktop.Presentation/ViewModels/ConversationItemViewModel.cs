using CommunityToolkit.Mvvm.ComponentModel;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

public partial class ConversationItemViewModel : ObservableObject
{
    public string Id { get; }
    public ConversationSource Source { get; }
    public DateTimeOffset CreatedAt { get; }
    public string? ModelId { get; set; }

    /// <summary>Server mirror id of a local agent conversation (Phase 3 sync).
    /// Null for unsynced local chats and for server-source rows (their Id IS
    /// the server id). Used to map server-side search hits back (Phase 5).</summary>
    public string? ServerId { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(DisplayTitle))]
    private string? _title;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(GroupKey))]
    [NotifyPropertyChangedFor(nameof(GroupRank))]
    private bool _isPinned;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(GroupKey))]
    [NotifyPropertyChangedFor(nameof(GroupRank))]
    private DateTimeOffset _updatedAt;

    /// <summary>Project membership (server-side chat.projects). Projected chats render
    /// inside their project section instead of the time buckets.</summary>
    [ObservableProperty]
    private string? _projectId;

    /// <summary>Phase 14 — archived chats leave the main sidebar and appear
    /// only in the archive view. Not a delete: the chat still opens.</summary>
    [ObservableProperty]
    private bool _isArchived;

    /// <summary>Selection is VM-authoritative (maintained by ConversationListViewModel)
    /// because rows live in several ItemsControls, not one Selector.</summary>
    [ObservableProperty]
    private bool _isSelected;

    /// <summary>True while the row title is an inline-edit TextBox (web-style rename).</summary>
    [ObservableProperty]
    private bool _isRenaming;

    /// <summary>Scratch text bound to the inline-rename TextBox.</summary>
    [ObservableProperty]
    private string _editingName = string.Empty;

    public string DisplayTitle => string.IsNullOrWhiteSpace(Title) ? "New chat" : Title!;

    public string GroupKey => LocaleService.Instance.T(
        IsPinned ? "sidebar.group.pinned" : DateBucket(UpdatedAt).LabelKey);

    public int GroupRank => IsPinned ? 0 : DateBucket(UpdatedAt).Rank;

    public ConversationItemViewModel(Conversation entity)
    {
        Id = entity.Id;
        Source = entity.Source;
        CreatedAt = entity.CreatedAt;
        ModelId = entity.ModelId;
        ServerId = entity.ServerId;
        _title = entity.Title;
        _isPinned = entity.IsPinned;
        _updatedAt = entity.UpdatedAt;
        _projectId = entity.ProjectId;
        _isArchived = entity.IsArchived;
    }

    public Conversation ToEntity() => new()
    {
        Id = Id,
        Title = Title,
        ModelId = ModelId,
        CreatedAt = CreatedAt,
        UpdatedAt = UpdatedAt,
        IsPinned = IsPinned,
        IsArchived = IsArchived,
        ProjectId = ProjectId,
        Source = Source,
        ServerId = ServerId
    };

    private static (string LabelKey, int Rank) DateBucket(DateTimeOffset timestamp)
    {
        var today = DateTime.Today;
        var date = timestamp.ToLocalTime().Date;

        // Web grouping: Today / Previous 30 days / Older (yesterday folds in).
        if (date >= today) return ("sidebar.group.today", 1);
        if (date >= today.AddDays(-30)) return ("sidebar.group.last30", 2);
        return ("sidebar.group.older", 3);
    }
}
