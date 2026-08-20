using System.Text.RegularExpressions;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Single home for the plan/artifact fence grammar — shared by ChatViewModel's
/// card extraction (completed messages) and StreamingFenceGuard (mid-stream
/// hiding) so the two can never drift apart.
/// </summary>
public static class FencePatterns
{
    public static readonly Regex PlanFenceRegex = new(
        @":::(excel-plan|doc-plan|pptx-plan)\s*\n([\s\S]*?)\n:::",
        RegexOptions.Compiled);

    /// <summary>
    /// Deliberately tolerant. The prompt shows a three-line example
    /// (ChatViewModel.BuildSystemExtrasAsync) but never states that the
    /// newline after ']' or the closing ':::' on its own line are required —
    /// and models routinely emit the whole directive on ONE line, or forget
    /// the closer entirely. The old pattern demanded both
    /// (<c>\]\s*\n … \n:::</c>), so those replies matched nothing and the
    /// diagram was lost.
    ///
    /// So: any whitespace (or none) may follow the ']', and the block may end
    /// at a closing ':::' OR at the end of the message. Tightening the prompt
    /// instead would only lower the failure rate — the failure mode would
    /// remain. Matching to end-of-message can absorb trailing prose when the
    /// model forgets the closer; showing the diagram plus a little extra text
    /// inside the card beats showing the raw directive and no diagram at all.
    /// </summary>
    public static readonly Regex ArtifactFenceRegex = new(
        @":::artifact\[(?<attrs>[^\]]*)\]\s*(?<code>[\s\S]*?)(?:\n?:::|$)",
        RegexOptions.Compiled);

    /// <summary>Opening markers exactly as the model emits them.</summary>
    public static readonly string[] PlanOpenings =
    {
        ":::excel-plan", ":::doc-plan", ":::pptx-plan",
    };

    public const string ArtifactOpening = ":::artifact[";
}
