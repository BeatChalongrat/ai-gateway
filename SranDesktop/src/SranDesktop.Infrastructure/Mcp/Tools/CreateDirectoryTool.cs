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
    private readonly IWorkspaceFileBroker _files;

    public CreateDirectoryTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

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

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = ToolHelpers.GetString(arguments, "path");
        if (string.IsNullOrWhiteSpace(path))
            return ToolResult.Fail("'path' is required.");
        try
        {
            await _files.CreateDirectoryAsync(path, ct);
            return ToolResult.Ok($"Created or verified directory {path}");
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or ArgumentException
                                   or NotSupportedException)
        {
            return ToolResult.Fail(ex.Message);
        }
    }
}
