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

    public static readonly Regex ArtifactFenceRegex = new(
        @":::artifact\[(?<attrs>[^\]]*)\]\s*\n(?<code>[\s\S]*?)\n:::",
        RegexOptions.Compiled);

    /// <summary>Opening markers exactly as the model emits them.</summary>
    public static readonly string[] PlanOpenings =
    {
        ":::excel-plan", ":::doc-plan", ":::pptx-plan",
    };

    public const string ArtifactOpening = ":::artifact[";
}
