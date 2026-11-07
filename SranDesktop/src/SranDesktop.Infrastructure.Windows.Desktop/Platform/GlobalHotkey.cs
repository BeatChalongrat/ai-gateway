using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace SranDesktop.Infrastructure.Platform;

/// <summary>
/// Registers a system-wide hotkey (Ctrl+Shift+Space) via Win32 RegisterHotKey.
/// The window's HWND receives WM_HOTKEY even while hidden.
/// Shell Phase 1: moved here from SranDesktop.App so both hosts can use it.
/// </summary>
public sealed class GlobalHotkey : IDisposable
{
    private const int HotkeyId = 0x1001;
    private const uint MOD_CTRL = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_NOREPEAT = 0x4000;
    private const uint VK_SPACE = 0x20;
    private const int WM_HOTKEY = 0x0312;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private readonly IntPtr _hwnd;
    private readonly HwndSource _source;
    private readonly Action _onTriggered;
    private bool _registered;

    public GlobalHotkey(Window window, Action onTriggered)
    {
        _onTriggered = onTriggered;
        // EnsureHandle materializes the HWND even if the window has never shown.
        _hwnd = new WindowInteropHelper(window).EnsureHandle();
        _source = HwndSource.FromHwnd(_hwnd)
                  ?? throw new InvalidOperationException("Failed to obtain HwndSource for the window.");
        _source.AddHook(WndProc);
    }

    /// <summary>Returns false if the combo is already held by another app.</summary>
    public bool Register()
    {
        _registered = RegisterHotKey(_hwnd, HotkeyId, MOD_CTRL | MOD_SHIFT | MOD_NOREPEAT, VK_SPACE);
        return _registered;
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_HOTKEY && wParam.ToInt32() == HotkeyId)
        {
            _onTriggered();
            handled = true;
        }
        return IntPtr.Zero;
    }

    public void Dispose()
    {
        if (_registered)
        {
            UnregisterHotKey(_hwnd, HotkeyId);
            _registered = false;
        }
        _source.RemoveHook(WndProc);
    }
}
