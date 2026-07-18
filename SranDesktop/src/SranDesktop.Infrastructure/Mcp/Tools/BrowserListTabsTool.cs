using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_list_tabs — read-only tab inventory (browser tools).
/// Split from browser_tabs so listing can be AllowAlways/Safe while
/// activate/new/close stay consent-gated.</summary>
public sealed class BrowserListTabsTool : IMcpTool
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserListTabsTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_list_tabs";
    public string Description =>
        "List the open Chrome tabs (id, title, url, which is active). " +
        "Use the tab ids with the other browser_* tools.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        { "type": "object", "properties": {} }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
        BrowserToolHelpers.RequestAsync(_bridge, "tabs.list", null, result =>
        {
            if (!result.TryGetProperty("tabs", out var tabs) || tabs.ValueKind != JsonValueKind.Array)
                return ToolResult.Fail("Malformed tab list from the extension.");
            var sb = new StringBuilder();
            foreach (var tab in tabs.EnumerateArray())
            {
                sb.Append('[').Append(tab.GetProperty("id").GetInt32()).Append("] ")
                  .Append(BrowserToolHelpers.GetString(tab, "title"))
                  .Append(" — ").Append(BrowserToolHelpers.GetString(tab, "url"));
                if (tab.TryGetProperty("active", out var active) && active.ValueKind == JsonValueKind.True)
                    sb.Append(" (active)");
                sb.AppendLine();
            }
            return ToolResult.Ok(sb.Length > 0 ? sb.ToString().TrimEnd() : "No open tabs.");
        }, ct);
}
