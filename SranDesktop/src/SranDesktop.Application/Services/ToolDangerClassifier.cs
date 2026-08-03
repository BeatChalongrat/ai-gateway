using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

/// <summary>Whether a tool warrants the prominent consent danger banner, and why
/// (a localization key). (Phase C PART 3).</summary>
public sealed record ToolDanger(bool IsDangerous, string ReasonKey);

/// <summary>
/// Derives consent "danger" from CAPABILITY (category + a small explicit set),
/// not a brittle hardcoded name list (Phase C PART 3). Fail-SAFE: only known
/// read/meta tools are calm; anything in a side-effecting category — or an
/// unknown category — is dangerous by default, so a newly added side-effecting
/// tool gets the warning automatically. This only ever ADDS warnings; it never
/// removes a consent gate.
/// </summary>
public static class ToolDangerClassifier
{
    /// <summary>Known read-only / side-effect-free tools that stay calm. Everything
    /// else is judged by category (fail-safe).</summary>
    private static readonly HashSet<string> Safe = new(StringComparer.OrdinalIgnoreCase)
    {
        "read_file", "read_document", "list_directory", "search_files", "read_clipboard",
        "web_search", "web_fetch", "run_subagent", "update_plan", "use_skill", "remember",
        // Computer-use READ tools: they only observe the screen/window tree.
        "computer_zoom", "get_computer_state", "read_screen_elements",
        // Browser READ tools (Chrome extension bridge): observation only.
        "browser_read_page", "browser_find", "browser_list_tabs", "browser_screenshot",
    };

    public static ToolDanger Classify(string toolName, ToolCategory category)
    {
        if (Safe.Contains(toolName))
            return new ToolDanger(false, "");

        // High-signal explicit case: sandboxed-but-still-code execution.
        if (string.Equals(toolName, "run_python", StringComparison.OrdinalIgnoreCase))
            return new ToolDanger(true, "danger.code");

        return category switch
        {
            ToolCategory.Shell => new ToolDanger(true, "danger.shell"),
            ToolCategory.Screen => new ToolDanger(true, "danger.computerUse"),
            ToolCategory.Browser => new ToolDanger(true, "danger.browser"),
            ToolCategory.Clipboard => new ToolDanger(true, "danger.clipboard"), // write (read is safe-listed)
            ToolCategory.FileSystem => new ToolDanger(true, "danger.fileWrite"), // write (reads safe-listed)
            ToolCategory.Network => new ToolDanger(true, "danger.connector"),    // remote MCP / unknown
            _ => new ToolDanger(true, "danger.generic"),                          // unknown → fail-safe
        };
    }
}
