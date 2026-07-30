using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class SearchFilesTool : IMcpTool, ISensitiveDataTool
{
    private const int MaxMatches = 100;
    private const long MaxGrepFileBytes = 2 * 1024 * 1024;
    private readonly IWorkspaceFileBroker _files;

    public SearchFilesTool(IWorkspaceFileBroker? files = null) =>
        _files = files ?? WorkspaceFileBroker.Shared;

    public string Name => "search_files";
    public string Description =>
        "Search for files matching a glob pattern under a directory; optionally grep their contents with a regex.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "Workspace.SearchLocal",
        "WorkspaceSearch.SendToModel",
        "workspace_search_result",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "directory": { "type": "string", "description": "Directory to search under" },
                "pattern": { "type": "string", "description": "File name glob, e.g. *.txt" },
                "content_pattern": { "type": "string", "description": "Optional regex to match inside files" }
            },
            "required": ["directory", "pattern"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var directory = ToolHelpers.GetString(arguments, "directory");
        if (string.IsNullOrWhiteSpace(directory))
            return ToolResult.Fail("'directory' is required.");

        var pattern = ToolHelpers.GetString(arguments, "pattern");
        if (string.IsNullOrWhiteSpace(pattern))
            return ToolResult.Fail("'pattern' is required.");

        Regex? contentRegex = null;
        if (ToolHelpers.GetString(arguments, "content_pattern") is { Length: > 0 } cp)
        {
            try
            {
                contentRegex = new Regex(cp, RegexOptions.None, TimeSpan.FromSeconds(2));
            }
            catch (ArgumentException ex)
            {
                return ToolResult.Fail($"Invalid content_pattern regex: {ex.Message}");
            }
        }

        try
        {
            var found = await _files.SearchAsync(
                directory,
                pattern,
                contentRegex,
                MaxMatches,
                MaxGrepFileBytes,
                ct);
            var sb = new StringBuilder();
            foreach (var match in found)
            {
                if (match.LineNumber is null)
                    sb.AppendLine(match.FullPath);
                else
                    sb.AppendLine(
                        $"{match.FullPath}:{match.LineNumber}: " +
                        $"{(match.Line!.Length > 200 ? match.Line[..200] + "..." : match.Line.Trim())}");
            }
            if (found.Count == MaxMatches)
                sb.AppendLine($"[...truncated at {MaxMatches} matches]");
            return ToolResult.Verified(
                Application.Services.UntrustedObservationBoundary.ProtectIfUntrusted(
                    Name,
                    found.Count == 0 ? "No matches found." : sb.ToString().TrimEnd()),
                [$"search_matches_observed={found.Count}"]);
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
