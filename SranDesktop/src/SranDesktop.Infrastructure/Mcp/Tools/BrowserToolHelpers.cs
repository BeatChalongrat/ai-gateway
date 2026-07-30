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

    /// <summary>Extension-reported errors that are raised by a guard BEFORE the
    /// page is touched. Anything else that comes back from a mutating dispatch
    /// may have already changed the page.</summary>
    private static readonly string[] PreDispatchGuards =
    [
        "StaleDomReference",
        "StaleBrowserSession",
        "RuntimeContextRequired",
        "ActionCancelledBeforeDispatch",
        "TargetNotFound",
        "TargetAmbiguous",
        "Cannot access this page"
    ];

    internal static async Task<ToolResult> RequestAsync(
        IBrowserBridgeService bridge, string method, object? @params,
        Func<JsonElement, ToolResult> onSuccess, CancellationToken ct,
        bool mutating = false)
    {
        try
        {
            var result = await bridge.RequestAsync(method, @params, ct);
            return onSuccess(result);
        }
        catch (Exception ex) when (
            ex is BrowserNotConnectedException or TimeoutException or BrowserBridgeException)
        {
            return DispatchFailure(ex, mutating);
        }
    }

    /// <summary>
    /// Classifies a bridge failure for the durable runtime. A relay/extension
    /// that dies (or goes silent) AFTER a mutating request was written to the
    /// pipe leaves the page state genuinely unknown — the click may have landed
    /// and only the answer was lost. Reporting that as a plain failure would let
    /// the runtime treat it as "no effect" and re-dispatch, which is exactly how
    /// duplicate submits happen. Unknown outcomes are surfaced as such so the
    /// runtime reconciles before any further side effect.
    /// </summary>
    internal static ToolResult DispatchFailure(Exception ex, bool mutating)
    {
        if (!mutating)
            return ToolResult.Fail(ex is BrowserNotConnectedException
                ? NotConnectedMessage
                : ex.Message);

        return ex switch
        {
            BrowserNotConnectedException => ToolResult.FailedEffect(
                "UnknownOutcome",
                "The browser bridge disconnected around this action. Whether the page "
                + "changed is unknown — re-read the page and reconcile before retrying. "
                + NotConnectedMessage,
                retryable: false),
            TimeoutException => ToolResult.FailedEffect(
                "Timeout",
                $"{ex.Message} The action may already have taken effect; "
                + "re-read the page and reconcile before retrying.",
                retryable: false),
            _ when PreDispatchGuards.Any(guard =>
                ex.Message.Contains(guard, StringComparison.Ordinal)) =>
                ToolResult.Fail(ex.Message),
            _ => ToolResult.FailedEffect(
                "UnknownOutcome",
                $"{ex.Message} The extension reported this after dispatch, so the page "
                + "state is unknown — re-read the page and reconcile before retrying.",
                retryable: false)
        };
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

    internal static int GetInt(JsonElement result, string name) =>
        result.ValueKind == JsonValueKind.Object
        && result.TryGetProperty(name, out var el)
        && el.TryGetInt32(out var value)
            ? value
            : 0;

    internal static long GetLong(JsonElement result, string name) =>
        result.ValueKind == JsonValueKind.Object
        && result.TryGetProperty(name, out var el)
        && el.TryGetInt64(out var value)
            ? value
            : 0;
}
