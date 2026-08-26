using System.Text.Json;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// Ports the WPF AgentExecutionViewModel's argument humanisation (which lives
/// in Presentation and cannot be referenced here) so the shell's tool cards
/// show the same one-line argument summaries and expanded input the WPF cards
/// do. Match the information, not the pixels (plan §3).
///
/// Never returns raw secrets verbatim — values are truncated; the coordinator
/// additionally never logs any of this (a tool argument can carry file
/// contents, tokens, clipboard text).
/// </summary>
public static class ToolCardFormatter
{
    /// <summary>One-line salient argument summary for the card header.</summary>
    public static string Humanize(string toolName, string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return Compact(json);
            var r = doc.RootElement;

            string? S(string n) => r.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
            string? I(string n) => r.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetRawText() : null;

            var summary = toolName switch
            {
                "list_directory" or "create_directory" => Leaf(S("path")),
                "read_file" or "write_file" or "edit_file" => Leaf(S("path")),
                "read_document" => Leaf(S("path")),
                // Parent+name on both sides: a bare "name → name" reads like a
                // no-op when only the FOLDER changed (the S3 finding).
                "move_file" => JoinNonEmpty(" → ", LeafWithParent(S("source")), LeafWithParent(S("destination"))),
                "search_files" => S("pattern") ?? S("content_pattern"),
                "run_command" => Trunc(S("command"), 70),
                "run_python" => Trunc(S("code"), 70),
                "web_search" or "browser_find" => S("query"),
                "web_fetch" or "open_url" or "browser_navigate" => Trunc(S("url"), 70),
                "open_application" or "use_skill" => S("name"),
                "remember" => Trunc(S("note"), 70),
                "run_subagent" => Trunc(S("task"), 70),
                "write_clipboard" => Trunc(S("text"), 60),
                "computer_click" or "computer_scroll" => I("x") is { } x && I("y") is { } y ? $"({x}, {y})" : null,
                "computer_type" => Trunc(S("text"), 40),
                "computer_key" => S("keys"),
                "generate_image" or "generate_video" => Trunc(S("prompt"), 70),
                _ => null,
            };
            return summary ?? Compact(json);
        }
        catch (JsonException)
        {
            return Compact(json);
        }
    }

    /// <summary>Expanded "name: value" lines for the card body (values capped).</summary>
    public static string BuildInput(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return Compact(json);
            var lines = doc.RootElement.EnumerateObject()
                .Select(p => $"{p.Name}: {ValueText(p.Value)}")
                .Take(12);
            return string.Join("\n", lines);
        }
        catch (JsonException)
        {
            return Compact(json);
        }
    }

    /// <summary>The "code" argument verbatim (run_python code box).</summary>
    public static string? ExtractCode(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            return doc.RootElement.ValueKind == JsonValueKind.Object
                && doc.RootElement.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String
                ? c.GetString() : null;
        }
        catch (JsonException) { return null; }
    }

    private static string ValueText(JsonElement v) => v.ValueKind switch
    {
        JsonValueKind.String => Trunc(v.GetString(), 400) ?? "",
        JsonValueKind.Number => v.GetRawText(),
        JsonValueKind.True => "✓",
        JsonValueKind.False => "✗",
        JsonValueKind.Array => $"[{v.GetArrayLength()} items]",
        JsonValueKind.Object => "{…}",
        _ => "null",
    };

    private static string? Leaf(string? path)
    {
        if (string.IsNullOrWhiteSpace(path)) return path;
        var trimmed = path.TrimEnd('/', '\\');
        var idx = trimmed.LastIndexOfAny(new[] { '/', '\\' });
        return idx >= 0 && idx < trimmed.Length - 1 ? trimmed[(idx + 1)..] : trimmed;
    }

    /// <summary>Last two path segments ("folder/name") so a move whose filename
    /// is unchanged still shows the folder that changed. Falls back to the leaf
    /// when there is no parent segment.</summary>
    private static string? LeafWithParent(string? path)
    {
        if (string.IsNullOrWhiteSpace(path)) return path;
        var trimmed = path.TrimEnd('/', '\\');
        var last = trimmed.LastIndexOfAny(new[] { '/', '\\' });
        if (last <= 0) return Leaf(trimmed);
        var prev = trimmed.LastIndexOfAny(new[] { '/', '\\' }, last - 1);
        var seg = prev >= 0 ? trimmed[(prev + 1)..] : trimmed;
        return seg.Replace('/', '\\');
    }

    private static string? Trunc(string? s, int max)
    {
        if (s is null) return null;
        s = s.Replace('\n', ' ').Replace('\r', ' ').Trim();
        return s.Length <= max ? s : s[..max] + "…";
    }

    private static string JoinNonEmpty(string sep, params string?[] parts) =>
        string.Join(sep, parts.Where(p => !string.IsNullOrWhiteSpace(p)));

    private static string Compact(string json)
    {
        var s = (json ?? "").Replace('\n', ' ').Replace('\r', ' ').Trim();
        return s.Length <= 80 ? s : s[..80] + "…";
    }
}
