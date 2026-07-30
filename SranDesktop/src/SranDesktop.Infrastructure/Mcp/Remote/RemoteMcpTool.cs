using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// IMcpTool adapter for a tool living on a remote MCP server: resolution and
/// consent flow stay identical to local tools, and execution proxies through the
/// connector service, which since Phase 4.1C routes it into
/// <see cref="IMcpDurableGateway"/>.
///
/// It carries no protocol logic of its own, on purpose. Trust, risk
/// classification, the recorded dispatch intent, the observation boundary, and
/// reconciliation all live behind that gateway; a tool class that dialled the
/// server itself is exactly the bypass this phase removed.
/// </summary>
public sealed class RemoteMcpTool : IMcpTool
{
    private readonly IMcpConnectorService _connectors;

    public RemoteMcpTool(IMcpConnectorService connectors, string name, string description, JsonElement schema)
    {
        _connectors = connectors;
        Name = name;
        Description = description;
        ParametersSchema = schema;
    }

    public string Name { get; }
    public string Description { get; }
    public JsonElement ParametersSchema { get; }
    public ToolCategory Category => ToolCategory.Network;

    // Remote side-effects are opaque — always ask the user first.
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public async Task<ToolResult> ExecuteAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        var result = await _connectors.ExecuteRemoteToolAsync(Name, arguments, ct);
        if (!result.Success) return result;
        var protectedContent = Application.Services.UntrustedObservationBoundary
            .ProtectIfUntrusted("mcp_remote_tool", result.Content);
        if (result.Effect?.Verified == true)
            return result with { Content = protectedContent };
        return ToolResult.ExecutedUnverified(
            protectedContent,
            "RemoteEffectNotIndependentlyVerified",
            "Remote MCP text is an untrusted observation; it cannot prove a consequential effect.",
            retryable: false,
            evidence: ["source=remote_mcp", "trust=untrusted_external_content"]);
    }
}
