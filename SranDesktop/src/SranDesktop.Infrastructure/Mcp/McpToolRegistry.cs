using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Mcp;

public sealed class McpToolRegistry
{
    private readonly Dictionary<string, IMcpTool> _tools = new(StringComparer.OrdinalIgnoreCase);

    public void Register(IMcpTool tool) => _tools[tool.Name] = tool;

    public IMcpTool? Resolve(string name) => _tools.GetValueOrDefault(name);

    public IReadOnlyList<IMcpTool> All => _tools.Values.ToList();
}
