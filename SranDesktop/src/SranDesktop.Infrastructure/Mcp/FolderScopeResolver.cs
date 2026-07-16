using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Mcp;

/// <summary>
/// Folder-scoped consent (Phase A PART 4). Reduces prompt COUNT within
/// already-allowed roots — it never widens reach: <see cref="PathGuard"/> and
/// the app-state deny still gate every actual access.
///
/// - FileSystem writes → the target's containing granted/allowed folder, so
///   "Always allow" trusts the whole project folder, not one file. (Reads are
///   AllowAlways and never reach consent.)
/// - Shell → the command's working directory (explicit, else the conversation's
///   retained cwd, else the user profile) mapped to its containing root, so
///   "Always allow" trusts commands in THIS project folder — not every future
///   command everywhere, as the old blanket "*" did.
/// </summary>
public sealed class FolderScopeResolver : IToolScopeResolver
{
    private readonly ShellSession? _shell;

    public FolderScopeResolver(ShellSession? shell = null) => _shell = shell;

    public string Resolve(IMcpTool tool, string argumentsJson, string conversationId)
    {
        if (tool.Category == ToolCategory.FileSystem)
            return ScopeForPath(ReadPathArg(argumentsJson, "path", "destination", "directory"));

        if (tool.Category == ToolCategory.Shell)
        {
            var wd = ReadPathArg(argumentsJson, "working_directory")
                     ?? _shell?.GetWorkingDirectory(conversationId)
                     ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return ScopeForPath(wd);
        }

        // Delegation / network tools have no path — a single scope is fine; they
        // carry no direct local side effect of their own.
        return "*";
    }

    private static string ScopeForPath(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
            return "*";

        var full = PathGuard.Resolve(raw, out _);
        if (full is null)
        {
            // Outside every allowed root: a unique, non-wildcard scope so it can
            // never be blanket-approved. The tool still denies it at execution.
            try { return System.IO.Path.GetFullPath(raw); }
            catch (Exception ex) when (ex is ArgumentException or NotSupportedException or System.IO.PathTooLongException)
            {
                return raw;
            }
        }

        return PathGuard.ContainingRoot(full) ?? full;
    }

    private static string? ReadPathArg(string json, params string[] names)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return null;
            foreach (var name in names)
            {
                if (doc.RootElement.TryGetProperty(name, out var el)
                    && el.ValueKind == JsonValueKind.String
                    && el.GetString() is { Length: > 0 } value)
                    return value;
            }
        }
        catch (JsonException)
        {
        }
        return null;
    }
}
