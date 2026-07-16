using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Move or rename a file or directory (Phase A PART 2). BOTH endpoints are
/// PathGuard-confined (source and destination must be within allowed/granted
/// roots and never the app's own state), and it is consent-gated like other
/// write tools. A first-class tool is safer than shelling out through
/// run_command for a common operation.
/// </summary>
public sealed class MoveFileTool : IMcpTool
{
    public string Name => "move_file";
    public string Description =>
        "Move or rename a file or directory. Both the source and destination must be inside allowed folders.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "source": { "type": "string", "description": "Absolute path of the file or directory to move" },
                "destination": { "type": "string", "description": "Absolute destination path" },
                "overwrite": { "type": "boolean", "description": "Overwrite an existing destination file (default false)" }
            },
            "required": ["source", "destination"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var source = PathGuard.Resolve(ToolHelpers.GetString(arguments, "source"), out var srcError);
        if (source is null)
            return Task.FromResult(ToolResult.Fail(srcError!));

        var destination = PathGuard.Resolve(ToolHelpers.GetString(arguments, "destination"), out var dstError);
        if (destination is null)
            return Task.FromResult(ToolResult.Fail(dstError!));

        var overwrite = ToolHelpers.GetBool(arguments, "overwrite");
        var isDirectory = Directory.Exists(source);

        if (!isDirectory && !File.Exists(source))
            return Task.FromResult(ToolResult.Fail($"Source not found: {source}"));

        var parent = Path.GetDirectoryName(destination);
        if (parent is not null)
            Directory.CreateDirectory(parent);

        try
        {
            if (isDirectory)
            {
                if (Directory.Exists(destination))
                    return Task.FromResult(ToolResult.Fail($"Destination directory already exists: {destination}"));
                Directory.Move(source, destination);
            }
            else
            {
                if (File.Exists(destination) && !overwrite)
                    return Task.FromResult(ToolResult.Fail(
                        $"Destination already exists: {destination}. Set overwrite to replace it."));
                File.Move(source, destination, overwrite);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return Task.FromResult(ToolResult.Fail($"Move failed: {ex.Message}"));
        }

        return Task.FromResult(ToolResult.Ok($"Moved {source} to {destination}"));
    }
}
