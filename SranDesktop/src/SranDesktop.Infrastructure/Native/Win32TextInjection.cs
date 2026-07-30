using System.Runtime.InteropServices;
using System.Text;

namespace SranDesktop.Infrastructure.Native;

/// <summary>
/// Text-into-control primitives for the uia_set_value injection ladder (T3 PART 1).
/// These complement SendInput (Win32Input): clipboard paste and window-message
/// injection (EM_REPLACESEL / WM_SETTEXT) reach RichEdit-class controls — notably
/// Windows 11 Notepad (RichEditD2DPT) — that ignore UIA ValuePattern.SetValue, and
/// WM_GETTEXT reads the control back for HWND-bound verification even when UIA
/// TextPattern returns nothing. Every method targets a specific control HWND, so it
/// can never land in the wrong window.
/// </summary>
internal static class Win32TextInjection
{
    private const uint WM_SETTEXT = 0x000C;
    private const uint WM_GETTEXT = 0x000D;
    private const uint WM_GETTEXTLENGTH = 0x000E;
    private const uint WM_PASTE = 0x0302;
    private const uint EM_SETSEL = 0x00B1;
    private const uint EM_REPLACESEL = 0x00C2;
    private const uint CF_UNICODETEXT = 13;
    private const uint GMEM_MOVEABLE = 0x0002;

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SendMessageW(IntPtr hWnd, uint msg, IntPtr wParam, string? lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SendMessageW(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessageW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, EnumChildProc callback, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr hWnd, StringBuilder buffer, int maxCount);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    private delegate bool EnumChildProc(IntPtr hWnd, IntPtr lParam);

    // ── Clipboard ───────────────────────────────────────────────────────────
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool OpenClipboard(IntPtr hWndNewOwner);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseClipboard();
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EmptyClipboard();
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetClipboardData(uint format);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetClipboardData(uint format, IntPtr hMem);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GlobalLock(IntPtr hMem);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GlobalUnlock(IntPtr hMem);
    [DllImport("kernel32.dll")]
    private static extern IntPtr GlobalSize(IntPtr hMem);

    /// <summary>Class-name fragments that mark an editable text control. Windows 11
    /// Notepad's document is "RichEditD2DPT"; classic edits are "Edit"/"RICHEDIT*".</summary>
    private static readonly string[] EditClassFragments =
    {
        "edit", "richedit", "richeditd2dpt", "textbox"
    };

    /// <summary>Returns the best editable text-input child of <paramref name="root"/>
    /// (the real control that receives text), or <paramref name="root"/> itself when it
    /// already is one / none is found. Prefers a visible RichEdit/Edit descendant.</summary>
    internal static IntPtr FindTextInputChild(IntPtr root)
    {
        if (root == IntPtr.Zero)
            return IntPtr.Zero;
        if (IsEditClass(root))
            return root;

        var best = IntPtr.Zero;
        EnumChildWindows(root, (hWnd, _) =>
        {
            if (IsWindowVisible(hWnd) && IsEditClass(hWnd))
            {
                best = hWnd;
                return false; // stop at the first visible edit control
            }
            return true;
        }, IntPtr.Zero);
        return best == IntPtr.Zero ? root : best;
    }

    internal static string ClassNameOf(IntPtr hWnd)
    {
        var sb = new StringBuilder(256);
        return GetClassName(hWnd, sb, sb.Capacity) > 0 ? sb.ToString() : string.Empty;
    }

    private static bool IsEditClass(IntPtr hWnd) => IsEditClassName(ClassNameOf(hWnd));

    /// <summary>True when a window class name denotes an editable text control
    /// (Edit / RichEdit / RichEditD2DPT / TextBox). Extracted for testing.</summary>
    internal static bool IsEditClassName(string? className)
    {
        if (string.IsNullOrEmpty(className))
            return false;
        var lower = className.ToLowerInvariant();
        return EditClassFragments.Any(lower.Contains);
    }

    /// <summary>Reads the control's text via WM_GETTEXT — an HWND-bound read-back that
    /// works on classic Edit/RichEdit even when UIA gives nothing. May return "" for
    /// controls that don't service WM_GETTEXT (then the caller falls back to observation).</summary>
    internal static string ReadControlText(IntPtr hWnd)
    {
        if (hWnd == IntPtr.Zero)
            return string.Empty;
        var length = (int)SendMessageW(hWnd, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
        if (length <= 0)
            return string.Empty;
        var buffer = new StringBuilder(length + 1);
        SendMessageW(hWnd, WM_GETTEXT, new IntPtr(buffer.Capacity), buffer);
        return buffer.ToString();
    }

    /// <summary>Replaces the control's whole content with <paramref name="text"/> via
    /// EM_SETSEL(0,-1)+EM_REPLACESEL (edit/RichEdit), falling back to WM_SETTEXT. Sends
    /// directly to the control HWND — no foreground stealing, no wrong-window risk.</summary>
    internal static bool ReplaceControlText(IntPtr hWnd, string text)
    {
        if (hWnd == IntPtr.Zero)
            return false;
        // Select all, then replace the selection with the new text.
        SendMessageW(hWnd, EM_SETSEL, IntPtr.Zero, new IntPtr(-1));
        SendMessageW(hWnd, EM_REPLACESEL, new IntPtr(1), text);
        // WM_SETTEXT as a second attempt for controls that ignore EM_REPLACESEL.
        SendMessageW(hWnd, WM_SETTEXT, IntPtr.Zero, text);
        return true;
    }

    /// <summary>Posts WM_PASTE to the control (used after the clipboard holds the text).</summary>
    internal static void PasteMessage(IntPtr hWnd)
    {
        if (hWnd != IntPtr.Zero)
            SendMessageW(hWnd, WM_PASTE, IntPtr.Zero, IntPtr.Zero);
    }

    /// <summary>Sets the Unicode clipboard text, returning the previous text so the
    /// caller can restore it. Returns (ok, previous). Best-effort — clipboard access can
    /// fail if another process holds it.</summary>
    internal static (bool Ok, string? Previous) SetClipboardText(string text)
    {
        var previous = GetClipboardTextInternal();
        if (!OpenClipboard(IntPtr.Zero))
            return (false, previous);
        try
        {
            EmptyClipboard();
            var bytes = (UIntPtr)((text.Length + 1) * 2);
            var hMem = GlobalAlloc(GMEM_MOVEABLE, bytes);
            if (hMem == IntPtr.Zero)
                return (false, previous);
            var target = GlobalLock(hMem);
            if (target == IntPtr.Zero)
                return (false, previous);
            try
            {
                var raw = Encoding.Unicode.GetBytes(text + '\0');
                Marshal.Copy(raw, 0, target, raw.Length);
            }
            finally
            {
                GlobalUnlock(hMem);
            }
            return (SetClipboardData(CF_UNICODETEXT, hMem) != IntPtr.Zero, previous);
        }
        finally
        {
            CloseClipboard();
        }
    }

    /// <summary>Restores clipboard text saved by <see cref="SetClipboardText"/>.</summary>
    internal static void RestoreClipboardText(string? previous)
    {
        if (previous is null)
            return;
        SetClipboardText(previous);
    }

    private static string? GetClipboardTextInternal()
    {
        if (!OpenClipboard(IntPtr.Zero))
            return null;
        try
        {
            var handle = GetClipboardData(CF_UNICODETEXT);
            if (handle == IntPtr.Zero)
                return null;
            var ptr = GlobalLock(handle);
            if (ptr == IntPtr.Zero)
                return null;
            try
            {
                return Marshal.PtrToStringUni(ptr);
            }
            finally
            {
                GlobalUnlock(handle);
            }
        }
        finally
        {
            CloseClipboard();
        }
    }
}
