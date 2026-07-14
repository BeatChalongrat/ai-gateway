using CommunityToolkit.Mvvm.ComponentModel;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// One full-text hit in the sidebar's "In messages" section (Phase 5 Task 5).
/// The server sends no message id, so a hit can only OPEN its conversation.
/// Phase 8 Task 6: a hit whose conversation exists only server-side is shown
/// too (<see cref="RequiresPull"/>) — clicking pulls it locally first, with
/// <see cref="IsPulling"/> driving the row's loading affordance.
/// </summary>
public sealed partial class MessageSearchHitViewModel : ObservableObject
{
    /// <summary>Local conversation id, or the SERVER conversation id when
    /// <see cref="RequiresPull"/> is true.</summary>
    public string ConversationId { get; }

    public bool RequiresPull { get; }

    public string Title { get; }
    public string Snippet { get; }

    /// <summary>The shared row chrome binds IsSelected; hits are never selected.</summary>
    public bool IsSelected => false;

    [ObservableProperty]
    private bool _isPulling;

    public MessageSearchHitViewModel(
        string conversationId, string title, string snippet, bool requiresPull = false)
    {
        ConversationId = conversationId;
        Title = title;
        Snippet = snippet;
        RequiresPull = requiresPull;
    }
}
