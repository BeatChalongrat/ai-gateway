using System.Diagnostics;
using System.Runtime.InteropServices;
using SranDesktop.Infrastructure.Native;
using Xunit;
using Xunit.Abstractions;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// OS-level probe for OsInputRejected (H1/H2/H3). Deterministic, NO LLM and NO computer-use
/// tools — it calls the real Win32Input primitives directly against throwaway targets it
/// launches itself. Guarded by SRAN_PROBE=1 so a normal `dotnet test` is a no-op; run with:
///   SRAN_PROBE=1 dotnet test --filter FullyQualifiedName~OsInputProbeTests
/// Results are written to %TEMP%\sran_probe.txt and the test output.
/// NOTE: injects a single left-click into each target's OWN window (harmless), then closes it.
/// </summary>
public class OsInputProbeTests
{
    private readonly ITestOutputHelper _out;
    public OsInputProbeTests(ITestOutputHelper output) => _out = output;

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumProc cb, IntPtr param);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    private delegate bool EnumProc(IntPtr h, IntPtr p);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    /// <summary>First visible top-level window whose title contains <paramref name="titlePart"/>
    /// — needed for Store-Notepad, whose window is owned by a broker process, not the
    /// launched notepad.exe, so Process.MainWindowHandle is 0.</summary>
    private static IntPtr FindWindowByTitle(string titlePart)
    {
        var found = IntPtr.Zero;
        EnumWindows((h, _) =>
        {
            if (!IsWindowVisible(h)) return true;
            var sb = new System.Text.StringBuilder(256);
            GetWindowText(h, sb, sb.Capacity);
            if (sb.ToString().Contains(titlePart, StringComparison.OrdinalIgnoreCase))
            {
                found = h;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    [Fact]
    public void Probe_SendInput_Acceptance_Win32_vs_StoreNotepad()
    {
        if (Environment.GetEnvironmentVariable("SRAN_PROBE") != "1")
            return; // manual OS probe — set SRAN_PROBE=1 to run

        var rows = new List<string>
        {
            "target | launched | foreground_acquired | cursor_positioned | accepted/requested | win32_error"
        };

        // Store-Notepad first (the reported failure), then medium-IL Win32 apps for contrast.
        foreach (var (label, cmd, titleHint) in new[]
        {
            ("store-notepad", "notepad.exe", "Notepad"),
            ("win32-charmap",  "charmap.exe", "Character Map"),
        })
        {
            Process? p = null;
            try
            {
                p = Process.Start(new ProcessStartInfo(cmd) { UseShellExecute = true });

                var hwnd = IntPtr.Zero;
                for (var i = 0; i < 50 && hwnd == IntPtr.Zero; i++)
                {
                    Thread.Sleep(200);
                    p?.Refresh();
                    hwnd = p?.MainWindowHandle ?? IntPtr.Zero;
                    // Store-Notepad's window belongs to a broker, not the launched process —
                    // fall back to finding it by title.
                    if (hwnd == IntPtr.Zero) hwnd = FindWindowByTitle(titleHint);
                }
                if (hwnd == IntPtr.Zero)
                {
                    rows.Add($"{label} | no-window | - | - | - | -");
                    continue;
                }

                Win32Input.ForceForegroundWindow(hwnd);
                Thread.Sleep(400);
                var foregroundAcquired = Win32Input.GetForegroundWindow() == hwnd;

                GetWindowRect(hwnd, out var r);
                var cx = (r.Left + r.Right) / 2;
                var cy = (r.Top + r.Bottom) / 2;

                var res = Win32Input.Click(cx, cy); // single left-click in the target's own window

                rows.Add($"{label} | yes | {foregroundAcquired} | {res.CursorPositioned} | " +
                         $"{res.AcceptedInputEvents}/{res.RequestedInputEvents} | {res.LastWin32Error}");
            }
            catch (Exception ex)
            {
                rows.Add($"{label} | error: {ex.Message} | - | - | - | -");
            }
            finally
            {
                try { if (p is { HasExited: false }) p.Kill(entireProcessTree: true); } catch { /* best effort */ }
            }
        }

        var table = string.Join("\n", rows);
        _out.WriteLine(table);
        try { File.WriteAllText(Path.Combine(Path.GetTempPath(), "sran_probe.txt"), table); } catch { }
    }
}
