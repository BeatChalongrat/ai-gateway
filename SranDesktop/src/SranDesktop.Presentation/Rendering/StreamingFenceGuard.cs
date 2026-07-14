namespace SranDesktop.Presentation.Rendering;

public enum StreamingFenceKind { None, Plan, Artifact }

/// <summary>
/// Hides plan/artifact fence content while a message streams. The markdown is
/// truncated just before the FIRST fence opening — completed or not: raw fence
/// bodies (JSON plans, artifact code) are never useful to display, and the
/// real cards appear at completion via ChatViewModel.ExtractPlanBlocks.
/// Consequently, text AFTER a completed fence is also hidden during streaming;
/// the final render restores everything. A trailing PARTIAL opening marker
/// (":::", ":::doc-p", …) is hidden too so it never flashes as literal text.
/// </summary>
public static class StreamingFenceGuard
{
    public static (string Visible, StreamingFenceKind Kind) TruncateAtOpenFence(string markdown)
    {
        if (string.IsNullOrEmpty(markdown)
            || !markdown.Contains(":::", StringComparison.Ordinal))
            return (markdown, StreamingFenceKind.None);

        var bestIndex = -1;
        var kind = StreamingFenceKind.None;
        foreach (var opening in FencePatterns.PlanOpenings)
        {
            var idx = markdown.IndexOf(opening, StringComparison.Ordinal);
            if (idx >= 0 && (bestIndex < 0 || idx < bestIndex))
            {
                bestIndex = idx;
                kind = StreamingFenceKind.Plan;
            }
        }

        var artifactIdx = markdown.IndexOf(FencePatterns.ArtifactOpening, StringComparison.Ordinal);
        if (artifactIdx >= 0 && (bestIndex < 0 || artifactIdx < bestIndex))
        {
            bestIndex = artifactIdx;
            kind = StreamingFenceKind.Artifact;
        }

        if (bestIndex >= 0)
            return (markdown[..bestIndex], kind);

        // No classifiable opening yet — but hide a trailing prefix of one
        // (the marker itself can be split across stream deltas).
        var tail = markdown.LastIndexOf(":::", StringComparison.Ordinal);
        if (tail >= 0)
        {
            var suffix = markdown[tail..];
            var isPartialOpening = FencePatterns.PlanOpenings
                .Append(FencePatterns.ArtifactOpening)
                .Any(o => o.Length > suffix.Length && o.StartsWith(suffix, StringComparison.Ordinal));
            if (isPartialOpening)
                return (markdown[..tail], StreamingFenceKind.None);
        }

        return (markdown, StreamingFenceKind.None);
    }
}
