using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class SearchFilesTool : IMcpTool
{
    private const int MaxMatches = 100;
    private const long MaxGrepFileBytes = 2 * 1024 * 1024;

    public string Name => "search_files";
    public string Description =>
        "Search for files matching a glob pattern under a directory; optionally grep their contents with a regex.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

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
        var directory = PathGuard.Resolve(ToolHelpers.GetString(arguments, "directory"), out var error);
        if (directory is null)
            return ToolResult.Fail(error!);
        if (!Directory.Exists(directory))
            return ToolResult.Fail($"Directory not found: {directory}");

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

        var sb = new StringBuilder();
        var matches = 0;

        foreach (var file in Directory.EnumerateFiles(directory, pattern, new EnumerationOptions
                 {
                     RecurseSubdirectories = true,
                     IgnoreInaccessible = true
                 }))
        {
            ct.ThrowIfCancellationRequested();
            // A recursive search under an allowed parent (e.g. %APPDATA%)
            // must not read or list the app's own state files (Phase 10 D1).
            if (PathGuard.IsProtectedStatePath(file))
                continue;
            if (matches >= MaxMatches)
            {
                sb.AppendLine($"[...truncated at {MaxMatches} matches]");
                break;
            }

            if (contentRegex is null)
            {
                sb.AppendLine(file);
                matches++;
                continue;
            }

            if (new FileInfo(file).Length > MaxGrepFileBytes)
                continue;

            var lineNumber = 0;
            foreach (var line in await File.ReadAllLinesAsync(file, ct))
            {
                lineNumber++;
                bool isMatch;
                try
                {
                    isMatch = contentRegex.IsMatch(line);
                }
                catch (RegexMatchTimeoutException)
                {
                    continue;
                }

                if (!isMatch) continue;
                sb.AppendLine($"{file}:{lineNumber}: {(line.Length > 200 ? line[..200] + "..." : line.Trim())}");
                if (++matches >= MaxMatches) break;
            }
        }

        return ToolResult.Ok(matches == 0 ? "No matches found." : sb.ToString().TrimEnd());
    }
}
