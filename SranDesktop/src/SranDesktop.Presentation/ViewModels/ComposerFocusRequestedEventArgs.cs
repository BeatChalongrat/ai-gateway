namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// Args for <see cref="ChatViewModel.ComposerFocusRequested"/>.
/// <see cref="OnlyIfIdle"/> marks the turn-completion refocus: the view must
/// focus the composer only when the user's keyboard focus is not already
/// somewhere useful (composer itself, another editable control, or a modal
/// dialog — see ComposerControl.OnComposerFocusRequested). The pre-existing
/// callers (edit-message, suggestion chips, New chat) keep raising with
/// EventArgs.Empty and focus unconditionally.
/// </summary>
public sealed class ComposerFocusRequestedEventArgs : EventArgs
{
    public static readonly ComposerFocusRequestedEventArgs OnlyWhenIdle =
        new() { OnlyIfIdle = true };

    public bool OnlyIfIdle { get; init; }
}
