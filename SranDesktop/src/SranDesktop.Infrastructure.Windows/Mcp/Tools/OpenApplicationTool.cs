using System.Diagnostics;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class OpenApplicationTool : IMcpTool
{
    public string Name => "open_application";
    public string Description =>
        "Launch a Windows application by name, or bring it to the foreground if it's already running " +
        "(e.g. 'Notepad', 'Calculator', 'Chrome', 'Excel').";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "name": { "type": "string", "description": "Application name, e.g. 'Notepad', 'Chrome', 'Excel'" }
            },
            "required": ["name"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var name = ToolHelpers.GetString(arguments, "name");
        if (string.IsNullOrWhiteSpace(name))
            return ToolResult.Fail("name is required.");

        // 1) Already running? Just focus it.
        var existing = Win32Input.FindWindowByTitle(name);
        if (existing != IntPtr.Zero)
        {
            Win32Input.FocusWindow(existing);
            await Task.Delay(300, ct);
            return ToolResult.Ok($"'{name}' was already running — brought its window to the foreground. " +
                                 "Take a screenshot to see it.");
        }

        // 2) Launch via the shell (resolves App Paths, PATH, and Store aliases).
        try
        {
            Process.Start(new ProcessStartInfo(name) { UseShellExecute = true });
        }
        catch (Exception)
        {
            try
            {
                // Fallback: let the shell search ("explorer shell:appsFolder" style names not
                // supported — try the bare name through cmd's start, which also checks App Paths).
                Process.Start(new ProcessStartInfo("cmd.exe", $"/c start \"\" \"{name}\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true
                });
            }
            catch (Exception ex)
            {
                return ToolResult.Fail($"Could not launch '{name}': {ex.Message}");
            }
        }

        // 3) Wait up to 3s for a window whose title mentions the app.
        for (var waited = 0; waited < 3000; waited += 300)
        {
            await Task.Delay(300, ct);
            var hWnd = Win32Input.FindWindowByTitle(name);
            if (hWnd != IntPtr.Zero)
            {
                Win32Input.FocusWindow(hWnd);
                return ToolResult.Ok($"Opened '{name}' and focused its window. Take a screenshot to see it.");
            }
        }

        return ToolResult.Ok($"Launched '{name}' (no window with that title appeared within 3s — " +
                             "it may still be starting, or its window title differs). " +
                             "Take a screenshot to check.");
    }
}
