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
}
