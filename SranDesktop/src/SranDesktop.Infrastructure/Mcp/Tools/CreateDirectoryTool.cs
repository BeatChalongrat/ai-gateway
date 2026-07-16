using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Create a directory (and any missing parents — "mkdir -p") within an allowed
/// root (Phase A PART 2). PathGuard-confined and consent-gated like other write
/// tools; a real agent needs this to lay out a project folder.
/// </summary>
public sealed class CreateDirectoryTool : IMcpTool
{
    public string Name => "create_directory";
    public string Description => "Create a directory, including any missing parent directories (mkdir -p).";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "Absolute path of the directory to create" }
            },
            "required": ["path"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return Task.FromResult(ToolResult.Fail(error!));

        if (File.Exists(path))
            return Task.FromResult(ToolResult.Fail($"A file already exists at {path}."));

        if (Directory.Exists(path))
            return Task.FromResult(ToolResult.Ok($"Directory already exists: {path}"));

        Directory.CreateDirectory(path);
        return Task.FromResult(ToolResult.Ok($"Created directory {path}"));
    }
}
