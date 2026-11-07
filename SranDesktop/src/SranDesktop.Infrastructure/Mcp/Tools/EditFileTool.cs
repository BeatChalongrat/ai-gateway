using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Targeted string-replacement edit (Phase A PART 2) — the primitive a real
/// coding agent uses to change part of a file without re-emitting the whole
/// thing. <c>old_string</c> must match EXACTLY and be UNIQUE in the file (else
/// the model is told to add surrounding context), unless <c>replace_all</c> is
/// set. Same PathGuard confinement and consent path as write_file.
/// </summary>
public sealed class EditFileTool : IMcpTool
{
    private const long MaxFileBytes = 10 * 1024 * 1024;

    public string Name => "edit_file";
    public string Description =>
        "Edit an existing file by replacing an exact string. 'old_string' must appear EXACTLY once " +
        "(include enough surrounding context to make it unique) unless 'replace_all' is true. " +
        "Prefer this over write_file for changing part of an existing file — you don't re-send the whole file.";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": { "type": "string", "description": "Absolute path of the file to edit" },
                "old_string": { "type": "string", "description": "The exact text to replace. Must be unique in the file unless replace_all is true." },
                "new_string": { "type": "string", "description": "The replacement text." },
                "replace_all": { "type": "boolean", "description": "Replace every occurrence instead of requiring a unique match (default false)." }
            },
            "required": ["path", "old_string", "new_string"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return ToolResult.Fail(error!);

        var oldString = ToolHelpers.GetString(arguments, "old_string");
        var newString = ToolHelpers.GetString(arguments, "new_string");
        if (string.IsNullOrEmpty(oldString))
            return ToolResult.Fail("'old_string' is required and cannot be empty. To create a new file, use write_file.");
        if (newString is null)
            return ToolResult.Fail("'new_string' is required (use an empty string to delete the matched text).");
        if (oldString == newString)
            return ToolResult.Fail("'old_string' and 'new_string' are identical — nothing to change.");

        if (!File.Exists(path))
            return ToolResult.Fail($"File not found: {path}. To create a new file, use write_file.");

        var info = new FileInfo(path);
        if (info.Length > MaxFileBytes)
            return ToolResult.Fail($"File too large to edit: {ToolHelpers.FormatSize(info.Length)} (max 10 MB).");

        var content = await File.ReadAllTextAsync(path, ct);
        var replaceAll = ToolHelpers.GetBool(arguments, "replace_all");

        var occurrences = CountOccurrences(content, oldString);
        if (occurrences == 0)
            return ToolResult.Fail(
                "'old_string' was not found in the file. It must match the file's current contents " +
                "exactly (including whitespace and indentation). Read the file to see the current text.");
        if (occurrences > 1 && !replaceAll)
            return ToolResult.Fail(
                $"'old_string' matched {occurrences} places in the file, so the edit is ambiguous. " +
                "Add more surrounding context to make it unique, or set replace_all to change every occurrence.");

        var firstIndex = content.IndexOf(oldString, StringComparison.Ordinal);
        var updated = replaceAll
            ? content.Replace(oldString, newString)
            : string.Concat(content.AsSpan(0, firstIndex), newString,
                content.AsSpan(firstIndex + oldString.Length));

        var backup = ToolHelpers.BackupBeforeOverwrite(path); // FIX-G
        await File.WriteAllTextAsync(path, updated, Encoding.UTF8, ct);

        var line = LineNumberAt(content, firstIndex);
        var backupNote = backup is null ? "" : $" Previous version saved to {backup}";
        return ToolResult.Ok(replaceAll
            ? $"Replaced {occurrences} occurrence(s) in {path}.{backupNote}"
            : $"Edited {path} at line {line} (1 replacement).{backupNote}");
    }

    private static int CountOccurrences(string haystack, string needle)
    {
        var count = 0;
        var i = 0;
        while ((i = haystack.IndexOf(needle, i, StringComparison.Ordinal)) >= 0)
        {
            count++;
            i += needle.Length;
        }
        return count;
    }

    private static int LineNumberAt(string content, int index)
    {
        var line = 1;
        for (var i = 0; i < index && i < content.Length; i++)
            if (content[i] == '\n')
                line++;
        return line;
    }
}
