namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// Decides what a scheduled task should do right now.
///
/// Written as a pure function of (task, settings, last run, now) so the awkward
/// cases can actually be tested: a laptop opened after a week, a clock that
/// jumped an hour, two timers firing for the same minute. Those are the
/// situations where schedulers duplicate side effects, and a decision function
/// that reads the wall clock internally cannot be tested against them at all.
/// </summary>
public static class ScheduleEvaluator
{
    /// <summary>
    /// Works out which occurrences are owed and whether to run them.
    /// </summary>
    /// <param name="lastCompletedOccurrenceUtc">
    /// The occurrence instant of the last run that finished — not when it ran.
    /// Catch-up is computed from the schedule, not from execution history.
    /// </param>
    public static ScheduleDecision Evaluate(
        ScheduledTask task,
        ScheduleSettings settings,
        DateTimeOffset? lastCompletedOccurrenceUtc,
        DateTimeOffset nowUtc)
    {
        ArgumentNullException.ThrowIfNull(task);
        ArgumentNullException.ThrowIfNull(settings);

        if (!task.IsEnabled)
            return new ScheduleDecision(false, [], null, null, "The task is disabled.");

        if (settings.IsPaused)
            return new ScheduleDecision(
                false, [], NextOccurrence(task, settings, nowUtc), null,
                "The task is paused; its schedule is preserved.");

        if (settings.ExpiresUtc is { } expiry && nowUtc >= expiry)
            return new ScheduleDecision(
                false, [], null, ScheduleRunState.Expired,
                $"The task expired at {expiry:u}.");

        // Enumerate every occurrence that is genuinely owed, not just as many as
        // the policy might run. Capping here made "run the most recent missed
        // one" pick the wrong occurrence, because the list stopped short of the
        // latest one — the policy needs the true tail to choose from. The cap is
        // for runaway expressions only.
        var due = OccurrencesBetween(
            task, settings,
            lastCompletedOccurrenceUtc ?? task.CreatedAt,
            nowUtc,
            maximum: 2000).ToList();

        if (due.Count == 0)
            return new ScheduleDecision(
                false, [], NextOccurrence(task, settings, nowUtc), null,
                "Nothing is due yet.");

        // One occurrence due is the ordinary case — no policy needed.
        if (due.Count == 1)
            return new ScheduleDecision(
                true, due, NextOccurrence(task, settings, nowUtc), null,
                "One occurrence is due.");

        // More than one means time passed without the scheduler running, and
        // what to do about that is the user's decision, not a default.
        var missed = due.Count;
        return settings.MissedRunPolicy switch
        {
            MissedRunPolicy.Skip => new ScheduleDecision(
                false, [], NextOccurrence(task, settings, nowUtc), ScheduleRunState.Skipped,
                $"{missed} occurrences were missed and the policy is to skip them."),

            MissedRunPolicy.RunOnceWhenAvailable => new ScheduleDecision(
                true, [due[^1]], NextOccurrence(task, settings, nowUtc), null,
                $"{missed} occurrences were missed; running the most recent one only."),

            MissedRunPolicy.RunAllMissedWithinLimit => RunAllWithinLimit(
                task, settings, due, nowUtc),

            MissedRunPolicy.AskUser => new ScheduleDecision(
                false, due, NextOccurrence(task, settings, nowUtc), ScheduleRunState.NeedsUser,
                $"{missed} occurrences were missed; the user chose to be asked."),

            MissedRunPolicy.Expire => new ScheduleDecision(
                false, [], null, ScheduleRunState.Expired,
                $"{missed} occurrences were missed and the policy is to expire the task."),

            _ => new ScheduleDecision(
                false, [], NextOccurrence(task, settings, nowUtc), ScheduleRunState.NeedsUser,
                "Unrecognised missed-run policy; refusing to guess.")
        };
    }

    private static ScheduleDecision RunAllWithinLimit(
        ScheduledTask task, ScheduleSettings settings,
        List<DateTimeOffset> due, DateTimeOffset nowUtc)
    {
        if (due.Count > settings.MissedRunLimit)
        {
            // Past the limit the honest answer is that we cannot silently decide
            // to drop the overflow, so the newest ones run and the user is told.
            var kept = due.TakeLast(settings.MissedRunLimit).ToList();
            return new ScheduleDecision(
                true, kept, NextOccurrence(task, settings, nowUtc), ScheduleRunState.NeedsUser,
                $"{due.Count} occurrences were missed, above the limit of "
                + $"{settings.MissedRunLimit}; running the most recent {kept.Count} "
                + "and reporting the rest.");
        }

        return new ScheduleDecision(
            true, due, NextOccurrence(task, settings, nowUtc), null,
            $"Running all {due.Count} missed occurrences, within the limit.");
    }

    /// <summary>
    /// Enumerates occurrences in (after, upTo]. Evaluated in the task's own time
    /// zone so a "daily at 09:00" task stays at 09:00 local when the clocks move.
    /// </summary>
    public static IEnumerable<DateTimeOffset> OccurrencesBetween(
        ScheduledTask task,
        ScheduleSettings settings,
        DateTimeOffset after,
        DateTimeOffset upTo,
        int maximum = 100)
    {
        var zone = ResolveZone(settings.TimeZoneId);
        var cursor = after;
        var produced = 0;

        while (produced < maximum)
        {
            var next = NextAfter(task.CronExpression, cursor, zone);
            if (next is null || next > upTo)
                yield break;

            yield return next.Value;
            cursor = next.Value;
            produced++;
        }
    }

    public static DateTimeOffset? NextOccurrence(
        ScheduledTask task, ScheduleSettings settings, DateTimeOffset afterUtc) =>
        NextAfter(task.CronExpression, afterUtc, ResolveZone(settings.TimeZoneId));

