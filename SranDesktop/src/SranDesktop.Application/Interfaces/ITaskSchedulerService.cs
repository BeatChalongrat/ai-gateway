using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Background scheduler: ticks once a minute, fires due tasks by sending their
/// prompt through chat-edge, and records the result (R9 Feature G).
/// </summary>
public interface ITaskSchedulerService
{
    /// <summary>Begin ticking (call after login; idempotent).</summary>
    void Start();

    void Stop();

    /// <summary>Fire a task immediately regardless of schedule.</summary>
    Task RunNowAsync(ScheduledTask task, CancellationToken ct = default);

    /// <summary>Next local-time occurrence of a cron expression; null if invalid.</summary>
    DateTimeOffset? NextOccurrence(string cronExpression);

    /// <summary>Raised after any task run completes (UI refresh hook).</summary>
    event EventHandler? TaskRunCompleted;
}
