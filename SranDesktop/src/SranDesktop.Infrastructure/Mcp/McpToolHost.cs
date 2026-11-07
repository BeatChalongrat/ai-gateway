using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Remote;

namespace SranDesktop.Infrastructure.Mcp;

public sealed class McpToolHost : IMcpToolHost
{
    private readonly McpToolRegistry _registry;
    private readonly IMcpConnectorService? _connectors;

    public McpToolHost(McpToolRegistry registry, IMcpConnectorService? connectors = null)
    {
        _registry = registry;
        _connectors = connectors;
    }

    public IReadOnlyList<IMcpTool> GetRegisteredTools() => _registry.All;

    public IMcpTool? Resolve(string name)
    {
        if (_registry.Resolve(name) is { } local)
            return local;

        // Remote MCP tools resolve through the connector cache (R10 1C).
        if (_connectors is not null && _connectors.TryResolveRemoteTool(name, out _))
        {
            var def = _connectors.GetCachedRemoteToolDefinitions()
                .FirstOrDefault(d => d.Function.Name == name);
            if (def is not null)
                return new RemoteMcpTool(_connectors, name,
                    def.Function.Description, def.Function.Parameters);
        }
        return null;
    }

    public IReadOnlyList<GatewayToolDefinition> GetToolDefinitions()
    {
        var defs = _registry.All
            // web_search is only offered while the composer toggle is on (R9 C)
            .Where(t => t.Name != "web_search" || WebSearchState.Enabled)
            .Select(t => new GatewayToolDefinition
            {
                Function = new GatewayFunctionDef
                {
                    Name = t.Name,
                    Description = t.Description,
                    Parameters = t.ParametersSchema
                }
            }).ToList();

        // Tools from connected MCP servers (cached — no network here). Local
        // tools win name collisions.
        if (_connectors is not null)
        {
            var localNames = defs.Select(d => d.Function.Name).ToHashSet(StringComparer.Ordinal);
            defs.AddRange(_connectors.GetCachedRemoteToolDefinitions()
                .Where(d => !localNames.Contains(d.Function.Name)));
        }
        return defs;
    }
}
