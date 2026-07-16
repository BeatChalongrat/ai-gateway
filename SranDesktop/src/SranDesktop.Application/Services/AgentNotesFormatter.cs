using System.Text;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

/// <summary>
/// Renders the durable agent notes into the never-trimmed system prologue's
/// <c>## Notes</c> block (Phase B PART 3). Size-bounded exactly like the Phase-14
/// file manifest so a long-lived conversation's notes can never balloon the
/// system prompt. Keeps the NEWEST notes when capped (most relevant), presented
/// oldest→newest for a readable narrative.
/// </summary>
public static class AgentNotesFormatter
{
    public const int MaxBlockChars = 2000;

    public static string? BuildBlock(IReadOnlyList<AgentNote> notes, int maxChars = MaxBlockChars)
    {
        if (notes.Count == 0)
            return null;

        var kept = new List<string>();
        var used = 0;
        var omitted = 0;
        // Newest first so the most recent facts always make the cut.
        for (var i = notes.Count - 1; i >= 0; i--)
        {
            var line = "- " + notes[i].Content.Replace('\n', ' ').Replace('\r', ' ').Trim() + "\n";
            if (used + line.Length > maxChars && kept.Count > 0)
            {
                omitted = i + 1;
                break;
            }
            kept.Add(line);
            used += line.Length;
        }
        kept.Reverse();

        var sb = new StringBuilder();
        sb.Append("\n\n## Notes (what I've learned about this task)\n");
        sb.Append("Durable notes I saved earlier in THIS conversation (they survive restarts). ");
        sb.Append("Use them to avoid re-discovering facts — they are hints, not guarantees, so ");
        sb.Append("verify anything that looks stale.\n");
        if (omitted > 0)
            sb.Append("(+").Append(omitted).Append(" older note(s) omitted)\n");
        foreach (var line in kept)
            sb.Append(line);
        return sb.ToString();
    }
}
