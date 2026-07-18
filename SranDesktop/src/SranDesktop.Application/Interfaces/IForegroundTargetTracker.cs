namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Tracks the window the agent is WORKING ON across consent dialogs
/// (computer-use self-interference fix). Every consent dialog brings SRAN to
/// the foreground; without restoring the target afterwards, the next
/// screenshot captures SRAN and the next click lands on SRAN's own window —
/// the agent loops. Recorded before each consent prompt, restored after
/// approval of Screen-category tools and whenever a screen capture finds
/// SRAN itself in the foreground.
/// </summary>
public interface IForegroundTargetTracker
{
    /// <summary>A non-SRAN target window has been recorded.</summary>
    bool HasTarget { get; }

    /// <summary>Snapshot the current foreground as the agent's target. A
    /// foreground belonging to SRAN's own process is ignored — the previous
    /// target is kept (the dialog chain must not overwrite the real target).</summary>
    void RecordBeforeConsent();

    /// <summary>Bring the recorded target back to the foreground. False when
    /// no target exists, the window died, or activation was refused.</summary>
    bool RestoreTarget();

    /// <summary>Restore ONLY when SRAN itself currently holds the foreground
    /// (screen-capture path). False when a real app is foreground already.</summary>
    bool RestoreIfOwnForeground();
}
