using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// IMcpTool adapter for a tool living on a remote MCP server (R10 1C):
/// resolution and consent flow stay identical to local tools, execution
/// proxies through the connector service.
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

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
        _connectors.ExecuteRemoteToolAsync(Name, arguments, ct);
}
