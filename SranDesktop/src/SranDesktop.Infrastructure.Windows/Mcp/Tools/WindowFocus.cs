namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Moved verbatim from the portable <c>ToolHelpers.FocusWindowForInputAsync</c>
/// (Infrastructure split): it was the one Win32-bound method in an otherwise
/// portable helper class. Same body, same callers (computer_type / computer_key).
/// </summary>
internal static class WindowFocus
{
    /// <summary>Brings a window to the foreground before keyboard injection and
    /// VERIFIES it — typing into the wrong window is the worst computer-use
    /// failure mode. Returns an error message, or null when focused.</summary>
    public static async Task<string?> FocusWindowForInputAsync(string windowTitle, CancellationToken ct)
    {
        var hWnd = Native.Win32Input.FindWindowByTitle(windowTitle);
        if (hWnd == IntPtr.Zero)
            return $"No visible window with a title containing '{windowTitle}'.";

        var focused = await Task.Run(() => Native.Win32Input.ForceForegroundWindow(hWnd), ct);
        await Task.Delay(150, ct); // let the activation settle before keystrokes
        if (!focused || Native.Win32Input.GetForegroundWindow() != hWnd)
            return $"Could not bring '{windowTitle}' to the foreground — it may belong to an " +
                   "elevated (administrator) process, which cannot receive our input. " +
                   "Input was NOT sent.";
        return null;
    }
}
