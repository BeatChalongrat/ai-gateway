using System.Security.Cryptography;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ReadFileTool : IMcpTool, ISensitiveDataTool
{
    private const long MaxFileBytes = 10 * 1024 * 1024;
    private readonly IWorkspaceFileBroker _files;

    public ReadFileTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

    public string Name => "read_file";
    public string Description =>
        "Read the contents of a file at the given path. Returns text for text files, base64 for small binary files.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "WorkspaceFile.ReadLocal",
        "WorkspaceFile.SendToModel",
        "workspace_file",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: (int)MaxFileBytes);

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
        var path = ToolHelpers.GetString(arguments, "path");
        if (string.IsNullOrWhiteSpace(path))
            return ToolResult.Fail("'path' is required.");
        try
        {
            var snapshot = await _files.ReadFileAsync(path, MaxFileBytes, ct);
            if (snapshot.Bytes.AsSpan().Contains((byte)0))
                return ToolResult.Verified(
                    Application.Services.UntrustedObservationBoundary.ProtectIfUntrusted(
                        Name,
                        $"[binary file {snapshot.Name}, {ToolHelpers.FormatSize(snapshot.Bytes.LongLength)}, base64]\n" +
                        Convert.ToBase64String(snapshot.Bytes)),
                    [$"file_path={snapshot.FullPath}", $"file_sha256={snapshot.Sha256}"]);

            var content = System.Text.Encoding.UTF8.GetString(snapshot.Bytes);
            return ToolResult.Verified(
                Application.Services.UntrustedObservationBoundary.ProtectIfUntrusted(
                    Name,
                    content.Length == 0 ? "[empty file]" : content),
                [$"file_path={snapshot.FullPath}",
                    $"file_sha256={snapshot.Sha256}",
                    $"file_length={snapshot.Bytes.Length}"]);
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
