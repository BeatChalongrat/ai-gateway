using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class BrowserNavigateTool : IMcpTool, IPerceptualActionTool
{
    private readonly IBrowserBridgeService _bridge;
    private readonly BrowserInteractionObserver _observer;

    public BrowserNavigateTool(IBrowserBridgeService bridge)
    {
        _bridge = bridge;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_navigate";
    public string Description =>
        "Navigate a Chrome tab to a URL, wait for load completion, and verify the final URL.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "Absolute http:// or https:// URL" },
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" }
            },
            "required": ["url"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
            return Task.FromResult(ToolResult.Fail("url must be an absolute http(s) URL."));

        return BrowserToolHelpers.RequestAsync(
            _bridge,
            "page.navigate",
            new { url, tab_id = BrowserToolHelpers.GetTabId(arguments) },
            result =>
            {
                var finalUrl = BrowserToolHelpers.GetString(result, "url");
                var navigationVerified =
                    result.TryGetProperty("navigation_verified", out var verified)
                    && verified.ValueKind == JsonValueKind.True;
                if (!navigationVerified
                    || !Uri.TryCreate(finalUrl, UriKind.Absolute, out _))
                    return ToolResult.ExecutedUnverified(
                        "The browser accepted navigation, but no valid final URL was returned.",
                        "NavigationNotVerified",
                        "The bridge did not report a valid final URL.");
                return ToolResult.Verified(
                    $"Loaded and verified: {BrowserToolHelpers.GetString(result, "title")} - {finalUrl}.",
                    [$"browser_final_url={finalUrl}", "document_ready_state=complete"]);
            },
            ct,
            mutating: true);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Navigate,
            ToolHelpers.GetString(arguments, "url") ?? "",
            ToolHelpers.GetString(arguments, "url") ?? "",
            SideEffectClass.ReversibleLowRisk,
            Reversible: true,
            Idempotent: true,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(20),
            MaximumAttempts: 2);

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveAfterAsync(arguments, DescribeAction(arguments), before, ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);
}
