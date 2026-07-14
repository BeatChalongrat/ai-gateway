using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ReadFileTool : IMcpTool
{
    private const long MaxFileBytes = 10 * 1024 * 1024;

    public string Name => "read_file";
    public string Description =>
        "Read the contents of a file at the given path. Returns text for text files, base64 for small binary files.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "Absolute path of the file to read" }
            },
            "required": ["path"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return ToolResult.Fail(error!);

        if (!File.Exists(path))
            return ToolResult.Fail($"File not found: {path}");

        var info = new FileInfo(path);
        if (info.Length > MaxFileBytes)
            return ToolResult.Fail($"File too large: {ToolHelpers.FormatSize(info.Length)} (max 10 MB).");

        if (await IsBinaryAsync(path, ct))
        {
            var bytes = await File.ReadAllBytesAsync(path, ct);
            return ToolResult.Ok(
                $"[binary file {info.Name}, {ToolHelpers.FormatSize(info.Length)}, base64]\n" +
                Convert.ToBase64String(bytes));
        }

        var content = await File.ReadAllTextAsync(path, ct);
        return ToolResult.Ok(content.Length == 0 ? "[empty file]" : content);
    }

    private static async Task<bool> IsBinaryAsync(string path, CancellationToken ct)
    {
        var buffer = new byte[8192];
        await using var fs = File.OpenRead(path);
        var read = await fs.ReadAsync(buffer, ct);
        return buffer.AsSpan(0, read).Contains((byte)0);
    }
}
