using System.Text;
using System.Text.RegularExpressions;

namespace SranDesktop.Application.Services;

/// <summary>A destructive-command detection hit: which rule fired and the
/// token/keyword that triggered it (safe to show in an error message — never
/// the full command).</summary>
public sealed record DestructiveCommand(string Rule, string Keyword);

/// <summary>
/// Command-shape detector for system-destroying shell commands. Replaces the
/// old substring blocklist in run_command, which had both false positives
/// (a bare "format " entry caught the ubiquitous output-formatting flag —
/// `docker images --format "{{...}}"`) and false negatives (PowerShell's
/// Format-Volume / Clear-Disk were never matched at all).
///
/// How it decides (tokenized, not substring):
///  1. The command line is split into SEGMENTS on unquoted pipeline
///     separators (| ; &amp; &amp;&amp; || newline) so `echo y | format d:` is judged
///     by its second command, and quoted text (`git commit -m "del /s ..."`)
///     never splits or matches.
///  2. Each segment's COMMAND token (first token, basename, extension
///     stripped — `C:\Windows\System32\format.com` is still `format`) is
///     matched against per-command rules that also require the destructive
///     ARGUMENT SHAPE (`format c:`, `del /s` on a root-ish path). A word
///     appearing as a flag (`--format`), a `/x:` switch value, a path
///     segment, or inside quotes is data, not a command, and never matches.
///  3. Shell wrappers are unwrapped (`cmd /c ...`, `powershell -Command
///     ...`); PowerShell `-EncodedCommand` payloads are base64-decoded and
///     re-analyzed — an undecodable payload is treated as destructive
///     (fail-safe: what cannot be inspected cannot be cleared).
///  4. Hyphenated destructive PowerShell cmdlet names (Format-Volume,
///     Clear-Disk, Remove-Partition, Initialize-Disk, Restart-Computer) are
///     matched as any UNQUOTED token, because inside a wrapper the command
///     position is ambiguous and those Verb-Noun names never occur as flags
///     or paths.
///
/// Used from two places (owner decision, 2026-08-04): the run_command
/// blocklist gate (enforced in Manual + Auto approval modes, BYPASSED with
/// an audit log in Skip-all mode) and as the "unsafe" signal that makes
/// Auto mode pause. Fail-safe: ambiguous parses lean destructive.
/// </summary>
public static class DestructiveCommandDetector
{
    private const int MaxUnwrapDepth = 4;

    /// <summary>Destructive PowerShell cmdlets matched at ANY unquoted token
    /// position (Verb-Noun names are unambiguous — see class doc, point 4).</summary>
    private static readonly Dictionary<string, string> CmdletRules = new(StringComparer.OrdinalIgnoreCase)
    {
        ["format-volume"] = "disk_wipe",
        ["clear-disk"] = "disk_wipe",
        ["remove-partition"] = "disk_wipe",
        ["initialize-disk"] = "disk_wipe",
        ["restart-computer"] = "power",
    };

