using System.Text.Json;
using System.Windows;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ReadClipboardTool : IMcpTool, ISensitiveDataTool
{
    private const int MaxClipboardChars = 64 * 1024;
    public string Name => "read_clipboard";
    public string Description => "Read the current text content of the system clipboard.";
    public ToolCategory Category => ToolCategory.Clipboard;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        LocalAction: "Clipboard.ReadLocal",
        ExternalAction: "Clipboard.SendToModel",
        SourceType: "clipboard",
        Sensitivity: "unknown",
        Destination: "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: MaxClipboardChars * 2);

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

        if (string.IsNullOrEmpty(text))
            return ToolResult.Verified("Clipboard is empty.", ["clipboard_chars_observed=0"]);
        if (text.Length > MaxClipboardChars)
            return ToolResult.Fail(
                $"Clipboard text exceeds the local {MaxClipboardChars}-character safety limit.");
        return ToolResult.Verified(
            UntrustedObservationBoundary.ProtectIfUntrusted(Name, text),
            [$"clipboard_chars_observed={text.Length}"]);
    }
}
