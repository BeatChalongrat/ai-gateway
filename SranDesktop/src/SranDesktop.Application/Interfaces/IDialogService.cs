namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Abstracts modal confirmation + error notices so ViewModels don't reference
/// System.Windows.MessageBox (Phase C.5). Themed in the Presentation
/// implementation; mockable in tests so a delete flow can be verified to gate on
/// confirmation without a real dialog.
/// </summary>
public interface IDialogService
{
    /// <summary>Shows a themed confirmation. Returns true when the user confirms.
    /// <paramref name="destructive"/> styles the confirm button red and labels it
    /// "Delete"; the safe default (Enter/Esc) is cancel for a destructive prompt.</summary>
    Task<bool> ConfirmAsync(string title, string message, bool destructive = true);

    /// <summary>Shows a non-blocking error notice (routed to the toast system).</summary>
    void NotifyError(string message);
}
