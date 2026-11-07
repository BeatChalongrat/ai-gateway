using Cronos;

namespace SranDesktop.Application.Services;

/// <summary>Shared cron parsing for the scheduler + task form (R9 Feature G).</summary>
public static class CronHelper
{
    /// <summary>Next local-time occurrence of a 5-field cron expression; null if invalid.</summary>
    public static DateTimeOffset? NextOccurrence(string cronExpression)
    {
        try
        {
            var cron = CronExpression.Parse(cronExpression);
            return cron.GetNextOccurrence(DateTimeOffset.UtcNow, TimeZoneInfo.Local);
        }
        catch (Exception ex) when (ex is CronFormatException or ArgumentException or FormatException)
        {
            // ArgumentException covers null/empty input; CronFormatException bad fields.
            return null;
        }
    }

    /// <summary>FIX8 A1.1 — the occurrences of a cron STRICTLY AFTER <paramref name="from"/>
    /// up to and including <paramref name="to"/> (local time). Used to find missed
    /// runs while the app was closed: the last element is the most-recently missed
    /// occurrence. Empty on a bad cron or an empty window.</summary>
    public static IReadOnlyList<DateTimeOffset> OccurrencesBetween(string cronExpression, DateTimeOffset from, DateTimeOffset to)
    {
        if (from >= to) return Array.Empty<DateTimeOffset>();
        try
        {
            var cron = CronExpression.Parse(cronExpression);
            return cron.GetOccurrences(from, to, TimeZoneInfo.Local, fromInclusive: false, toInclusive: true).ToList();
        }
        catch (Exception ex) when (ex is CronFormatException or ArgumentException or FormatException)
        {
            return Array.Empty<DateTimeOffset>();
        }
    }
}

/// <summary>FIX8 A1.1 — the pure missed-run catch-up decision: given a task's last
/// real run and "now", return the ONE occurrence to catch up (the most recent
/// missed, within the last 7 days) and the OLDER missed occurrences to record as
/// skipped. Everything older than 7 days is discarded (never returned). Pure, so
/// "exactly one catch-up for the most recent" is unit-proven without a scheduler.</summary>
public static class TaskCatchUp
{
    public static (DateTimeOffset? mostRecent, IReadOnlyList<DateTimeOffset> olderSkipped) Compute(
        string cronExpression, DateTimeOffset? lastRunAt, DateTimeOffset now)
    {
        var window = now - TimeSpan.FromDays(7);
        var from = lastRunAt is { } last && last > window ? last : window;
        var missed = CronHelper.OccurrencesBetween(cronExpression, from, now);
        if (missed.Count == 0) return (null, Array.Empty<DateTimeOffset>());
        return (missed[^1], missed.Take(missed.Count - 1).ToList());
    }
}

/// <summary>FIX8 A1.5 — a deterministic per-task stagger: a fixed 0–59 s offset
/// derived from the task id, so tasks scheduled for the same minute do not all fire
/// on the minute, and the SAME task takes the SAME offset on every restart (no
/// stored state, no randomness). FNV-1a over the id, mod 60.</summary>
public static class TaskStagger
{
    public static int SecondsFor(string taskId)
    {
        unchecked
        {
            uint h = 2166136261;
            foreach (var c in taskId ?? "")
            {
                h ^= c;
                h *= 16777619;
            }
            return (int)(h % 60);
        }
    }
}
