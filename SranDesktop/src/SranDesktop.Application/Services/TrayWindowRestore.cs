namespace SranDesktop.Application.Services;

/// <summary>
/// Decides how to re-apply window state when the main window comes back
/// from the tray (UX polish #2 follow-up — the "maximized comes back
/// small / maximize needs two clicks" bug).
///
/// The WPF quirk this exists for: Show() on a window that was HIDDEN
/// while maximized re-displays it in its normal rectangle, but the
/// WindowState property still says Maximized. The stale property is
/// both symptoms at once — the window LOOKS normal (A), and the
/// chrome's maximize button toggles the property away from Maximized
/// on the first click without any visual change (B). The cure is to
/// force the property through Normal (making it truthful) and then
/// re-apply Maximized when that is what the user had before hiding.
///
/// Pure — the App layer captures the inputs and applies the plan — so
/// the sequencing rules are unit-testable without a WPF session.
/// </summary>
public static class TrayWindowRestore
{
    /// <summary>What ShowMainWindow must do right after Show().</summary>
    /// <param name="ForceNormalFirst">Set WindowState = Normal first —
    /// resyncs the stale property with the actual visual.</param>
    /// <param name="ThenMaximize">Then set WindowState = Maximized —
    /// restores what the user had before the window was hidden.</param>
    public readonly record struct Plan(bool ForceNormalFirst, bool ThenMaximize);

    /// <param name="wasHidden">Window was NOT visible before this Show()
    /// (came back from the tray). A window that was merely in the
    /// background must keep its live state untouched.</param>
    /// <param name="wasMaximizedBeforeHide">WindowState captured at the
    /// moment it was hidden.</param>
    /// <param name="isMinimized">Live WindowState == Minimized (the
    /// visible-but-minimized case the old code already handled).</param>
    public static Plan AfterShow(
        bool wasHidden, bool wasMaximizedBeforeHide, bool isMinimized)
    {
        if (wasHidden)
            return new Plan(ForceNormalFirst: true, ThenMaximize: wasMaximizedBeforeHide);

        // Visible all along: only un-minimize; never touch a live
        // maximized window (e.g. a notification click while in front).
        return new Plan(ForceNormalFirst: isMinimized, ThenMaximize: false);
    }
}
