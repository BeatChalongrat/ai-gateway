using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ListDirectoryTool : IMcpTool, ISensitiveDataTool
{
    private const int MaxEntries = 1000;
    private readonly IWorkspaceFileBroker _files;

    public ListDirectoryTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

    public string Name => "list_directory";
    public string Description => "List files and directories at the given path.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "Workspace.ListLocal",
        "WorkspaceListing.SendToModel",
        "workspace_metadata",
        "internal",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "Absolute directory path" },
                "recursive": { "type": "boolean", "description": "Include subdirectories (default false)" }
            },
            "required": ["path"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = ToolHelpers.GetString(arguments, "path");
        if (string.IsNullOrWhiteSpace(path))
            return ToolResult.Fail("'path' is required.");
        var recursive = ToolHelpers.GetBool(arguments, "recursive");
        try
        {
            var entries = await _files.ListAsync(path, recursive, MaxEntries, ct);
            var sb = new StringBuilder();
            foreach (var entry in entries)
            {
                if (entry.IsDirectory)
                    sb.AppendLine($"D  {entry.RelativePath}{Path.DirectorySeparatorChar}");
                else
                    sb.AppendLine($"F  {entry.RelativePath}  ({ToolHelpers.FormatSize(entry.Length)})");
            }
            if (entries.Count == MaxEntries)
                sb.AppendLine($"[...truncated at {MaxEntries} entries]");
            // Reports the observation itself, not just "the call returned". The
            // runtime used to infer "effect observed" from a read-only tool that
            // merely succeeded, which credited any successful read with having
            // seen whatever the goal wanted; that inference is gone, so a tool
            // that really did observe something has to say so.
            return ToolResult.Verified(
                entries.Count == 0 ? "[empty directory]" : sb.ToString().TrimEnd(),
                [$"directory_entries_observed={entries.Count}", $"listed_path={path}"]);
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
