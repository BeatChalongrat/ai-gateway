namespace SranDesktop.Application.Services;

/// <summary>
/// Account-wide "custom instructions" (Phase 13 Task 1) — the two free-text
/// fields the web Projects page calls "About you" / "How the AI should
/// respond". They live on the SERVER (GET/PUT /user/preferences), so editing
/// them on the desktop changes what the web app sends too, and vice versa.
///
/// Two consumers, one shape:
///
///   • chat-edge chats already get them injected server-side by the prompt
///     compiler (layer 10, preferences_service.go) — nothing to do here.
///   • the desktop's own agent turns build their system prompt locally, so
///     they would silently IGNORE the user's instructions. <see cref="BuildPromptBlock"/>
///     renders the SAME markdown the server renders, and ChatViewModel
///     appends it, so both paths behave the same.
///
/// The rendering is deliberately a byte-for-byte match of Go's
/// PreferencesService.BuildInstructionsBlock: same headings, same order, same
/// "empty means omit the section" rule. If that ever changes server-side this
/// must follow, or the two clients will shape the persona differently.
/// </summary>
public static class PersonalInstructions
{
    /// <summary>The server's per-field cap (handler/preferences.go). Enforced
    /// client-side too so the counter turns red before a save can 422.</summary>
    public const int MaxChars = 1500;

    /// <summary>Markdown block for the system prompt, or an empty string when
    /// the user has set nothing (callers append unconditionally and rely on
    /// this returning "" rather than testing both fields themselves).</summary>
    public static string BuildPromptBlock(string? about, string? respond)
    {
        var a = (about ?? string.Empty).Trim();
        var r = (respond ?? string.Empty).Trim();
        if (a.Length == 0 && r.Length == 0)
            return string.Empty;

        var parts = new List<string>(2);
        if (a.Length > 0) parts.Add("## About the user\n" + a);
        if (r.Length > 0) parts.Add("## Response style\n" + r);
        return string.Join("\n\n", parts);
    }

    /// <summary>True when either field is over the server's cap — the save is
    /// refused locally rather than sent and rejected.</summary>
    public static bool IsTooLong(string? about, string? respond) =>
        (about?.Length ?? 0) > MaxChars || (respond?.Length ?? 0) > MaxChars;
}

/// <summary>
/// Session cache of <see cref="PersonalInstructions"/>, written by Settings
/// (on load and on save) and read when the desktop composes a local agent
/// turn. Same session-state pattern as <c>WebSearchState</c>.
///
/// Not persisted: the server is the source of truth, and a stale local copy
/// surviving a restart would let the desktop shape replies with instructions
/// the user already deleted on the web.
/// </summary>
public static class PersonalInstructionsState
{
    private static readonly object Gate = new();
    private static string _about = string.Empty;
    private static string _respond = string.Empty;

    public static string About
    {
        get { lock (Gate) return _about; }
    }

    public static string Respond
    {
        get { lock (Gate) return _respond; }
    }

    public static void Set(string? about, string? respond)
    {
        lock (Gate)
        {
            _about = about ?? string.Empty;
            _respond = respond ?? string.Empty;
        }
    }

    /// <summary>Convenience for the prompt builder.</summary>
    public static string PromptBlock()
    {
        lock (Gate)
            return PersonalInstructions.BuildPromptBlock(_about, _respond);
    }

    /// <summary>Test hook — the state is process-wide.</summary>
    public static void Clear() => Set(null, null);
}
