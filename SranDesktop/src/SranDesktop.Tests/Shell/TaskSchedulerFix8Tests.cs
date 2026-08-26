using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX8 Part A — the scheduled-task logic that does NOT need a live turn: the
/// missed-run catch-up decision (A1.1), the deterministic stagger (A1.5), and the
/// run-history / permission-mode persistence (A1.2/A1.3). The live criteria (a real
/// catch-up firing, a manual-mode stall) are owner-gated on a signed-in session; the
/// DECISIONS behind them are proven here.
/// </summary>
public class TaskCatchUpTests
{
    // A daily 06:00 task, last run 3 days ago → it missed today, yesterday, and the
    // day before: THREE occurrences. Catch up exactly the MOST RECENT; the other two
    // are discarded as skipped. (The criterion warns: miss at least three.)
    [Fact]
    public void Catches_up_exactly_once_for_the_most_recent_and_discards_older()
    {
        var now = new DateTimeOffset(2026, 8, 25, 12, 0, 0, TimeSpan.Zero);
        var lastRun = now.AddDays(-4); // Aug 21 12:00 → misses the 06:00 on Aug 22, 23, 24, 25 = FOUR
        var (mostRecent, older) = TaskCatchUp.Compute("0 6 * * *", lastRun, now);

        mostRecent.Should().NotBeNull();
        mostRecent!.Value.Hour.Should().Be(6);
        mostRecent.Value.Date.Should().Be(now.Date);          // today's 06:00 is the most recent missed
        (older.Count + 1).Should().Be(4);                     // four missed in total (≥ three, per the criterion)
        older.Should().HaveCount(3);                          // exactly three discarded as skipped
        older.Should().OnlyContain(d => d < mostRecent.Value);
    }

    [Fact]
    public void Discards_everything_older_than_seven_days()
    {
        var now = new DateTimeOffset(2026, 8, 25, 12, 0, 0, TimeSpan.Zero);
        var lastRun = now.AddDays(-30); // the app was closed for a month
        var (mostRecent, older) = TaskCatchUp.Compute("0 6 * * *", lastRun, now);

        mostRecent.Should().NotBeNull();
        // Only the last 7 days count: at most 7 daily occurrences in the window, so
        // ONE catch-up and AT MOST six older — never the 30 that actually elapsed.
        older.Count.Should().BeLessThanOrEqualTo(6);
        (older.Count + 1).Should().BeLessThanOrEqualTo(7);
    }

    [Fact]
    public void No_missed_occurrence_means_no_catch_up()
    {
        var now = new DateTimeOffset(2026, 8, 25, 12, 0, 0, TimeSpan.Zero);
        var (mostRecent, older) = TaskCatchUp.Compute("0 6 * * *", now.AddMinutes(-5), now); // last run 5 min ago, today's 06:00 already done
        mostRecent.Should().BeNull();
        older.Should().BeEmpty();
    }
}

public class TaskStaggerTests
{
    [Fact]
    public void Is_deterministic_across_restarts_and_in_range()
    {
        var id = "a1b2c3d4e5";
        TaskStagger.SecondsFor(id).Should().Be(TaskStagger.SecondsFor(id));     // same id → same offset, every time
        TaskStagger.SecondsFor(id).Should().BeInRange(0, 59);
    }

    [Fact]
    public void Different_tasks_generally_get_different_offsets()
    {
        var offsets = Enumerable.Range(0, 20).Select(i => TaskStagger.SecondsFor($"task-{i}")).Distinct().Count();
        offsets.Should().BeGreaterThan(10); // not all-zero, not all-identical
    }
}

public class TaskRunHistoryStoreTests
{
    private static SqliteScheduledTaskStore Fresh() =>
        new(System.IO.Path.Combine(System.IO.Path.GetTempPath(), "fix8-" + Guid.NewGuid().ToString("N"), "tasks.db"));

    [Fact]
    public async Task Permission_mode_persists_and_defaults_to_manual()
    {
        var store = Fresh();
        await store.UpsertAsync(new ScheduledTask { Id = "t1", Name = "n", Prompt = "p", CronExpression = "0 6 * * *", PermissionMode = "skip" });
        await store.UpsertAsync(new ScheduledTask { Id = "t2", Name = "n", Prompt = "p", CronExpression = "0 6 * * *" }); // default
        var all = await store.GetAllAsync();
        all.Single(t => t.Id == "t1").PermissionMode.Should().Be("skip");
        all.Single(t => t.Id == "t2").PermissionMode.Should().Be("manual");
    }

    [Fact]
    public async Task Run_history_records_the_three_skip_reasons_and_returns_newest_first()
    {
        var store = Fresh();
        var now = DateTimeOffset.UtcNow;
        await store.AppendRunAsync(new TaskRun { TaskId = "t1", RanAt = now.AddMinutes(-3), Outcome = "skipped", Reason = "asleep" });
        await store.AppendRunAsync(new TaskRun { TaskId = "t1", RanAt = now.AddMinutes(-2), Outcome = "skipped", Reason = "prev_running" });
        await store.AppendRunAsync(new TaskRun { TaskId = "t1", RanAt = now.AddMinutes(-1), Outcome = "skipped", Reason = "others_running" });
        await store.AppendRunAsync(new TaskRun { TaskId = "t1", RanAt = now, Outcome = "ok", Result = "done" });

        var runs = await store.GetRunsAsync("t1", 10);
        runs.Should().HaveCount(4);
        runs[0].Outcome.Should().Be("ok");                          // newest first
        runs.Select(r => r.Reason).Should().Contain(new[] { "asleep", "prev_running", "others_running" });
    }
}
