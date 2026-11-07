using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ListDirectoryTool : IMcpTool
{
    private const int MaxEntries = 1000;

    public string Name => "list_directory";
    public string Description => "List files and directories at the given path.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

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

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return Task.FromResult(ToolResult.Fail(error!));

        if (!Directory.Exists(path))
            return Task.FromResult(ToolResult.Fail($"Directory not found: {path}"));

        var recursive = ToolHelpers.GetBool(arguments, "recursive");
        var option = recursive ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly;

        var sb = new StringBuilder();
        var count = 0;
        try
        {
            foreach (var entry in Directory.EnumerateFileSystemEntries(path, "*", option))
            {
                ct.ThrowIfCancellationRequested();
                // A recursive listing of an allowed parent (e.g. %APPDATA%)
                // must not surface the app's own state subtree (Phase 10 D1).
                if (PathGuard.IsProtectedStatePath(entry))
                    continue;
                // Internal undo snapshots (BackupBeforeOverwrite) are not user
                // content — keep them off the listing so an edited folder looks
                // clean; the edit/move result still names the backup for undo.
                if (entry.EndsWith(ToolHelpers.BackupSuffix, StringComparison.OrdinalIgnoreCase))
                    continue;
                if (++count > MaxEntries)
                {
                    sb.AppendLine($"[...truncated at {MaxEntries} entries]");
                    break;
                }

                var relative = Path.GetRelativePath(path, entry);
                if (Directory.Exists(entry))
                    sb.AppendLine($"D  {relative}{Path.DirectorySeparatorChar}");
                else
                    sb.AppendLine($"F  {relative}  ({ToolHelpers.FormatSize(new FileInfo(entry).Length)})");
            }
        }
        catch (UnauthorizedAccessException ex)
        {
            sb.AppendLine($"[some entries skipped: {ex.Message}]");
        }

        return Task.FromResult(ToolResult.Ok(
            count == 0 ? "[empty directory]" : sb.ToString().TrimEnd()));
    }
}
