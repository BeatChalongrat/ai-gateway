using System.Runtime.InteropServices;
using System.Text;

namespace SranDesktop.Infrastructure.Native;

internal sealed record InputExecutionResult(
    bool CursorPositioned,
    int RequestedInputEvents,
    int AcceptedInputEvents,
    int LastWin32Error)
{
    public bool OsInputAccepted =>
        CursorPositioned && RequestedInputEvents > 0
        && AcceptedInputEvents == RequestedInputEvents;
}

/// <summary>
/// P/Invoke wrappers for synthetic mouse/keyboard input (SendInput) and
/// window lookup/focus — the foundation of the computer-use tools.
/// </summary>
internal static class Win32Input
{
    // ── SendInput plumbing ──────────────────────────────────────────────────

    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT
    {
        public uint type;
        public InputUnion u;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT
    {
        public ushort wVk, wScan;
        public uint dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    private const uint INPUT_MOUSE = 0;
    private const uint INPUT_KEYBOARD = 1;

    private const uint MOUSEEVENTF_MOVE = 0x0001;
    private const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    private const uint MOUSEEVENTF_LEFTUP = 0x0004;
    private const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    private const uint MOUSEEVENTF_RIGHTUP = 0x0010;
    private const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    private const uint MOUSEEVENTF_MIDDLEUP = 0x0040;
    private const uint MOUSEEVENTF_WHEEL = 0x0800;
    private const uint MOUSEEVENTF_HWHEEL = 0x1000;

    private const uint KEYEVENTF_KEYUP = 0x0002;
    private const uint KEYEVENTF_UNICODE = 0x0004;

    [DllImport("user32.dll", EntryPoint = "SendInput", SetLastError = true)]
    private static extern uint SendInputRaw(uint nInputs, INPUT[] pInputs, int cbSize);

    /// <summary>
    /// The single narrow point through which every synthetic keystroke and mouse
    /// event in this process passes. Marking the agent-input window here rather
    /// than at each caller is deliberate: a decision that must be a person's —
    /// approving a connector, above all — has to be able to tell that the click
    /// arriving right now was manufactured by a tool, and a marker set at five
    /// call sites is a marker missing from the sixth.
    /// </summary>
    private static uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize)
    {
        Application.Services.AgentInputScope.Touch("synthetic input (SendInput)");
        return SendInputRaw(nInputs, pInputs, cbSize);
    }

    // SetLastError so ClickCore's Marshal.GetLastWin32Error() after a failed SetCursorPos
    // is accurate (UIPI blocks report ERROR_ACCESS_DENIED = 5) — part of the OsInputRejected probe.
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    private const int SM_CXSCREEN = 0;
    private const int SM_CYSCREEN = 1;
    private const int SM_XVIRTUALSCREEN = 76;
    private const int SM_YVIRTUALSCREEN = 77;
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;

    // ── Window management ───────────────────────────────────────────────────

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    internal static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool IsWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    internal static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    private delegate bool MonitorEnumProc(
        IntPtr hMonitor, IntPtr hdcMonitor, ref RECT monitorRect, IntPtr data);

    [StructLayout(LayoutKind.Sequential)]
    internal struct RECT
    {
        public int Left, Top, Right, Bottom;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct MONITORINFOEX
    {
        public int Size;
        public RECT Monitor;
        public RECT WorkArea;
        public uint Flags;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string DeviceName;
    }

    [DllImport("user32.dll")]
    private static extern bool EnumDisplayMonitors(
        IntPtr hdc, IntPtr clipRect, MonitorEnumProc callback, IntPtr data);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFOEX info);

    internal static (int Width, int Height) PrimaryScreenSize()
        => (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));

