using System.Runtime.InteropServices;
using System.Text;

namespace SranDesktop.Infrastructure.Native;

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

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    private const int SM_CXSCREEN = 0;
    private const int SM_CYSCREEN = 1;

    // ── Window management ───────────────────────────────────────────────────

    [DllImport("user32.dll")]
    internal static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    internal static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    internal static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    internal static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    internal static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    internal struct RECT
    {
        public int Left, Top, Right, Bottom;
    }

    internal static (int Width, int Height) PrimaryScreenSize()
        => (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));

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

    // ── Mouse ───────────────────────────────────────────────────────────────

    public static void MoveMouse(int x, int y) => SetCursorPos(x, y);

    public static void Click(int x, int y, string button = "left", int clicks = 1)
    {
        SetCursorPos(x, y);
        Thread.Sleep(60);

        var (down, up) = button switch
        {
            "right" => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle" => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            _ => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        };

        for (var i = 0; i < clicks; i++)
        {
            SendMouse(down);
            SendMouse(up);
            if (i < clicks - 1)
                Thread.Sleep(80); // double/triple-click cadence
        }
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

    private static void SendMouse(uint flags, uint mouseData = 0)
    {
        var input = new INPUT
        {
            type = INPUT_MOUSE,
            u = new InputUnion { mi = new MOUSEINPUT { dwFlags = flags, mouseData = mouseData } }
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
    }

    // ── Keyboard ────────────────────────────────────────────────────────────

    /// <summary>Types literal text via KEYEVENTF_UNICODE (layout-independent).</summary>
    public static void TypeText(string text)
    {
        foreach (var ch in text)
        {
            if (ch == '\r')
                continue;
            if (ch == '\n')
            {
                PressVirtualKey(0x0D); // Enter
                Thread.Sleep(5);
                continue;
            }

            SendUnicode(ch, keyUp: false);
            SendUnicode(ch, keyUp: true);
            Thread.Sleep(5);
        }
    }

    private static void SendUnicode(char ch, bool keyUp)
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
        SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
    }

    /// <summary>
    /// Presses a combo like "Ctrl+C", "Enter", "Alt+Tab", "Ctrl+Shift+S":
    /// modifiers down → key down/up → modifiers up.
    /// </summary>
    public static void PressKey(string keyCombo)
    {
        var parts = ParseKeyCombo(keyCombo);
        foreach (var vk in parts.Modifiers)
            SendVirtualKey(vk, keyUp: false);
        Thread.Sleep(20);
        PressVirtualKey(parts.Key);
        Thread.Sleep(20);
        for (var i = parts.Modifiers.Count - 1; i >= 0; i--)
            SendVirtualKey(parts.Modifiers[i], keyUp: true);
    }

    private static void PressVirtualKey(ushort vk)
    {
        SendVirtualKey(vk, keyUp: false);
        SendVirtualKey(vk, keyUp: true);
    }

    private static void SendVirtualKey(ushort vk, bool keyUp)
    {
        var input = new INPUT
        {
            type = INPUT_KEYBOARD,
            u = new InputUnion
            {
                ki = new KEYBDINPUT { wVk = vk, dwFlags = keyUp ? KEYEVENTF_KEYUP : 0 }
            }
        };
        SendInput(1, new[] { input }, Marshal.SizeOf<INPUT>());
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
