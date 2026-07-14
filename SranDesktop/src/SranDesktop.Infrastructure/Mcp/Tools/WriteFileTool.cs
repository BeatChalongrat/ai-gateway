using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class WriteFileTool : IMcpTool
{
    public string Name => "write_file";
    public string Description => "Create or overwrite a file with the given text content.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "Absolute path of the file to write" },
                "content": { "type": "string", "description": "Full text content of the file" }
            },
            "required": ["path", "content"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return ToolResult.Fail(error!);

        var content = ToolHelpers.GetString(arguments, "content");
        if (content is null)
            return ToolResult.Fail("'content' is required.");

        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await File.WriteAllTextAsync(path, content, Encoding.UTF8, ct);

        return ToolResult.Ok($"Written {Encoding.UTF8.GetByteCount(content)} bytes to {path}");
    }
}
