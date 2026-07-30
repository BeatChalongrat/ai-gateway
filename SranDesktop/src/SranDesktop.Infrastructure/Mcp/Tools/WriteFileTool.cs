using System.Text;
using System.Security.Cryptography;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class WriteFileTool : IMcpTool
{
    private readonly IWorkspaceFileBroker _files;

    public WriteFileTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

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
        var path = ToolHelpers.GetString(arguments, "path");
        if (string.IsNullOrWhiteSpace(path))
            return ToolResult.Fail("'path' is required.");
        var content = ToolHelpers.GetString(arguments, "content");
        if (content is null)
            return ToolResult.Fail("'content' is required.");

        try
        {
            var receipt = await _files.WriteTextAsync(path, content, ct);
            return ToolResult.Verified(
                $"Written and verified {receipt.Length} bytes at {receipt.FullPath} " +
                $"(SHA-256 {receipt.Sha256}).",
                [$"file_sha256={receipt.Sha256}", $"file_length={receipt.Length}"],
                [$"file_written={receipt.FullPath}"]);
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
