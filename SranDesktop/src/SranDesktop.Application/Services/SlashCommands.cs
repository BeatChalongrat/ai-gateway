using System.Text.RegularExpressions;

namespace SranDesktop.Application.Services;

/// <summary>Which existing flow a slash command is a shortcut into.</summary>
public enum SlashActionId
{
    Image,
    Video,
    NewChat,
    Shortcuts,
}

/// <summary>One quick action offered by the composer's slash menu.</summary>
public sealed record SlashCommand(
    SlashActionId Id,
    /// <summary>Typed form, without the leading slash.</summary>
    string Keyword,
    /// <summary>Dictionary key for the label.</summary>
    string LabelKey,
    /// <summary>Dictionary key for the one-line hint.</summary>
    string HintKey,
    /// <summary>Extra spellings that should find this action.</summary>
    string[] Aliases);

/// <summary>
/// Slash quick actions for the desktop composer — the same behaviour the web
/// composer got in Phase 10 (apps/web-chat/src/lib/slashCommands.ts), ported
/// so the two clients answer "/" identically.
///
/// A slash command is a SHORTCUT INTO AN EXISTING FLOW, never a new
/// capability: image/video mode toggles, new chat, and the shortcuts sheet
/// all already exist here.
///
/// Trigger rule (identical to web): the menu opens only when the WHOLE input
/// is a slash token — "/" possibly followed by letters, nothing before it.
/// So "and/or", "24/7", a URL path, a leading space, and "/image of a cat"
/// (the token ended at the space) are all ordinary text.
/// </summary>
public static class SlashCommands
{
    public static readonly IReadOnlyList<SlashCommand> All = new[]
    {
        new SlashCommand(SlashActionId.Image, "image", "slash.image", "slash.image.hint",
            new[] { "img", "pic" }),
        new SlashCommand(SlashActionId.Video, "video", "slash.video", "slash.video.hint",
            new[] { "vid" }),
        new SlashCommand(SlashActionId.NewChat, "new", "slash.new", "slash.new.hint",
            Array.Empty<string>()),
        new SlashCommand(SlashActionId.Shortcuts, "shortcuts", "slash.shortcuts",
            "slash.shortcuts.hint", new[] { "keys", "help" }),
    };

    // No trimming: leading whitespace means the user is writing prose that
    // happens to contain a slash, not invoking a command.
    private static readonly Regex TokenPattern =
        new(@"^/([a-zA-Z]*)$", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    /// <summary>The typed slash token, or null when the input isn't one.
    /// An empty string means a bare "/" — show everything.</summary>
    public static string? Query(string? text)
    {
        if (text is null) return null;
        var m = TokenPattern.Match(text);
        return m.Success ? m.Groups[1].Value.ToLowerInvariant() : null;
    }

    /// <summary>True when the composer should show the slash menu.</summary>
    public static bool ShouldShowMenu(string? text) => Query(text) is not null;

    /// <summary>Commands matching the typed token, in registry order. An empty
    /// query lists everything; an unknown one lists nothing, so the menu shows
    /// its empty state instead of silently vanishing.</summary>
    public static IReadOnlyList<SlashCommand> Filter(string query)
    {
        var q = (query ?? string.Empty).ToLowerInvariant();
        if (q.Length == 0) return All;
        return All.Where(c =>
                c.Keyword.StartsWith(q, StringComparison.Ordinal)
                || c.Aliases.Any(a => a.StartsWith(q, StringComparison.Ordinal)))
            .ToList();
    }
}
