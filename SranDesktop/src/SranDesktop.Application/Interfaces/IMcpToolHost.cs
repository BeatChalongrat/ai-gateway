using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Interfaces;

/// <summary>A locally-executed desktop tool exposed to the model.</summary>
public interface IMcpTool
{
    string Name { get; }
    string Description { get; }
    JsonElement ParametersSchema { get; }
    ToolCategory Category { get; }
    PermissionLevel DefaultPermission { get; }

    Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct);
}

public interface IMcpToolHost
{
    IReadOnlyList<IMcpTool> GetRegisteredTools();
    IMcpTool? Resolve(string name);
    IReadOnlyList<GatewayToolDefinition> GetToolDefinitions();
}
