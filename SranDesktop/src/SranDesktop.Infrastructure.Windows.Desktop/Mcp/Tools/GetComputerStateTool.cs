using System.Diagnostics;
using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// get_computer_state (UIA layer) — the agent's situational awareness: which
/// window is focused, what else is open, and whether a modal dialog is
/// blocking the foreground window (the classic reason clicks "do nothing").
/// Read-only observation → AllowAlways + Safe-listed.
/// </summary>
public sealed class GetComputerStateTool : IMcpTool
{
    private const int MaxWindows = 40;

    public string Name => "get_computer_state";
    public string Description =>
        "See what applications and windows are open and which is focused. " +
        "Use before deciding how to act.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        { "type": "object", "properties": {} }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        try
        {
            var json = await Task.Run(BuildState, ct).WaitAsync(UiaToolSupport.UiaTimeout, ct);
            return ToolResult.Ok(json);
        }
        catch (TimeoutException)
        {
            return ToolResult.Fail(UiaToolSupport.TimeoutError);
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"Could not read computer state: {UiaToolSupport.Describe(ex)}");
        }
    }

    private static string BuildState()
    {
        var foreground = Win32Input.GetForegroundWindow();
        object? foregroundInfo = null;
        if (foreground != IntPtr.Zero)
        {
            Win32Input.GetWindowRect(foreground, out var rect);
            foregroundInfo = new
            {
                title = Win32Input.GetWindowTitleText(foreground),
                process = ProcessNameOf(foreground),
                bounds = new
                {
                    x = rect.Left,
                    y = rect.Top,
                    width = rect.Right - rect.Left,
                    height = rect.Bottom - rect.Top
                }
            };
        }

        var windows = Win32Input.ListTopLevelWindows()
            .Take(MaxWindows)
            .Select(w => new
            {
                title = w.Title,
                process = ProcessNameOf(w.Handle),
                minimized = w.Minimized,
                focused = w.Handle == foreground
            })
            .ToList();

        return JsonSerializer.Serialize(new
        {
            foreground = foregroundInfo,
            windows,
            modal_dialog_on_top = foreground != IntPtr.Zero && HasModalChild(foreground)
        }, new JsonSerializerOptions { WriteIndented = true });
    }

    private static string ProcessNameOf(IntPtr hWnd)
    {
        try
        {
            Win32Input.GetWindowThreadProcessId(hWnd, out var pid);
            using var process = Process.GetProcessById((int)pid);
            return process.ProcessName;
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            return "unknown";
        }
    }

    /// <summary>Modal heuristic: a child Window element under the foreground
    /// window whose WindowPattern says modal — or ANY child window when the
    /// pattern is unavailable (dialogs frequently omit it). Best-effort: a
    /// UIA hiccup just reports false.</summary>
    private static bool HasModalChild(IntPtr foreground)
    {
        try
        {
            var root = AutomationElement.FromHandle(foreground);
            var children = root.FindAll(TreeScope.Children,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Window));
            foreach (AutomationElement child in children)
            {
                try
                {
                    if (child.Current.IsOffscreen)
                        continue;
                    if (child.TryGetCurrentPattern(WindowPattern.Pattern, out var pattern))
                    {
                        if (((WindowPattern)pattern).Current.IsModal)
                            return true;
                    }
                    else
                    {
                        return true; // child window without the pattern — very likely a dialog
                    }
                }
                catch (ElementNotAvailableException) { }
            }
            return false;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
