using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>Local persistence for scheduled tasks (R9 Feature G).</summary>
public interface IScheduledTaskStore
{
    Task<IReadOnlyList<ScheduledTask>> GetAllAsync(CancellationToken ct = default);
    Task UpsertAsync(ScheduledTask task, CancellationToken ct = default);
    Task DeleteAsync(string id, CancellationToken ct = default);

    /// <summary>FIX8 A1.2 — append one line of run history (a run, a catch-up, or a
    /// skip with its reason).</summary>
    Task AppendRunAsync(TaskRun run, CancellationToken ct = default);

    /// <summary>FIX8 A1.4 — the most recent <paramref name="limit"/> history lines
    /// for a task, newest first.</summary>
    Task<IReadOnlyList<TaskRun>> GetRunsAsync(string taskId, int limit = 50, CancellationToken ct = default);
}
