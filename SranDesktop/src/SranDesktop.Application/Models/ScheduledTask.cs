namespace SranDesktop.Application.Models;

/// <summary>A prompt that runs automatically on a cron schedule (R9 Feature G).</summary>
public sealed class ScheduledTask
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Name { get; set; } = "";

    /// <summary>The message sent to the AI when the task fires.</summary>
    public string Prompt { get; set; } = "";

    /// <summary>Run inside this conversation; null = create a fresh one per run.</summary>
    public string? ConversationId { get; set; }

    /// <summary>Standard 5-field cron, e.g. "0 6 * * *" = daily 06:00.</summary>
    public string CronExpression { get; set; } = "0 6 * * *";

    public bool IsEnabled { get; set; } = true;
    public DateTimeOffset? NextRunAt { get; set; }
    public DateTimeOffset? LastRunAt { get; set; }
    public string? LastRunId { get; set; }

    /// <summary>Truncated preview of the last response (or error message).</summary>
    public string? LastResult { get; set; }

    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}
