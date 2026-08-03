using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Mcp;

/// <summary>
/// FIX-F: read tools are consent-free by default, which used to mean the agent
/// could read %USERPROFILE%\.ssh or enumerate the whole profile silently. This
/// guard flags read-class calls whose target lies OUTSIDE the user's chosen
/// scope (working folder + granted folders — <see cref="PathGuard.IsWithinUserScope"/>)
/// so the orchestrator asks first. In-scope reads stay friction-free.
/// </summary>
public sealed class SensitiveReadGuard : ISensitiveReadGuard
{
    /// <summary>Read-class tools and the argument that names their target.</summary>
    private static readonly Dictionary<string, string> PathArgByTool = new(StringComparer.OrdinalIgnoreCase)
    {
        ["read_file"] = "path",
        ["list_directory"] = "path",
        ["search_files"] = "directory",
    };

    public bool RequiresConsent(IMcpTool tool, string argumentsJson)
    {
        if (!PathArgByTool.TryGetValue(tool.Name, out var argName))
            return false;

        string? raw;
        try
        {
            using var doc = JsonDocument.Parse(
                string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
            raw = doc.RootElement.ValueKind == JsonValueKind.Object
                  && doc.RootElement.TryGetProperty(argName, out var el)
                  && el.ValueKind == JsonValueKind.String
                ? el.GetString()
                : null;
        }
        catch (JsonException)
        {
            return false; // malformed args fail in the tool with a clear error
        }
        if (string.IsNullOrWhiteSpace(raw))
            return false;

        var full = PathGuard.Resolve(raw, out _);
        if (full is null)
            return false; // outside every allowed root — PathGuard denies it anyway

        return !PathGuard.IsWithinUserScope(full);
    }
}
