using System.Diagnostics;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class OpenUrlTool : IMcpTool
{
    public string Name => "open_url";
    public string Description => "Open a URL in the user's default web browser.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "http:// or https:// URL to open" }
            },
            "required": ["url"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
        {
            return Task.FromResult(ToolResult.Fail("URL must be absolute and use http:// or https://."));
        }

        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
            return Task.FromResult(ToolResult.Ok($"Opened {uri.AbsoluteUri} in the default browser."));
        }
        catch (Exception ex)
        {
            return Task.FromResult(ToolResult.Fail($"Could not open browser: {ex.Message}"));
        }
    }
}
