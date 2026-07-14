using Markdig.Syntax;

namespace SranDesktop.Presentation.Rendering;

/// <summary>Source identity of one top-level markdown block: its source span
/// plus a hash of the exact source slice.</summary>
public readonly record struct BlockFingerprint(int Start, int Length, int TextHash);

/// <summary>
/// Pure diff over top-level Markdig blocks for progressive streaming renders.
/// Parsing a whole message is cheap; building WPF element trees is not — so
/// blocks whose SOURCE TEXT is unchanged keep their existing elements and only
/// blocks from the first changed index on are re-rendered (in practice: the
/// growing last block plus anything newly appeared). No WPF types here.
/// </summary>
public static class BlockDiff
{
    public static List<BlockFingerprint> Fingerprint(string markdown, MarkdownDocument document)
    {
        var result = new List<BlockFingerprint>(document.Count);
        foreach (var block in document)
        {
            var start = Math.Max(0, block.Span.Start);
            var end = Math.Min(markdown.Length - 1, block.Span.End);
            var length = Math.Max(0, end - start + 1);
            var hash = length > 0 ? string.GetHashCode(markdown.AsSpan(start, length)) : 0;
            result.Add(new BlockFingerprint(start, length, hash));
        }
        return result;
    }

    /// <summary>
    /// Index of the first block to (re)render; blocks strictly before it are
    /// reusable. The LAST current block ALWAYS re-renders: Markdig gives an
    /// unclosed fenced code block (the streaming hot case) a span covering
    /// only its opening marker, so span+hash can look unchanged while its
    /// content grows. That's safe because a stream is append-only — every
    /// block before the last has immutable source, so prefix comparison is
    /// exact there.
    /// </summary>
    public static int FirstChangedIndex(
        IReadOnlyList<BlockFingerprint> previous,
        IReadOnlyList<BlockFingerprint> current)
    {
        if (current.Count == 0)
            return 0;

        var limit = Math.Min(Math.Min(previous.Count, current.Count), current.Count - 1);
        for (var i = 0; i < limit; i++)
        {
            if (previous[i] != current[i])
                return i;
        }
        return limit;
    }
}