    /// <summary>The virtual desktop spanning ALL monitors. X/Y can be negative
    /// (a monitor left of / above the primary).</summary>
    internal static (int X, int Y, int Width, int Height) VirtualScreenBounds()
        => (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN));

    internal static double DpiScaleForWindow(IntPtr hWnd)
    {
        var dpi = hWnd == IntPtr.Zero ? 0 : GetDpiForWindow(hWnd);
        return dpi == 0 ? 1d : dpi / 96d;
    }

    internal static long DisplayTopologyVersion()
    {
        unchecked
        {
            const long offset = 1469598103934665603;
            const long prime = 1099511628211;
            var hash = offset;
            foreach (var monitor in ListMonitors().OrderBy(m => m.Id, StringComparer.Ordinal))
            {
                foreach (var value in new[]
                         {
                             monitor.Bounds.X, monitor.Bounds.Y,
                             monitor.Bounds.Width, monitor.Bounds.Height
                         })
                    hash = (hash ^ value) * prime;
                foreach (var ch in monitor.Id)
                    hash = (hash ^ ch) * prime;
            }
            return hash;
        }
    }

    internal sealed record MonitorDescriptor(string Id, PhysicalRect Bounds);

    internal static IReadOnlyList<MonitorDescriptor> ListMonitors()
    {
        var monitors = new List<MonitorDescriptor>();
        EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero,
            (IntPtr hMonitor, IntPtr _, ref RECT rect, IntPtr _) =>
            {
                var info = new MONITORINFOEX
                {
                    Size = Marshal.SizeOf<MONITORINFOEX>(),
                    DeviceName = string.Empty
                };
                var id = GetMonitorInfo(hMonitor, ref info)
                    ? info.DeviceName
                    : $"monitor-{hMonitor.ToInt64()}";
                monitors.Add(new MonitorDescriptor(
                    id,
                    new PhysicalRect(
                        rect.Left, rect.Top,
                        rect.Right - rect.Left,
                        rect.Bottom - rect.Top)));
                return true;
            }, IntPtr.Zero);
        return monitors;
    }

    internal static long WindowGeometryVersion(IntPtr hWnd)
    {
        if (hWnd == IntPtr.Zero || !GetWindowRect(hWnd, out var rect))
            return 0;
        return HashCode.Combine(hWnd.ToInt64(), rect.Left, rect.Top, rect.Right, rect.Bottom);
    }

    /// <summary>First visible top-level window whose title contains <paramref name="titlePart"/>.</summary>
    internal static IntPtr FindWindowByTitle(string titlePart)
    {
        var found = IntPtr.Zero;
        EnumWindows((hWnd, _) =>
        {
            if (!IsWindowVisible(hWnd))
                return true;
            var sb = new StringBuilder(512);
            GetWindowText(hWnd, sb, sb.Capacity);
            var title = sb.ToString();
            if (title.Length > 0 && title.Contains(titlePart, StringComparison.OrdinalIgnoreCase))
            {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    internal static void FocusWindow(IntPtr hWnd)
    {
        if (IsIconic(hWnd))
            ShowWindow(hWnd, 9); // SW_RESTORE
        SetForegroundWindow(hWnd);
    }

    // ── Foreground / hit-testing (computer-use accuracy overhaul) ───────────

    [DllImport("user32.dll")]
    internal static extern IntPtr GetForegroundWindow();

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int X, Y;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(POINT point);

    [DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr hWnd, uint gaFlags);

    private const uint GA_ROOT = 2;

    [DllImport("user32.dll")]
    internal static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

    [DllImport("user32.dll")]
    private static extern bool BringWindowToTop(IntPtr hWnd);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    /// <summary>Process id owning the window under a screen point (0 = none).
    /// Used by computer_click to refuse clicking SRAN's own UI.</summary>
    internal static uint ProcessIdAtPoint(int x, int y)
    {
        var hWnd = WindowFromPoint(new POINT { X = x, Y = y });
        if (hWnd == IntPtr.Zero)
            return 0;
        GetWindowThreadProcessId(hWnd, out var pid);
        return pid;
    }

    internal static IntPtr RootWindowAtPoint(int x, int y)
    {
        var hWnd = WindowFromPoint(new POINT { X = x, Y = y });
        return hWnd == IntPtr.Zero ? IntPtr.Zero : GetAncestor(hWnd, GA_ROOT);
    }

    /// <summary>
    /// SetForegroundWindow honestly fails when another process owns the
    /// foreground (the OS foreground lock). Attaching our input queue to the
    /// current foreground thread's queue is the documented workaround — while
    /// attached we count as "the foreground application" and the switch is
    /// allowed. Returns whether the window really is foreground afterwards.
    /// </summary>
    internal static bool ForceForegroundWindow(IntPtr hWnd)
    {
        if (IsIconic(hWnd))
            ShowWindow(hWnd, 9); // SW_RESTORE

        var foreground = GetForegroundWindow();
        if (foreground == hWnd)
            return true;

        var targetThread = GetWindowThreadProcessId(hWnd, out _);
        var foregroundThread = foreground != IntPtr.Zero
            ? GetWindowThreadProcessId(foreground, out _) : 0;
        var ourThread = GetCurrentThreadId();

        var attachedForeground = foregroundThread != 0 && foregroundThread != ourThread
            && AttachThreadInput(ourThread, foregroundThread, true);
        var attachedTarget = targetThread != 0 && targetThread != ourThread
            && AttachThreadInput(ourThread, targetThread, true);
        try
        {
            BringWindowToTop(hWnd);
            SetForegroundWindow(hWnd);
        }
        finally
        {
            if (attachedForeground)
                AttachThreadInput(ourThread, foregroundThread, false);
            if (attachedTarget)
                AttachThreadInput(ourThread, targetThread, false);
        }
        return GetForegroundWindow() == hWnd;
    }

    internal static string GetWindowTitleText(IntPtr hWnd)
    {
        var sb = new StringBuilder(512);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    /// <summary>All visible, titled top-level windows — get_computer_state's raw list.</summary>
    internal static List<(IntPtr Handle, string Title, bool Minimized)> ListTopLevelWindows()
    {
        var windows = new List<(IntPtr, string, bool)>();
        EnumWindows((hWnd, _) =>
        {
            if (IsWindowVisible(hWnd))
            {
                var title = GetWindowTitleText(hWnd);
                if (title.Length > 0)
                    windows.Add((hWnd, title, IsIconic(hWnd)));
            }
            return true;
        }, IntPtr.Zero);
        return windows;
    }

    // ── Mouse ───────────────────────────────────────────────────────────────

    public static void MoveMouse(int x, int y) => SetCursorPos(x, y);

    public static InputExecutionResult Click(
        int x, int y, string button = "left", int clicks = 1) =>
        ClickCore(
            SetCursorPos,
            (flags, data) => SendMouse(flags, data),
            x, y, button, clicks,
            milliseconds => Thread.Sleep(milliseconds));

    internal static InputExecutionResult ClickCore(
        Func<int, int, bool> setCursorPosition,
        Func<uint, uint, uint> sendMouse,
        int x,
        int y,
        string button,
        int clicks,
        Action<int>? delay = null)
    {
        if (clicks < 1)
            throw new ArgumentOutOfRangeException(nameof(clicks));

        var cursorPositioned = setCursorPosition(x, y);
        if (!cursorPositioned)
            return new InputExecutionResult(
                false, clicks * 2, 0, Marshal.GetLastWin32Error());

        delay?.Invoke(60);

        var (down, up) = button switch
        {
            "right" => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle" => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            _ => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        };

        var accepted = 0;
        for (var i = 0; i < clicks; i++)
        {
            accepted += checked((int)sendMouse(down, 0));
            accepted += checked((int)sendMouse(up, 0));
            if (i < clicks - 1)
                delay?.Invoke(80);
        }

        return new InputExecutionResult(
            true, clicks * 2, accepted,
            accepted == clicks * 2 ? 0 : Marshal.GetLastWin32Error());
    }

    public static void Scroll(int x, int y, string direction, int amount)
    {
        SetCursorPos(x, y);
        Thread.Sleep(40);
        var horizontal = direction is "left" or "right";
        var sign = direction is "up" or "right" ? 1 : -1;
        SendMouse(horizontal ? MOUSEEVENTF_HWHEEL : MOUSEEVENTF_WHEEL,
            mouseData: unchecked((uint)(120 * amount * sign)));
    }

    public static void Drag(int x1, int y1, int x2, int y2)
    {
        SetCursorPos(x1, y1);
        Thread.Sleep(80);
        SendMouse(MOUSEEVENTF_LEFTDOWN);
        Thread.Sleep(80);

        // Incremental moves — many apps ignore a single teleport while dragging.
        const int steps = 20;
        for (var i = 1; i <= steps; i++)
        {
            SetCursorPos(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps);
            Thread.Sleep(12);
        }

        Thread.Sleep(80);
        SendMouse(MOUSEEVENTF_LEFTUP);
    }

    private static uint SendMouse(uint flags, uint mouseData = 0)
    {
        var input = new INPUT
        {
            type = INPUT_MOUSE,
            u = new InputUnion { mi = new MOUSEINPUT { dwFlags = flags, mouseData = mouseData } }
        };
        return SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
    }

    // ── Keyboard ────────────────────────────────────────────────────────────

    /// <summary>Types literal text via KEYEVENTF_UNICODE (layout-independent).</summary>
    public static InputExecutionResult TypeText(string text)
    {
        var requested = 0;
        var accepted = 0;
        foreach (var ch in text)
        {
            if (ch == '\r')
                continue;
            if (ch == '\n')
            {
                requested += 2;
                accepted += PressVirtualKey(0x0D);
                Thread.Sleep(5);
                continue;
            }

            requested += 2;
            accepted += checked((int)SendUnicode(ch, keyUp: false));
            accepted += checked((int)SendUnicode(ch, keyUp: true));
            Thread.Sleep(5);
        }
        return new InputExecutionResult(
            true, requested, accepted,
            requested == accepted ? 0 : Marshal.GetLastWin32Error());
    }

    private static uint SendUnicode(char ch, bool keyUp)
    {
        var input = new INPUT
        {
            type = INPUT_KEYBOARD,
            u = new InputUnion
            {
                ki = new KEYBDINPUT
                {
                    wVk = 0,
                    wScan = ch,
                    dwFlags = KEYEVENTF_UNICODE | (keyUp ? KEYEVENTF_KEYUP : 0)
                }
            }
        };
        return SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
    }

    /// <summary>
    /// Presses a combo like "Ctrl+C", "Enter", "Alt+Tab", "Ctrl+Shift+S":
    /// modifiers down → key down/up → modifiers up.
    /// </summary>
    public static InputExecutionResult PressKey(string keyCombo)
    {
        var parts = ParseKeyCombo(keyCombo);
        var requested = 0;
        var accepted = 0;
        foreach (var vk in parts.Modifiers)
        {
            requested++;
            accepted += checked((int)SendVirtualKey(vk, keyUp: false));
        }
        Thread.Sleep(20);
        requested += 2;
        accepted += PressVirtualKey(parts.Key);
        Thread.Sleep(20);
        for (var i = parts.Modifiers.Count - 1; i >= 0; i--)
        {
            requested++;
            accepted += checked((int)SendVirtualKey(parts.Modifiers[i], keyUp: true));
        }
        return new InputExecutionResult(
            true, requested, accepted,
            requested == accepted ? 0 : Marshal.GetLastWin32Error());
    }

    private static int PressVirtualKey(ushort vk)
    {
        return checked((int)SendVirtualKey(vk, keyUp: false))
               + checked((int)SendVirtualKey(vk, keyUp: true));
    }

    private static uint SendVirtualKey(ushort vk, bool keyUp)
    {
        var input = new INPUT
        {
            type = INPUT_KEYBOARD,
            u = new InputUnion
            {
                ki = new KEYBDINPUT { wVk = vk, dwFlags = keyUp ? KEYEVENTF_KEYUP : 0 }
            }
        };
        return SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
    }

    /// <summary>Parses "Ctrl+Shift+S" → modifiers [Ctrl, Shift] + key S. Throws on unknown keys.</summary>
    internal static (List<ushort> Modifiers, ushort Key) ParseKeyCombo(string combo)
    {
        var modifiers = new List<ushort>();
        ushort key = 0;
        var sawKey = false;

        foreach (var raw in combo.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var token = raw.ToLowerInvariant();
            switch (token)
            {
                case "ctrl" or "control": modifiers.Add(0xA2); continue;
                case "alt": modifiers.Add(0xA4); continue;
                case "shift": modifiers.Add(0xA0); continue;
                case "win" or "windows" or "meta": modifiers.Add(0x5B); continue;
            }

            key = MapKey(token);
            sawKey = true;
        }

        if (!sawKey)
        {
            // Bare modifier ("Ctrl") — treat the last modifier as the key itself.
            if (modifiers.Count == 0)
                throw new ArgumentException($"Unrecognized key combo '{combo}'.");
            key = modifiers[^1];
            modifiers.RemoveAt(modifiers.Count - 1);
        }

        return (modifiers, key);
    }

    private static ushort MapKey(string token) => token switch
    {
        "enter" or "return" => 0x0D,
        "tab" => 0x09,
        "escape" or "esc" => 0x1B,
        "space" or "spacebar" => 0x20,
        "backspace" => 0x08,
        "delete" or "del" => 0x2E,
        "insert" or "ins" => 0x2D,
        "up" or "arrowup" => 0x26,
        "down" or "arrowdown" => 0x28,
        "left" or "arrowleft" => 0x25,
        "right" or "arrowright" => 0x27,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" or "pgup" => 0x21,
        "pagedown" or "pgdn" => 0x22,
        "printscreen" or "prtsc" => 0x2C,
        "capslock" => 0x14,
        _ when token.Length == 1 && token[0] is >= 'a' and <= 'z' => (ushort)(0x41 + token[0] - 'a'),
        _ when token.Length == 1 && token[0] is >= '0' and <= '9' => (ushort)(0x30 + token[0] - '0'),
        _ when token.Length is 2 or 3 && token[0] == 'f'
               && int.TryParse(token[1..], out var f) && f is >= 1 and <= 12 => (ushort)(0x70 + f - 1),
        _ => throw new ArgumentException($"Unrecognized key '{token}'.")
    };
}
