using System.Text.Json;
using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class WriteClipboardTool : IMcpTool
{
    public string Name => "write_clipboard";
    public string Description => "Replace the system clipboard content with the given text.";
    public ToolCategory Category => ToolCategory.Clipboard;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "text": { "type": "string", "description": "Text to place on the clipboard" }
            },
            "required": ["text"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var text = ToolHelpers.GetString(arguments, "text");
        if (text is null)
            return ToolResult.Fail("'text' is required.");

        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null)
            return ToolResult.Fail("Clipboard unavailable: no UI dispatcher.");

        await dispatcher.InvokeAsync(() => Clipboard.SetText(text));
        return ToolResult.Ok($"Clipboard set ({text.Length} chars).");
    }
}
