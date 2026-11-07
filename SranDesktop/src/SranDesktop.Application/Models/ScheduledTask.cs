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

    /// <summary>Truncated preview of the last response (or error message).</summary>
    public string? LastResult { get; set; }

    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    /// <summary>FIX8 A1.3 — how this task's own runs are approved: "manual"
    /// (pause before each tool), "auto" (prompt only for dangerous tools that have
    /// no grant yet), or "skip" (never pause). Set on create/edit; defaults to the
    /// benchmark's safe default of asking. A run in manual mode that hits a tool it
    /// lacks permission for STALLS with the prompt open — run it once by hand so the
    /// prompts appear and can be granted, and later runs stop stalling.</summary>
    public string PermissionMode { get; set; } = "manual";
}

/// <summary>FIX8 A1.2/A1.4 — one line of a task's run history: a real run (ok /
/// error), a catch-up of a missed occurrence, or a SKIP with its reason. The
/// history shows every past attempt including skips, and the reason says why —
/// exactly the three the benchmark records.</summary>
public sealed class TaskRun
{
    public long Id { get; set; }
    public string TaskId { get; set; } = "";
    /// <summary>When this outcome was recorded.</summary>
    public DateTimeOffset RanAt { get; set; } = DateTimeOffset.UtcNow;
    /// <summary>The scheduled occurrence this attempt was for (null for run-now).</summary>
    public DateTimeOffset? ScheduledFor { get; set; }
    /// <summary>"ok" | "error" | "skipped" | "catchup" | "needs_permission" (FIX31 §1.2:
    /// the run needed the user's permission for a tool while nobody was there; the
    /// action was not performed and never answered for the user).</summary>
    public string Outcome { get; set; } = "";
    /// <summary>For a skip: "asleep" (machine was off/asleep at the scheduled time)
    /// | "prev_running" (this task's previous run was still going) | "others_running"
    /// (other tasks were already running). For needs_permission: the tool name(s).
    /// Null otherwise.</summary>
    public string? Reason { get; set; }
    /// <summary>Truncated result or error text.</summary>
    public string? Result { get; set; }
}
