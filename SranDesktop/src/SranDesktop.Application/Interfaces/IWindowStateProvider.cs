namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Answers "is the main window the thing the user is looking at right now?" —
/// the gate for the OS-level completion notification. Abstracted so ViewModels
/// (and tests) never touch WPF window state directly.
/// </summary>
public interface IWindowStateProvider
{
    /// <summary>True only when the main window is visible, not minimized AND
    /// foreground-active. Hidden-to-tray / minimized / background all count as
    /// inactive.</summary>
    bool IsWindowActive { get; }
}
