using System.Text.Json;
using System.Security.Cryptography;
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
    private readonly IWorkspaceFileBroker _files;

    public MoveFileTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

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

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var source = ToolHelpers.GetString(arguments, "source");
        var destination = ToolHelpers.GetString(arguments, "destination");
        if (string.IsNullOrWhiteSpace(source) || string.IsNullOrWhiteSpace(destination))
            return ToolResult.Fail("'source' and 'destination' are required.");
        var overwrite = ToolHelpers.GetBool(arguments, "overwrite");
        try
        {
            await _files.MoveAsync(source, destination, overwrite, ct);
            return ToolResult.Verified(
                $"Moved and verified {source} to {destination}.",
                ["source_absent=true", "destination_present=true"],
                [$"moved_from={source}", $"moved_to={destination}"]);
        }
        catch (Exception ex) when (ex is IOException
                                   or UnauthorizedAccessException
                                   or ArgumentException
                                   or NotSupportedException)
        {
            return ToolResult.Fail($"Move failed: {ex.Message}");
        }
    }
}
