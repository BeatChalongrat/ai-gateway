using System.Text;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

/// <summary>
/// Renders the durable agent notes into the never-trimmed system prologue
/// (Phase B PART 3): a <c>## Memory (global)</c> section (cross-conversation
/// user facts, newest first) followed by <c>## Notes (this conversation)</c>.
/// Size-bounded exactly like the Phase-14 file manifest so long-lived notes can
/// never balloon the system prompt — and the global section has its OWN cap so
/// a busy conversation's notes can't evict it. Keeps the NEWEST notes when
/// capped (most relevant); conversation notes are presented oldest→newest for
/// a readable narrative.
/// </summary>
public static class AgentNotesFormatter
{
    public const int MaxBlockChars = 2000;
    public const int MaxGlobalBlockChars = 2000;

    public static string? BuildBlock(IReadOnlyList<AgentNote> notes, int maxChars = MaxBlockChars) =>
        BuildBlock(notes, Array.Empty<AgentNote>(), maxChars);

    public static string? BuildBlock(
        IReadOnlyList<AgentNote> conversationNotes,
        IReadOnlyList<AgentNote> globalNotes,
        int maxChars = MaxBlockChars,
        int maxGlobalChars = MaxGlobalBlockChars)
    {
        if (conversationNotes.Count == 0 && globalNotes.Count == 0)
            return null;

        var sb = new StringBuilder();

        if (globalNotes.Count > 0)
        {
            sb.Append("\n\n## Memory (global)\n");
            sb.Append("Durable facts about the user, saved across ALL conversations (newest first). ");
            sb.Append("Apply them without re-asking; they are hints, not guarantees.\n");
            var used = 0;
            for (var i = globalNotes.Count - 1; i >= 0; i--) // newest first
            {
                var line = "- " + OneLine(globalNotes[i].Content) + "\n";
                if (used + line.Length > maxGlobalChars && used > 0)
                {
                    sb.Append("(+").Append(i + 1).Append(" older note(s) omitted)\n");
                    break;
                }
                sb.Append(line);
                used += line.Length;
            }
        }

        if (conversationNotes.Count > 0)
        {
            var kept = new List<string>();
            var used = 0;
            var omitted = 0;
            // Newest first so the most recent facts always make the cut.
            for (var i = conversationNotes.Count - 1; i >= 0; i--)
            {
                var line = "- " + OneLine(conversationNotes[i].Content) + "\n";
                if (used + line.Length > maxChars && kept.Count > 0)
                {
                    omitted = i + 1;
                    break;
                }
                kept.Add(line);
                used += line.Length;
            }
            kept.Reverse();

            sb.Append("\n\n## Notes (this conversation)\n");
            sb.Append("Durable notes I saved earlier in THIS conversation (they survive restarts). ");
            sb.Append("Use them to avoid re-discovering facts — they are hints, not guarantees, so ");
            sb.Append("verify anything that looks stale.\n");
            if (omitted > 0)
                sb.Append("(+").Append(omitted).Append(" older note(s) omitted)\n");
            foreach (var line in kept)
                sb.Append(line);
        }

        return sb.ToString();
    }

    private static string OneLine(string content) =>
        content.Replace('\n', ' ').Replace('\r', ' ').Trim();
}
