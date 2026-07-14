namespace SranDesktop.Application.Services;

/// <summary>
/// Wire format for the single-instance named pipe (Phase 7 C2). A second
/// launch forwards its args to the running instance; historically the only
/// message was the literal "activate", and that stays valid both ways:
/// an old sender's "activate" still parses, and a new sender with no args
/// still emits "activate". Pure — no pipe/WPF types — so it's unit-testable.
/// </summary>
public static class SingleInstanceProtocol
{
    public const string ActivateMessage = "activate";
    public const string DeepLinkScheme = "sran://";

    /// <summary>Hard cap on a forwarded message — a hostile local sender
    /// can connect to the pipe, so never buffer unbounded input.</summary>
    public const int MaxMessageLength = 8 * 1024;

    /// <summary>What a received pipe message asks the first instance to do.
    /// Activation is unconditional (any second launch should surface the
    /// window); the deep link rides along when the args carried one.</summary>
    public readonly record struct Parsed(bool Activate, string? DeepLink);

    /// <summary>Second-instance side: args → one pipe message (args joined
    /// by newline, or "activate" when there are none worth forwarding).</summary>
    public static string Encode(IReadOnlyList<string>? args)
    {
        if (args is null || args.Count == 0)
            return ActivateMessage;
        var kept = args
            .Where(a => !string.IsNullOrWhiteSpace(a))
            .Select(a => a.Trim())
            .ToList();
        return kept.Count == 0 ? ActivateMessage : string.Join('\n', kept);
    }

    /// <summary>First-instance side: pipe message → actions. Never throws;
    /// hostile input (oversized, garbage) degrades to plain activation.</summary>
    public static Parsed Parse(string? message)
    {
        if (string.IsNullOrWhiteSpace(message) || message.Length > MaxMessageLength)
            return new Parsed(Activate: true, DeepLink: null);

        string? deepLink = null;
        foreach (var raw in message.Split('\n'))
        {
            var line = raw.Trim();
            if (deepLink is null
                && line.StartsWith(DeepLinkScheme, StringComparison.OrdinalIgnoreCase))
                deepLink = line;
        }
        return new Parsed(Activate: true, DeepLink: deepLink);
    }
}
