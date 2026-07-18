using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>Shared plumbing for the browser_* tools: one place that turns
/// bridge failures into the messages the model can act on (install guidance,
/// timeout, extension-reported errors passed through verbatim).</summary>
internal static class BrowserToolHelpers
{
    internal const string NotConnectedMessage =
        "Chrome extension not connected — ask the user to install/enable it (Settings → Browser).";

    internal static async Task<ToolResult> RequestAsync(
        IBrowserBridgeService bridge, string method, object? @params,
        Func<JsonElement, ToolResult> onSuccess, CancellationToken ct)
    {
        try
        {
            var result = await bridge.RequestAsync(method, @params, ct);
            return onSuccess(result);
        }
        catch (BrowserNotConnectedException)
        {
            return ToolResult.Fail(NotConnectedMessage);
        }
        catch (TimeoutException ex)
        {
            return ToolResult.Fail(ex.Message);
        }
        catch (BrowserBridgeException ex)
        {
            return ToolResult.Fail(ex.Message);
        }
    }

    internal static int? GetTabId(JsonElement args) =>
        args.ValueKind == JsonValueKind.Object
        && args.TryGetProperty("tab_id", out var el)
        && el.TryGetInt32(out var id)
            ? id
            : null;

    internal static string GetString(JsonElement result, string name) =>
        result.ValueKind == JsonValueKind.Object
        && result.TryGetProperty(name, out var el)
        && el.ValueKind == JsonValueKind.String
            ? el.GetString() ?? ""
            : "";
}
