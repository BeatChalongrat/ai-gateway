using System.Text.Json;
using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ReadClipboardTool : IMcpTool
{
    public string Name => "read_clipboard";
    public string Description => "Read the current text content of the system clipboard.";
    public ToolCategory Category => ToolCategory.Clipboard;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowOnce; // privacy-sensitive read

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        { "type": "object", "properties": {} }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        // System.Windows.Clipboard requires an STA thread → hop to the UI dispatcher.
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null)
            return ToolResult.Fail("Clipboard unavailable: no UI dispatcher.");

        var text = await dispatcher.InvokeAsync(() =>
            Clipboard.ContainsText() ? Clipboard.GetText() : null);

        return ToolResult.Ok(string.IsNullOrEmpty(text) ? "Clipboard is empty." : text!);
    }
}
