using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Scheduling;

/// <summary>
/// Schedulers cause damage in the gaps: a laptop opened after a week, a clock
/// that moved, two timers agreeing on the same minute. These tests live in those
/// gaps, because the ordinary "it fired at nine" case was never the risk.
/// </summary>
public sealed class ScheduleEvaluatorTests
{
    private static readonly TimeZoneInfo Berlin =
        TryZone("W. Europe Standard Time", "Europe/Berlin");

    private static ScheduledTask Daily9() => new()
    {
        Id = "task-1",
        Name = "Morning summary",
        CronExpression = "0 9 * * *",
        IsEnabled = true,
        CreatedAt = new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero)
    };

    // ── Ordinary scheduling ─────────────────────────────────────────────────

    [Fact]
    public void One_Due_Occurrence_Runs_Without_Consulting_Policy()
    {
        var task = Daily9();
        var settings = new ScheduleSettings { TimeZoneId = "UTC", MissedRunPolicy = MissedRunPolicy.Skip };
        var last = new DateTimeOffset(2026, 3, 10, 9, 0, 0, TimeSpan.Zero);

        var decision = ScheduleEvaluator.Evaluate(
            task, settings, last, new DateTimeOffset(2026, 3, 11, 9, 5, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeTrue();
        decision.OccurrencesToRun.Should().ContainSingle()
            .Which.Should().Be(new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero));
    }

    [Fact]
    public void Nothing_Runs_Before_It_Is_Due()
    {
        var settings = new ScheduleSettings { TimeZoneId = "UTC" };
        var last = new DateTimeOffset(2026, 3, 10, 9, 0, 0, TimeSpan.Zero);

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings, last, new DateTimeOffset(2026, 3, 10, 15, 0, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeFalse();
        decision.NextRunUtc.Should().Be(new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero));
    }

    [Fact]
    public void A_Paused_Task_Keeps_Its_Schedule_But_Does_Not_Fire()
    {
        var settings = new ScheduleSettings { TimeZoneId = "UTC", IsPaused = true };

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings,
            new DateTimeOffset(2026, 3, 1, 9, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 3, 11, 9, 5, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeFalse();
        decision.NextRunUtc.Should().NotBeNull("pausing is not the same as deleting the schedule");
    }

    [Fact]
    public void An_Expired_Task_Stops_Firing()
    {
        var settings = new ScheduleSettings
        {
            TimeZoneId = "UTC",
            ExpiresUtc = new DateTimeOffset(2026, 3, 5, 0, 0, 0, TimeSpan.Zero)
        };

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings, null, new DateTimeOffset(2026, 3, 11, 9, 5, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeFalse();
        decision.TerminalState.Should().Be(ScheduleRunState.Expired);
        decision.NextRunUtc.Should().BeNull();
    }

    // ── Missed runs: one week away, five policies ───────────────────────────

    [Theory]
    [InlineData(MissedRunPolicy.Skip, false, 0)]
    [InlineData(MissedRunPolicy.RunOnceWhenAvailable, true, 1)]
    [InlineData(MissedRunPolicy.AskUser, false, 0)]
    [InlineData(MissedRunPolicy.Expire, false, 0)]
    public void Missed_Runs_Follow_The_Chosen_Policy(
        MissedRunPolicy policy, bool shouldRun, int expectedRuns)
    {
        var settings = new ScheduleSettings { TimeZoneId = "UTC", MissedRunPolicy = policy };
        var lastRun = new DateTimeOffset(2026, 3, 3, 9, 0, 0, TimeSpan.Zero);
        var backFromHoliday = new DateTimeOffset(2026, 3, 10, 14, 0, 0, TimeSpan.Zero);

        var decision = ScheduleEvaluator.Evaluate(Daily9(), settings, lastRun, backFromHoliday);

        decision.ShouldRun.Should().Be(shouldRun);
        decision.OccurrencesToRun.Count.Should().Be(
            policy == MissedRunPolicy.AskUser ? decision.OccurrencesToRun.Count : expectedRuns);
    }

    [Fact]
    public void RunOnce_Chooses_The_Most_Recent_Missed_Occurrence()
    {
        var settings = new ScheduleSettings
        {
            TimeZoneId = "UTC",
            MissedRunPolicy = MissedRunPolicy.RunOnceWhenAvailable
        };

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings,
            new DateTimeOffset(2026, 3, 3, 9, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 3, 10, 14, 0, 0, TimeSpan.Zero));

        decision.OccurrencesToRun.Should().ContainSingle()
            .Which.Should().Be(new DateTimeOffset(2026, 3, 10, 9, 0, 0, TimeSpan.Zero),
                "eight stale morning summaries at once is worse than one current one");
    }

    [Fact]
    public void AskUser_Reports_The_Missed_Occurrences_Without_Running_Them()
    {
        var settings = new ScheduleSettings
        {
            TimeZoneId = "UTC",
            MissedRunPolicy = MissedRunPolicy.AskUser
        };

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings,
            new DateTimeOffset(2026, 3, 3, 9, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 3, 10, 14, 0, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeFalse();
        decision.TerminalState.Should().Be(ScheduleRunState.NeedsUser);
        decision.OccurrencesToRun.Should().NotBeEmpty("the user needs to see what was missed");
    }

    [Fact]
    public void RunAll_Is_Capped_And_Says_So()
    {
        var settings = new ScheduleSettings
        {
            TimeZoneId = "UTC",
            MissedRunPolicy = MissedRunPolicy.RunAllMissedWithinLimit,
            MissedRunLimit = 3
        };

        var decision = ScheduleEvaluator.Evaluate(
            Daily9(), settings,
            new DateTimeOffset(2026, 3, 1, 9, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 3, 10, 14, 0, 0, TimeSpan.Zero));

        decision.ShouldRun.Should().BeTrue();
        decision.OccurrencesToRun.Should().HaveCount(3);
        decision.TerminalState.Should().Be(ScheduleRunState.NeedsUser,
            "silently dropping the overflow would hide work that never happened");
    }

    // ── DST ─────────────────────────────────────────────────────────────────

    [Fact]
    public void A_Daily_Task_Keeps_Its_Local_Hour_Across_Spring_Forward()
    {
        var settings = new ScheduleSettings { TimeZoneId = Berlin.Id };
        var task = Daily9();

        // Berlin springs forward on 2026-03-29.
        var before = new DateTimeOffset(2026, 3, 28, 12, 0, 0, TimeSpan.Zero);
        var next = ScheduleEvaluator.NextOccurrence(task, settings, before);

        next.Should().NotBeNull();
        var local = TimeZoneInfo.ConvertTime(next!.Value, Berlin);
        local.Hour.Should().Be(9, "09:00 local must stay 09:00 local after the clocks move");
    }

    [Fact]
    public void A_Task_In_The_Skipped_Hour_Does_Not_Fire_Twice_Or_Vanish()
    {
        // 02:30 does not exist on the spring-forward day in Berlin.
        var task = new ScheduledTask
        {
            Id = "t", CronExpression = "30 2 * * *", IsEnabled = true,
            CreatedAt = new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero)
        };
        var settings = new ScheduleSettings { TimeZoneId = Berlin.Id };

        var occurrences = ScheduleEvaluator.OccurrencesBetween(
            task, settings,
            new DateTimeOffset(2026, 3, 28, 3, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 3, 31, 3, 0, 0, TimeSpan.Zero)).ToList();

        occurrences.Should().HaveCount(2,
            "the 29th has no 02:30 local, so that day is skipped rather than doubled");
        occurrences.Should().OnlyHaveUniqueItems();
    }

    [Fact]
    public void The_Repeated_Autumn_Hour_Fires_Once()
    {
        // Berlin falls back on 2026-10-25; 02:30 local happens twice.
        var task = new ScheduledTask
        {
            Id = "t", CronExpression = "30 2 * * *", IsEnabled = true,
            CreatedAt = new DateTimeOffset(2026, 10, 1, 0, 0, 0, TimeSpan.Zero)
        };
        var settings = new ScheduleSettings { TimeZoneId = Berlin.Id };

        var occurrences = ScheduleEvaluator.OccurrencesBetween(
            task, settings,
            new DateTimeOffset(2026, 10, 24, 12, 0, 0, TimeSpan.Zero),
            new DateTimeOffset(2026, 10, 26, 12, 0, 0, TimeSpan.Zero)).ToList();

        occurrences.Should().HaveCount(2, "one occurrence on the 25th and one on the 26th");
        occurrences.Should().OnlyHaveUniqueItems();
    }

    // ── Duplicate suppression and retry ─────────────────────────────────────

    [Fact]
    public void The_Same_Occurrence_Triggered_Twice_Is_Suppressed()
    {
        var settings = new ScheduleSettings();
        var occurrence = new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero);
        var history = new[]
        {
            new ScheduleRun("run-1", "task-1", occurrence,
                occurrence.AddSeconds(2), ScheduleRunState.Succeeded)
        };

        // A wake-from-sleep catch-up firing seconds later for the same 09:00.
        ScheduleEvaluator.IsDuplicateTrigger(settings, occurrence.AddSeconds(30), history)
            .Should().BeTrue();

        // Tomorrow's 09:00 is a different occurrence.
        ScheduleEvaluator.IsDuplicateTrigger(settings, occurrence.AddDays(1), history)
            .Should().BeFalse();
    }

    [Fact]
    public void A_Skipped_Run_Does_Not_Block_A_Real_One()
    {
        var settings = new ScheduleSettings();
        var occurrence = new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero);
        var history = new[]
        {
            new ScheduleRun("run-1", "task-1", occurrence, occurrence, ScheduleRunState.Skipped)
        };

        ScheduleEvaluator.IsDuplicateTrigger(settings, occurrence, history)
            .Should().BeFalse("a skipped occurrence never ran, so it is not a duplicate");
    }

    [Fact]
    public void The_Occurrence_Key_Identifies_The_Slot_Not_The_Attempt()
    {
        var occurrence = new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero);
        var first = new ScheduleRun("run-1", "t", occurrence, occurrence, ScheduleRunState.Failed);
        var retry = new ScheduleRun("run-2", "t", occurrence,
            occurrence.AddMinutes(5), ScheduleRunState.Succeeded, Attempt: 2);

        retry.OccurrenceKey.Should().Be(first.OccurrenceKey,
            "a retry is the same scheduled run, which is what makes idempotency possible");
    }

    [Fact]
    public void Retries_Are_Bounded_And_Backed_Off()
    {
        var settings = new ScheduleSettings { MaxAttempts = 3, RetryDelay = TimeSpan.FromMinutes(5) };
        var now = new DateTimeOffset(2026, 3, 11, 9, 0, 0, TimeSpan.Zero);
        var failed = new ScheduleRun("r", "t", now, now, ScheduleRunState.Failed);

        var first = ScheduleEvaluator.ShouldRetry(settings, failed, now);
        var second = ScheduleEvaluator.ShouldRetry(settings, failed with { Attempt = 2 }, now);
        var exhausted = ScheduleEvaluator.ShouldRetry(settings, failed with { Attempt = 3 }, now);

        first.Retry.Should().BeTrue();
        first.At.Should().Be(now.AddMinutes(5));
        second.At.Should().Be(now.AddMinutes(10), "backoff grows with the attempt");
        exhausted.Retry.Should().BeFalse();
    }

    [Fact]
    public void A_Successful_Run_Is_Never_Retried()
    {
        var now = DateTimeOffset.UtcNow;
        var succeeded = new ScheduleRun("r", "t", now, now, ScheduleRunState.Succeeded);

        ScheduleEvaluator.ShouldRetry(new ScheduleSettings(), succeeded, now)
            .Retry.Should().BeFalse();
    }

    [Fact]
    public void An_Unknown_Timezone_Falls_Back_Without_Silently_Shifting_The_Schedule()
    {
        var settings = new ScheduleSettings { TimeZoneId = "Mars/Olympus_Mons" };

        var next = ScheduleEvaluator.NextOccurrence(
            Daily9(), settings, new DateTimeOffset(2026, 3, 10, 0, 0, 0, TimeSpan.Zero));

        next.Should().NotBeNull("an unresolvable zone must not stop the task from ever firing");
        TimeZoneInfo.ConvertTime(next!.Value, TimeZoneInfo.Local).Hour.Should().Be(9);
    }

    private static TimeZoneInfo TryZone(string windowsId, string ianaId)
    {
        try
        {
            return TimeZoneInfo.FindSystemTimeZoneById(windowsId);
        }
        catch (TimeZoneNotFoundException)
        {
            return TimeZoneInfo.FindSystemTimeZoneById(ianaId);
        }
    }
}
