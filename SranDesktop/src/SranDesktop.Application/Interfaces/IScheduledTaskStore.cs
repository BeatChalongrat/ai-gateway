using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>Local persistence for scheduled tasks (R9 Feature G).</summary>
public interface IScheduledTaskStore
{
    Task<IReadOnlyList<ScheduledTask>> GetAllAsync(CancellationToken ct = default);
    Task UpsertAsync(ScheduledTask task, CancellationToken ct = default);
    Task DeleteAsync(string id, CancellationToken ct = default);
}
