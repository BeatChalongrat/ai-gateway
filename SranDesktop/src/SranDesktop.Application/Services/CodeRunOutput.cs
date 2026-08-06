using System.Text;

namespace SranDesktop.Application.Services;

/// <summary>
/// The parts of a <c>run_python</c> result the UI renders separately (Phase 13
/// Task 2): stdout, stderr, and the two states a person needs to be told about
/// explicitly — timed out and truncated.
/// </summary>
public sealed record CodeRunOutput(
    int? ExitCode,
    string Stdout,
    string Stderr,
    bool TimedOut,
    bool Truncated,
    string? GeneratedNote)
{
    public bool HasStdout => Stdout.Length > 0;
    public bool HasStderr => Stderr.Length > 0;

    /// <summary>True when the sandbox ran but printed nothing — rendered as an
    /// explicit "(no output)" line, because an empty black box reads as the
    /// card being broken rather than the code being quiet.</summary>
    public bool IsSilent => !HasStdout && !HasStderr && !TimedOut;
}

/// <summary>
/// Reads back the text <c>RunPythonTool.Shape</c> writes for the model.
///
/// The desktop's code interpreter is a thin client: the tool flattens the
/// sandbox's structured result into one string for the model, and the message
/// history only keeps that string. Rather than change the tool contract (and
/// with it what every past conversation replays), the card re-derives the
/// pieces from that text. Parsing is deliberately forgiving — anything that
/// doesn't match the known shape is shown verbatim as stdout, so a future
/// change to the tool degrades to "less structured card", never to lost
/// output.
///
/// Line endings are normalised to "\n" so the panes render identically
/// whether the text came from the sandbox or from a Windows-side failure
/// message.
/// </summary>
public static class CodeRunParser
{
    private const string TimeoutMarker = "[The code timed out and was stopped.]";
    private const string TruncatedMarker = "[output truncated]";
    private const string SilentMarker = "(no output was printed)";
    private const string ExitPrefix = "Exit code: ";

    public static CodeRunOutput Parse(string? text)
    {
        var raw = text ?? string.Empty;
        if (raw.Trim().Length == 0)
            return new CodeRunOutput(null, string.Empty, string.Empty, false, false, null);

        var timedOut = raw.Contains(TimeoutMarker, StringComparison.Ordinal);
        var truncated = raw.Contains(TruncatedMarker, StringComparison.Ordinal);

        int? exit = null;
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();
        var generated = new StringBuilder();
        // "unknown" until a section header is seen: a result that doesn't
        // follow the shape at all still lands somewhere visible.
        var section = Section.Unknown;

        foreach (var rawLine in raw.Replace("\r\n", "\n").Split('\n'))
        {
            var line = rawLine.TrimEnd('\r');
            var trimmed = line.Trim();

            if (trimmed == TimeoutMarker || trimmed == TruncatedMarker || trimmed == SilentMarker)
                continue;
            if (trimmed.StartsWith(ExitPrefix, StringComparison.Ordinal))
            {
                if (int.TryParse(trimmed[ExitPrefix.Length..].Trim(), out var parsed))
                    exit = parsed;
                section = Section.None;
                continue;
            }
            if (trimmed == "stdout:")
            {
                section = Section.Stdout;
                continue;
            }
            if (trimmed == "stderr:")
            {
                section = Section.Stderr;
                continue;
            }
            if (trimmed.StartsWith("Generated image(s):", StringComparison.Ordinal)
                || trimmed.StartsWith("Generated file(s):", StringComparison.Ordinal))
            {
                section = Section.Generated;
                Add(generated, trimmed);
                continue;
            }

            switch (section)
            {
                case Section.Stdout:
                    Add(stdout, line);
                    break;
                case Section.Stderr:
                    Add(stderr, line);
                    break;
                case Section.Generated:
                    if (trimmed.Length > 0) Add(generated, trimmed);
                    break;
                case Section.Unknown:
                    // Text that arrived before any known header — a tool-level
                    // failure message, for instance. Show it.
                    Add(stdout, line);
                    break;
            }
        }

        return new CodeRunOutput(
            exit,
            stdout.ToString().Trim('\n'),
            stderr.ToString().Trim('\n'),
            timedOut,
            truncated,
            generated.Length > 0 ? generated.ToString().Trim() : null);
    }

    /// <summary>AppendLine would use the platform separator; the panes want a
    /// single normalised "\n" regardless of where the text came from.</summary>
    private static void Add(StringBuilder sb, string line) => sb.Append(line).Append('\n');

    private enum Section
    {
        Unknown,
        None,
        Stdout,
        Stderr,
        Generated,
    }
}
