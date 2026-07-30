using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_read_page (browser tools) — THE preferred way to see a
/// page: the content script returns an indexed interactive-element list
/// ([12] &lt;button&gt; "Send reply" (enabled)) whose refs feed browser_click /
/// browser_type. DOM-level, so it beats screenshots for accuracy.</summary>
public sealed class BrowserReadPageTool : IMcpTool, ISensitiveDataTool
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserReadPageTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_read_page";
    public string Description =>
        "Read a Chrome tab: indexed list of interactive elements (use the [n] refs with " +
        "browser_click / browser_type) plus the page text with filter=full-text. " +
        "Refs go stale when the page changes — re-read after navigation.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "BrowserDom.ReadLocal",
        "BrowserDom.SendToModel",
        "browser_dom",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" },
                "filter": { "type": "string", "enum": ["interactive", "full-text"], "description": "interactive (default) = elements only; full-text adds the page text" }
            }
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
        BrowserToolHelpers.RequestAsync(_bridge, "page.read",
            new
            {
                tab_id = BrowserToolHelpers.GetTabId(arguments),
                filter = ToolHelpers.GetString(arguments, "filter") ?? "interactive"
            },
            r =>
            {
                // Page DOM/text is untrusted external content: wrap it so it can't
                // act as instructions. dom_length evidence stays on the raw length.
                var text = BrowserToolHelpers.GetString(r, "text");
                var observationId = BrowserToolHelpers.GetString(r, "observation_id");
                var domGeneration = BrowserToolHelpers.GetLong(r, "dom_generation");
                var tabId = BrowserToolHelpers.GetInt(r, "tab_id");
                if (string.IsNullOrWhiteSpace(observationId)
                    || domGeneration <= 0
                    || tabId <= 0)
                    return ToolResult.FailedEffect(
                        "BrowserObservationIdentityMissing",
                        "The browser bridge returned DOM content without a tab-bound " +
                        "observation identity and generation.",
                        retryable: true);
                var header =
                    $"Browser observation: observation_id={observationId}; " +
                    $"dom_generation={domGeneration}; tab_id={tabId}. " +
                    "Use all three values with browser_click/browser_type.\n";
                return ToolResult.Verified(
                    UntrustedObservationBoundary.ProtectIfUntrusted(Name, header + text),
                    ["browser_dom_observed=true", $"dom_length={text.Length}",
                        $"browser_observation_id={observationId}",
                        $"dom_generation={domGeneration}", $"tab_id={tabId}"]);
            }, ct);
}