    private static readonly Regex DriveArg = new(@"^[a-z]:\\?$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

    /// <summary>Returns the first destructive hit, or null when the command
    /// is clear of every rule.</summary>
    public static DestructiveCommand? Analyze(string command)
        => string.IsNullOrWhiteSpace(command) ? null : AnalyzeCore(command, 0);

    private static DestructiveCommand? AnalyzeCore(string commandLine, int depth)
    {
        if (depth > MaxUnwrapDepth)
            return new DestructiveCommand("unparseable_nesting", "wrapper");

        // Bash fork bomb — a quoting-hostile special form the tokenizer
        // would mangle; a raw scan is the honest check.
        if (commandLine.Contains(":(){", StringComparison.Ordinal))
            return new DestructiveCommand("fork_bomb", ":(){");

        foreach (var segment in SplitSegments(commandLine))
        {
            var hit = AnalyzeSegment(segment, depth);
            if (hit is not null)
                return hit;
        }
        return null;
    }

    private static DestructiveCommand? AnalyzeSegment(List<Token> tokens, int depth)
    {
        if (tokens.Count == 0)
            return null;

        // Destructive cmdlet names anywhere (unquoted) — wrapper-position
        // fuzz means the command token alone is not enough for these.
        foreach (var t in tokens)
        {
            if (!t.Quoted && CmdletRules.TryGetValue(t.Text, out var rule))
                return new DestructiveCommand(rule, t.Text.ToLowerInvariant());
        }

        var cmd = CommandName(tokens[0]);
        var args = tokens.Skip(1).ToList();

        switch (cmd)
        {
            // ── shell wrappers ───────────────────────────────────────
            case "cmd":
                return AnalyzeWrappedAfter(args, depth,
                    t => t is "/c" or "/k");
            case "powershell":
            case "pwsh":
                return AnalyzePowerShell(args, depth);

            // ── disk / volume ────────────────────────────────────────
            case "format":
                // The real format.com always takes a drive and/or /switches;
                // "--format", "/format:csv" and `format json` never get here
                // (flag/path shapes are not command tokens; args must match).
                // Quoting does not neutralize a drive target — `format "c:"`
                // still formats — so the arg check ignores Token.Quoted.
                if (args.Any(a => DriveArg.IsMatch(a.Text) || a.Text.StartsWith('/')))
                    return new DestructiveCommand("disk_format", "format");
                return null;
            case "diskpart":
                // Interactive or scripted (/s file we cannot read) — either
                // way we cannot see the clean/delete inside. Fail-safe.
                return new DestructiveCommand("disk_tool", "diskpart");
            case "dd":
                if (args.Any(a => a.Text.StartsWith("of=/dev/", StringComparison.OrdinalIgnoreCase)
                                  || a.Text.StartsWith(@"of=\\.\", StringComparison.OrdinalIgnoreCase)))
                    return new DestructiveCommand("disk_write", "dd");
                return null;
            case var m when m.StartsWith("mkfs", StringComparison.Ordinal):
                return new DestructiveCommand("disk_format", cmd);
            case "cipher":
                if (args.Any(a => a.Text.StartsWith("/w", StringComparison.OrdinalIgnoreCase)))
                    return new DestructiveCommand("disk_wipe", "cipher /w");
                return null;

            // ── mass delete ──────────────────────────────────────────
            case "del":
            case "erase":
            case "rd":
            case "rmdir":
                if (args.Any(a => a.Text.Equals("/s", StringComparison.OrdinalIgnoreCase))
                    && args.Where(a => !a.Text.StartsWith('/')).Any(a => IsRootish(a.Text)))
                    return new DestructiveCommand("mass_delete", cmd);
                return null;
            case "rm":
                if (HasRmRecursiveForce(args)
                    && args.Where(a => !a.Text.StartsWith('-')).Any(a => IsRootish(a.Text)))
                    return new DestructiveCommand("mass_delete", "rm -rf");
                return null;
            case "remove-item":
            case "ri":
                if (args.Any(a => a.Text.StartsWith("-recur", StringComparison.OrdinalIgnoreCase))
                    && args.Any(a => a.Text.StartsWith("-fo", StringComparison.OrdinalIgnoreCase))
                    && args.Where(a => !a.Text.StartsWith('-')).Any(a => IsRootish(a.Text)))
                    return new DestructiveCommand("mass_delete", "remove-item");
                return null;

            // ── recovery / boot / registry ───────────────────────────
            case "vssadmin":
                if (Has(args, "delete") && Has(args, "shadows"))
                    return new DestructiveCommand("recovery_delete", "vssadmin delete shadows");
                return null;
            case "wbadmin":
                if (Has(args, "delete"))
                    return new DestructiveCommand("recovery_delete", "wbadmin delete");
                return null;
            case "bcdedit":
                if (Has(args, "/delete"))
                    return new DestructiveCommand("boot_config", "bcdedit /delete");
                return null;
            case "reg":
                if (args.Count >= 2
                    && args[0].Text.Equals("delete", StringComparison.OrdinalIgnoreCase)
                    && (args[1].Text.StartsWith("hklm", StringComparison.OrdinalIgnoreCase)
                        || args[1].Text.StartsWith("hkey_local_machine", StringComparison.OrdinalIgnoreCase)))
                    return new DestructiveCommand("registry_delete", "reg delete hklm");
                return null;

            // ── power ────────────────────────────────────────────────
            case "shutdown":
                return new DestructiveCommand("power", "shutdown");

            default:
                return null;
        }
    }

    // ── wrappers ────────────────────────────────────────────────────

    private static DestructiveCommand? AnalyzeWrappedAfter(
        List<Token> args, int depth, Func<string, bool> isPayloadMarker)
    {
        for (var i = 0; i < args.Count; i++)
        {
            if (isPayloadMarker(args[i].Text.ToLowerInvariant()))
            {
                var payload = string.Join(' ', args.Skip(i + 1).Select(t => t.Text));
                return AnalyzeCore(payload, depth + 1);
            }
        }
        // No /c-/k marker: nothing executes, nothing to judge.
        return null;
    }

    private static DestructiveCommand? AnalyzePowerShell(List<Token> args, int depth)
    {
        for (var i = 0; i < args.Count; i++)
        {
            var t = args[i].Text.ToLowerInvariant();

            // -EncodedCommand and its unambiguous abbreviations (-e is left
            // alone: it collides with -ExecutionPolicy et al).
            var isEncoded = t == "-ec"
                || (t.StartsWith("-en", StringComparison.Ordinal)
                    && "-encodedcommand".StartsWith(t, StringComparison.Ordinal));
            if (isEncoded)
            {
                if (i + 1 >= args.Count)
                    return new DestructiveCommand("encoded_command", "-encodedcommand");
                var decoded = TryDecodeBase64Utf16(args[i + 1].Text);
                return decoded is null
                    ? new DestructiveCommand("encoded_command", "-encodedcommand")
                    : AnalyzeCore(decoded, depth + 1);
            }
        }

        // -Command / -c payload, or the first positional argument (which
        // PowerShell treats as an implicit -Command). Flag VALUES (e.g.
        // "hidden" after -WindowStyle) may leak into the rejoined payload —
        // harmless, they match no rule; the unquoted-cmdlet scan above
        // already covered position-independent hits.
        for (var i = 0; i < args.Count; i++)
        {
            var t = args[i].Text.ToLowerInvariant();
            if (t is "-command" or "-c")
                return AnalyzeCore(string.Join(' ', args.Skip(i + 1).Select(x => x.Text)), depth + 1);
        }
        var firstPositional = args.FindIndex(a => !a.Text.StartsWith('-'));
        if (firstPositional >= 0)
            return AnalyzeCore(
                string.Join(' ', args.Skip(firstPositional).Select(x => x.Text)), depth + 1);
        return null;
    }

    private static string? TryDecodeBase64Utf16(string payload)
    {
        try
        {
            var bytes = Convert.FromBase64String(payload.Trim().Trim('"', '\''));
            return Encoding.Unicode.GetString(bytes);
        }
        catch
        {
            return null;
        }
    }

    // ── helpers ─────────────────────────────────────────────────────

    private static bool Has(List<Token> args, string word)
        => args.Any(a => a.Text.Equals(word, StringComparison.OrdinalIgnoreCase));

    private static bool HasRmRecursiveForce(List<Token> args)
    {
        var recursive = false;
        var force = false;
        foreach (var a in args)
        {
            var t = a.Text;
            if (!t.StartsWith('-')) continue;
            if (t == "--recursive") recursive = true;
            else if (t == "--force") force = true;
            else if (!t.StartsWith("--", StringComparison.Ordinal))
            {
                // Bundled short flags: -rf, -fr, -r, -f …
                if (t.Contains('r', StringComparison.OrdinalIgnoreCase)) recursive = true;
                if (t.Contains('f', StringComparison.OrdinalIgnoreCase)) force = true;
            }
        }
        return recursive && force;
    }

    /// <summary>True for paths whose recursive deletion is catastrophic:
    /// a drive root, a TOP-LEVEL directory (C:\Users), filesystem root,
    /// home, or a bare wildcard. Deeper project paths (C:\repo\build,
    /// /tmp/build/out) are the consent dialog's business, not the net's.</summary>
    private static bool IsRootish(string raw)
    {
        var p = raw.Trim().Trim('"', '\'').TrimEnd('\\', '/');
        if (p.Length == 0) return true;                     // "\" or "/" alone
        if (p is "*" or "~") return true;
        if (Regex.IsMatch(p, @"^[a-z]:$", RegexOptions.IgnoreCase)) return true;          // C:
        if (Regex.IsMatch(p, @"^[a-z]:[\\/][^\\/]+$", RegexOptions.IgnoreCase)) return true; // C:\Users
        if (Regex.IsMatch(p, @"^[\\/][^\\/]*$")) return true;                              // /, /usr
        return false;
    }

    /// <summary>Normalizes a token to its command name: quotes stripped,
    /// directory prefix dropped, executable extension dropped, lowercased.
    /// A QUOTED first token is still a command (`"C:\tools\format.com" c:`).</summary>
    private static string CommandName(Token token)
    {
        var t = token.Text.Trim('"', '\'');
        var slash = t.LastIndexOfAny(new[] { '\\', '/' });
        if (slash >= 0 && slash < t.Length - 1)
            t = t[(slash + 1)..];
        foreach (var ext in new[] { ".exe", ".com", ".bat", ".cmd" })
        {
            if (t.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                t = t[..^ext.Length];
                break;
            }
        }
        return t.ToLowerInvariant();
    }

    private readonly record struct Token(string Text, bool Quoted);

    /// <summary>Splits a command line into pipeline segments on UNQUOTED
    /// `| ; &amp; \n`, tokenizing each segment on whitespace with double/single
    /// quotes respected (quotes kept out of Token.Text, remembered in
    /// Token.Quoted). `&amp;&amp;` / `||` fall out naturally as two separators.</summary>
    private static List<List<Token>> SplitSegments(string commandLine)
    {
        var segments = new List<List<Token>>();
        var current = new List<Token>();
        var sb = new StringBuilder();
        var quoted = false;
        char? quote = null;

        void EndToken()
        {
            if (sb.Length > 0 || quoted)
                current.Add(new Token(sb.ToString(), quoted));
            sb.Clear();
            quoted = false;
        }

        void EndSegment()
        {
            EndToken();
            if (current.Count > 0)
                segments.Add(current);
            current = new List<Token>();
        }

        foreach (var c in commandLine)
        {
            if (quote is not null)
            {
                if (c == quote) quote = null;
                else sb.Append(c);
                continue;
            }
            switch (c)
            {
                case '"' or '\'':
                    quote = c;
                    quoted = true;
                    break;
                case '|' or ';' or '&' or '\n' or '\r':
                    EndSegment();
                    break;
                case ' ' or '\t':
                    EndToken();
                    break;
                default:
                    sb.Append(c);
                    break;
            }
        }
        EndSegment();
        return segments;
    }
}