    /// <summary>
    /// Next firing strictly after <paramref name="afterUtc"/>.
    ///
    /// The search walks local minutes and converts back, which is what makes DST
    /// behave. On the spring-forward gap the local time simply never occurs and
    /// is skipped; on the autumn repeat the first of the two instants is taken,
    /// so a daily task fires once rather than twice.
    /// </summary>
    private static DateTimeOffset? NextAfter(
        string cronExpression, DateTimeOffset afterUtc, TimeZoneInfo zone)
    {
        if (string.IsNullOrWhiteSpace(cronExpression))
            return null;

        var fields = cronExpression.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (fields.Length != 5)
            return null;

        var localAfter = TimeZoneInfo.ConvertTime(afterUtc, zone);
        var candidate = new DateTime(
            localAfter.Year, localAfter.Month, localAfter.Day,
            localAfter.Hour, localAfter.Minute, 0, DateTimeKind.Unspecified)
            .AddMinutes(1);

        // Two years of minutes is far more than any 5-field cron needs to find
        // its next match, and bounds the loop for expressions that never match.
        for (var step = 0; step < 366 * 24 * 60 * 2; step++)
        {
            if (Matches(fields, candidate))
            {
                if (zone.IsInvalidTime(candidate))
                {
                    // The clock skipped this local time; there is no such instant.
                    candidate = candidate.AddMinutes(1);
                    continue;
                }

                var offset = zone.GetUtcOffset(
                    zone.IsAmbiguousTime(candidate)
                        ? candidate
                        : candidate);

                // On an ambiguous local time take the earlier (pre-transition)
                // instant, so the repeated hour does not fire the task twice.
                if (zone.IsAmbiguousTime(candidate))
                    offset = zone.GetAmbiguousTimeOffsets(candidate).Max();

                var result = new DateTimeOffset(candidate, offset).ToUniversalTime();
                if (result > afterUtc)
                    return result;
            }

            candidate = candidate.AddMinutes(1);
        }

        return null;
    }

    private static bool Matches(string[] fields, DateTime localTime) =>
        FieldMatches(fields[0], localTime.Minute)
        && FieldMatches(fields[1], localTime.Hour)
        && FieldMatches(fields[2], localTime.Day)
        && FieldMatches(fields[3], localTime.Month)
        && FieldMatches(fields[4], (int)localTime.DayOfWeek);

    /// <summary>Handles *, n, a-b, a,b,c and */step.</summary>
    private static bool FieldMatches(string field, int value)
    {
        foreach (var part in field.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            if (part == "*")
                return true;

            if (part.Contains('/', StringComparison.Ordinal))
            {
                var pieces = part.Split('/', 2);
                if (!int.TryParse(pieces[1], out var step) || step <= 0)
                    continue;
                if (pieces[0] is "*" or "")
                {
                    if (value % step == 0)
                        return true;
                    continue;
                }
                if (TryRange(pieces[0], out var rangeStart, out var rangeEnd)
                    && value >= rangeStart && value <= rangeEnd
                    && (value - rangeStart) % step == 0)
                    return true;
                continue;
            }

            if (TryRange(part, out var start, out var end))
            {
                if (value >= start && value <= end)
                    return true;
                continue;
            }

            if (int.TryParse(part, out var exact) && exact == value)
                return true;
        }
        return false;
    }

    private static bool TryRange(string part, out int start, out int end)
    {
        start = end = 0;
        var pieces = part.Split('-', 2);
        return pieces.Length == 2
               && int.TryParse(pieces[0], out start)
               && int.TryParse(pieces[1], out end);
    }

    private static TimeZoneInfo ResolveZone(string id)
    {
        try
        {
            return TimeZoneInfo.FindSystemTimeZoneById(id);
        }
        catch (Exception ex) when (ex is TimeZoneNotFoundException or InvalidTimeZoneException)
        {
            // A missing zone must not silently become UTC on one machine and
            // local on another; local is what the task was authored against.
            return TimeZoneInfo.Local;
        }
    }

    /// <summary>
    /// True when this trigger is a repeat of one already run.
    ///
    /// The comparison is on the OCCURRENCE, not the trigger time: a wake-from-
    /// sleep catch-up and the ordinary timer can both fire for the same 09:00,
    /// seconds apart, and both are the same scheduled run.
    /// </summary>
    public static bool IsDuplicateTrigger(
        ScheduleSettings settings,
        DateTimeOffset occurrenceUtc,
        IEnumerable<ScheduleRun> history)
    {
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(history);

        return history.Any(run =>
            run.State is not (ScheduleRunState.Skipped or ScheduleRunState.SuppressedDuplicate)
            && (run.ScheduledForUtc - occurrenceUtc).Duration() <= settings.DuplicateSuppressionWindow);
    }

    /// <summary>
    /// Whether a failed run should be tried again, and when.
    /// </summary>
    public static (bool Retry, DateTimeOffset? At, string Reason) ShouldRetry(
        ScheduleSettings settings, ScheduleRun run, DateTimeOffset nowUtc)
    {
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(run);

        if (run.State != ScheduleRunState.Failed)
            return (false, null, "The run did not fail.");

        if (run.Attempt >= settings.MaxAttempts)
            return (false, null,
                $"Attempt {run.Attempt} of {settings.MaxAttempts}; no attempts left.");

        // Linear backoff. A scheduled task that failed because a connector is
        // down should not hammer it, and exponential growth would push the retry
        // past the next occurrence and confuse the two.
        var delay = TimeSpan.FromTicks(settings.RetryDelay.Ticks * run.Attempt);
        return (true, nowUtc + delay,
            $"Attempt {run.Attempt + 1} of {settings.MaxAttempts} in {delay.TotalMinutes:0} min.");
    }
}
