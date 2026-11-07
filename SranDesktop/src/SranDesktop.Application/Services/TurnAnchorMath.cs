namespace SranDesktop.Application.Services;

/// <summary>
/// P3 — send-time turn anchoring (the ChatGPT/Claude pattern), ported from
/// the web's lib/turnAnchor. When the user sends, the new turn scrolls to
/// sit near the top of the viewport with a small peek of the previous
/// message above it, so the reply streams downward into open space instead
/// of crawling up from the composer. Pure math — headless-testable; the
/// view (ChatView) owns the pixels. Keep in sync with lib/turnAnchor.ts.
/// </summary>
public static class TurnAnchorMath
{
    /// <summary>
    /// Scroll offset that puts the sent message's top <paramref name="peek"/>
    /// pixels below the viewport top. The peek is a MAXIMUM: when the
    /// previous message is shorter, the target clamps to that message's own
    /// top (show whatever there is, never overscroll past it); with no
    /// previous message the turn lands flush at the top.
    /// </summary>
    public static double AnchorTarget(double userTop, double? prevTop, double peek)
        => Math.Max(0, Math.Max(prevTop ?? userTop, userTop - peek));

    /// <summary>
    /// Height of the bottom spacer that makes <paramref name="target"/>
    /// reachable: the part of the viewport below the existing content when
    /// scrolled there. Recomputed as the reply streams in, so it shrinks to
    /// zero on its own; never negative, never cumulative.
    /// </summary>
    public static double SpacerNeeded(double target, double viewportHeight, double contentHeight)
        => Math.Max(0, target + viewportHeight - contentHeight);

    /// <summary>
    /// Phase handover: once the streamed reply has filled the viewport below
    /// the anchor, the hold ends and the pre-existing stick-to-bottom
    /// behaviour takes over. THE line to change if QA wants a different
    /// handover policy.
    /// </summary>
    public static bool ShouldResumeFollow(double contentHeight, double offset, double viewportHeight)
        => contentHeight >= offset + viewportHeight - 0.5;

        /// <summary>
    /// Whether a ScrollChanged was (very probably) caused by our own
    /// programmatic offset write rather than the user: the write stamped
    /// <paramref name="issuedTick"/>, and anything inside the grace window is
    /// treated as ours. User scrolls outside the window always win.
    /// </summary>
    public static bool IsRecentTick(long nowTick, long issuedTick, int graceMs)
        => issuedTick != 0 && nowTick - issuedTick < graceMs;
}
