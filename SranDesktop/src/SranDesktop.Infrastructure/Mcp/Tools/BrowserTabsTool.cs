using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class BrowserTabsTool : IMcpTool, IPerceptualActionTool
{
    private readonly IBrowserBridgeService _bridge;
    private readonly BrowserInteractionObserver _observer;

    public BrowserTabsTool(IBrowserBridgeService bridge)
    {
        _bridge = bridge;
        _observer = new BrowserInteractionObserver(bridge);
    }

    public string Name => "browser_tabs";
    public string Description =>
        "Manage Chrome tabs and independently verify active, created, or closed state. " +
        "Use browser_list_tabs first to obtain tab identities.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "action": { "type": "string", "enum": ["activate", "new", "close"] },
                "tab_id": { "type": "integer" },
                "url": { "type": "string" }
            },
            "required": ["action"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var action = ToolHelpers.GetString(arguments, "action");
        var tabId = BrowserToolHelpers.GetTabId(arguments);
        var url = ToolHelpers.GetString(arguments, "url");
        switch (action)
        {
            case "activate" when tabId is null:
            case "close" when tabId is null:
                return Task.FromResult(ToolResult.Fail(
                    $"tab_id is required for action '{action}'."));
            case "new" when !Uri.TryCreate(url, UriKind.Absolute, out var uri)
                            || uri.Scheme is not ("http" or "https"):
                return Task.FromResult(ToolResult.Fail(
                    "url must be an absolute http(s) URL for action 'new'."));
            case "activate":
                return BrowserToolHelpers.RequestAsync(
                    _bridge,
                    "tabs.activate",
                    new { tab_id = tabId },
                    result => result.TryGetProperty("active", out var active)
                              && active.ValueKind == JsonValueKind.True
                        ? ToolResult.Verified(
                            $"Activated and verified tab {tabId}.",
                            [$"active_tab_id={tabId}"])
                        : ToolResult.ExecutedUnverified(
                            $"Requested activation of tab {tabId}.",
                            "TabActivationNotVerified",
                            "Chrome did not report the target tab as active."),
                    ct,
                    mutating: true);
            case "new":
                return BrowserToolHelpers.RequestAsync(
                    _bridge,
                    "tabs.new",
                    new { url },
                    result => result.TryGetProperty("created", out var created)
                              && created.ValueKind == JsonValueKind.True
                              && BrowserToolHelpers.GetInt(result, "tab_id") > 0
                        ? ToolResult.Verified(
                            $"Opened and verified tab " +
                            $"{BrowserToolHelpers.GetInt(result, "tab_id")}: " +
                            $"{BrowserToolHelpers.GetString(result, "title")} - " +
                            BrowserToolHelpers.GetString(result, "url"),
                            [$"created_tab_id={BrowserToolHelpers.GetInt(result, "tab_id")}"])
                        : ToolResult.ExecutedUnverified(
                            "Chrome accepted the new-tab request.",
                            "TabCreationNotVerified",
                            "The created tab identity was not observed."),
                    ct,
                    mutating: true);
            case "close":
                return BrowserToolHelpers.RequestAsync(
                    _bridge,
                    "tabs.close",
                    new { tab_id = tabId },
                    result => result.TryGetProperty("closed", out var closed)
                              && closed.ValueKind == JsonValueKind.True
                        ? ToolResult.Verified(
                            $"Closed and verified tab {tabId}.",
                            [$"closed_tab_id={tabId}"])
                        : ToolResult.ExecutedUnverified(
                            $"Requested close of tab {tabId}.",
                            "TabCloseNotVerified",
                            "Chrome still reported the target tab."),
                    ct,
                    mutating: true);
            default:
                return Task.FromResult(ToolResult.Fail(
                    "action must be one of: activate, new, close."));
        }
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments)
    {
        var action = ToolHelpers.GetString(arguments, "action");
        return new InteractionActionDescriptor(
            action switch
            {
                "activate" => InteractionActionKind.SwitchTab,
                "new" => InteractionActionKind.OpenTab,
                "close" => InteractionActionKind.CloseTab,
                _ => InteractionActionKind.Wait
            },
            action == "new"
                ? ToolHelpers.GetString(arguments, "url") ?? ""
                : $"tab:{BrowserToolHelpers.GetTabId(arguments)?.ToString() ?? "none"}",
            action ?? "",
            action == "close"
                ? SideEffectClass.ReversibleMediumRisk
                : SideEffectClass.ReversibleLowRisk,
            Reversible: action != "close",
            Idempotent: action == "activate",
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(20),
            MaximumAttempts: action == "close" ? 1 : 2);
    }

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveTabsBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveTabsAfterAsync(
            arguments, DescribeAction(arguments), before, ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);
}
