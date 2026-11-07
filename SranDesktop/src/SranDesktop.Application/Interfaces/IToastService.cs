namespace SranDesktop.Application.Interfaces;

public enum ToastKind
{
    Info,
    Success,
    Error
}

/// <summary>
/// App-wide transient notifications (Phase 8 Task 1). Messages are passed as
/// LOCALIZATION KEYS — the UI host resolves them through the active locale at
/// display time, so non-Presentation layers (coordinator, sync service) can
/// raise toasts without referencing the dictionary.
/// </summary>
public interface IToastService
{
    /// <param name="messageKey">Dict key (e.g. "toast.copied").</param>
    /// <param name="detail">Optional literal appended after the localized text
    /// (file name, connector name) — never localized.</param>
    /// <param name="duration">Override; defaults 3 s (info/success), 6 s (error).</param>
    void Show(ToastKind kind, string messageKey, string? detail = null, TimeSpan? duration = null);
}
