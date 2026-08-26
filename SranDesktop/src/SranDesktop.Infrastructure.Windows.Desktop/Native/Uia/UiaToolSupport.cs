using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace SranDesktop.Infrastructure.Native.Uia;

/// <summary>Shared plumbing for the uia_* tools: timeout, elevated-process
/// detection, and window-handle lookup. UIA calls run in the TARGET process —
/// they can hang (unresponsive app) or be denied (UIPI: an elevated window is
/// invisible to a non-elevated client), so every entry point is bounded and
/// every failure mapped to an explanation the model can act on.</summary>
internal static class UiaToolSupport
{
    internal static readonly TimeSpan UiaTimeout = TimeSpan.FromSeconds(5);

    internal const string TimeoutError =
        "UI Automation timed out — the application may be unresponsive. " +
        "Fall back to computer_screenshot + computer_click.";

    internal static bool IsAccessDenied(Exception ex) =>
        ex is UnauthorizedAccessException
        || (ex is COMException com && (uint)com.HResult == 0x80070005);

    internal static string Describe(Exception ex) => IsAccessDenied(ex)
        ? "Access denied — the target window belongs to an elevated (administrator) process, " +
          "which UI Automation cannot reach from this app. Ask the user to interact with it directly."
        : ex.Message;

    /// <summary>Nearest ancestor (or self) with a real HWND — the window to
    /// focus before a physical fallback click.</summary>
    internal static IntPtr WindowHandleOf(AutomationElement element)
    {
        var walker = TreeWalker.ControlViewWalker;
        var current = element;
        for (var depth = 0; current is not null && depth < 50; depth++)
        {
            var handle = current.Current.NativeWindowHandle;
            if (handle != 0)
                return new IntPtr(handle);
            current = walker.GetParent(current);
        }
        return IntPtr.Zero;
    }
}
