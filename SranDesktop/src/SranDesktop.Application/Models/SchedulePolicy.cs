namespace SranDesktop.Application.Models;

/// <summary>
/// What to do about runs that should have happened while the machine was off,
/// asleep, or offline.
///
/// This is an explicit choice per task because there is no safe default. A daily
/// backup wants to catch up; a "post the morning summary" task that fires eight
/// times at once after a week away is worse than useless; and anything that
/// spends money should ask first. Guessing on the user's behalf is how a laptop
/// opened after vacation sends fourteen stale reports.
/// </summary>
public enum MissedRunPolicy
{
    /// <summary>Forget missed occurrences; wait for the next scheduled one.</summary>
    Skip,

    /// <summary>Run once on return, however many were missed.</summary>
    RunOnceWhenAvailable,

    /// <summary>Run each missed occurrence, up to <see cref="ScheduleSettings.MissedRunLimit"/>.</summary>
    RunAllMissedWithinLimit,

    /// <summary>Do nothing automatically; surface the decision to the user.</summary>
    AskUser,

    /// <summary>Treat missed occurrences as expired and disable the task.</summary>
    Expire
}

public enum ScheduleRunState
{
    Scheduled,
    Running,
    Succeeded,
    Failed,
    Retrying,
    Skipped,
    NeedsUser,
    Expired,
    SuppressedDuplicate
}

/// <summary>Scheduling behaviour attached to a task.</summary>
public sealed record ScheduleSettings
{
    /// <summary>IANA or Windows id. The schedule is evaluated in this zone so a
    /// "09:00 daily" task stays at 09:00 local across a DST change.</summary>
    public string TimeZoneId { get; init; } = TimeZoneInfo.Local.Id;

    public MissedRunPolicy MissedRunPolicy { get; init; } = MissedRunPolicy.RunOnceWhenAvailable;

    /// <summary>Ceiling for <see cref="MissedRunPolicy.RunAllMissedWithinLimit"/>.</summary>
    public int MissedRunLimit { get; init; } = 5;

    public int MaxAttempts { get; init; } = 3;
    public TimeSpan RetryDelay { get; init; } = TimeSpan.FromMinutes(5);

    /// <summary>After this instant the task stops firing.</summary>
    public DateTimeOffset? ExpiresUtc { get; init; }

    /// <summary>Paused tasks keep their schedule but do not fire.</summary>
    public bool IsPaused { get; init; }

    /// <summary>Window within which two triggers for the same occurrence are
    /// treated as one. Timer drift and a wake-from-sleep catch-up can otherwise
    /// both fire the same occurrence.</summary>
    public TimeSpan DuplicateSuppressionWindow { get; init; } = TimeSpan.FromMinutes(1);
}

/// <summary>One execution of a scheduled task.</summary>
public sealed record ScheduleRun(
    string RunId,
    string TaskId,
    DateTimeOffset ScheduledForUtc,
    DateTimeOffset StartedUtc,
    ScheduleRunState State,
    int Attempt = 1,
    string? AgentRunId = null,
    string? Detail = null,
    DateTimeOffset? CompletedUtc = null)
{
    /// <summary>
    /// Identity of the OCCURRENCE, not of this attempt. Two triggers for the
    /// same occurrence produce the same key, which is what makes duplicate
    /// suppression possible at all.
    /// </summary>
    public string OccurrenceKey => $"{TaskId}@{ScheduledForUtc:yyyy-MM-ddTHH:mm:ssZ}";
}

/// <summary>What the scheduler decided to do at a given moment.</summary>
public sealed record ScheduleDecision(
    bool ShouldRun,
    IReadOnlyList<DateTimeOffset> OccurrencesToRun,
    DateTimeOffset? NextRunUtc,
    ScheduleRunState? TerminalState,
    string Explanation);
