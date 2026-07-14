using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 8 Task 1: the pure toast queue — ordering, cap, dedupe,
/// per-kind durations, dismissal. Time is injected; no sleeps.</summary>
public class ToastServiceTests
{
    private DateTimeOffset _now = new(2026, 7, 13, 12, 0, 0, TimeSpan.Zero);
    private ToastService Create() => new(() => _now);

    [Fact]
    public void Shows_Newest_At_Bottom()
    {
        var svc = Create();
        svc.Show(ToastKind.Info, "toast.a");
        svc.Show(ToastKind.Info, "toast.b");

        svc.Visible.Select(t => t.MessageKey).Should().Equal("toast.a", "toast.b");
    }

    [Fact]
    public void Caps_Visible_At_Three_And_Promotes_On_Dismiss()
    {
        var svc = Create();
        for (var i = 0; i < 5; i++)
            svc.Show(ToastKind.Info, $"toast.{i}");

        svc.Visible.Should().HaveCount(3);
        svc.Visible.Select(t => t.MessageKey).Should().Equal("toast.0", "toast.1", "toast.2");

        svc.Dismiss(svc.Visible[0]);
        svc.Visible.Select(t => t.MessageKey).Should().Equal("toast.1", "toast.2", "toast.3");
    }

    [Fact]
    public void Identical_Consecutive_Message_Dedupes_By_Extending()
    {
        var svc = Create();
        svc.Show(ToastKind.Success, "toast.copied");
        var original = svc.Visible.Single();
        var firstExpiry = original.ExpiresAt;

        _now += TimeSpan.FromSeconds(1);
        svc.Show(ToastKind.Success, "toast.copied");

        svc.Visible.Should().HaveCount(1);
        svc.Visible.Single().Should().BeSameAs(original);
        svc.Visible.Single().ExpiresAt.Should().BeAfter(firstExpiry);
    }

    [Fact]
    public void Different_Detail_Is_Not_A_Duplicate()
    {
        var svc = Create();
        svc.Show(ToastKind.Success, "toast.exportSaved", "a.md");
        svc.Show(ToastKind.Success, "toast.exportSaved", "b.md");

        svc.Visible.Should().HaveCount(2);
    }

    [Fact]
    public void Durations_Are_Per_Kind()
    {
        var svc = Create();
        svc.Show(ToastKind.Info, "toast.i");
        svc.Show(ToastKind.Success, "toast.s");
        svc.Show(ToastKind.Error, "toast.e");

        var byKey = svc.Visible.ToDictionary(t => t.MessageKey);
        (byKey["toast.i"].ExpiresAt - _now).Should().Be(ToastService.InfoDuration);
        (byKey["toast.s"].ExpiresAt - _now).Should().Be(ToastService.InfoDuration);
        (byKey["toast.e"].ExpiresAt - _now).Should().Be(ToastService.ErrorDuration);
    }

    [Fact]
    public void ExpireDue_Removes_Expired_And_Starts_Pending_Clock_On_Promotion()
    {
        var svc = Create();
        for (var i = 0; i < 4; i++)
            svc.Show(ToastKind.Info, $"toast.{i}"); // toast.3 pending

        _now += TimeSpan.FromSeconds(4); // info toasts (3 s) are all due
        svc.ExpireDue();

        var promoted = svc.Visible.Single();
        promoted.MessageKey.Should().Be("toast.3");
        // Its clock started at promotion, not at Show().
        promoted.ExpiresAt.Should().Be(_now + ToastService.InfoDuration);
    }

    [Fact]
    public void Explicit_Duration_Overrides_The_Kind_Default()
    {
        var svc = Create();
        svc.Show(ToastKind.Info, "toast.long", duration: TimeSpan.FromSeconds(30));
        (svc.Visible.Single().ExpiresAt - _now).Should().Be(TimeSpan.FromSeconds(30));
    }

    [Fact]
    public void Changed_Fires_On_Show_Dismiss_And_Expiry()
    {
        var svc = Create();
        var raised = 0;
        svc.Changed += (_, _) => raised++;

        svc.Show(ToastKind.Info, "toast.x");
        raised.Should().Be(1);

        svc.Dismiss(svc.Visible.Single());
        raised.Should().Be(2);

        svc.Show(ToastKind.Info, "toast.y");
        _now += TimeSpan.FromSeconds(4);
        svc.ExpireDue();
        raised.Should().Be(4);

        // No-op expiry stays silent.
        svc.ExpireDue();
        raised.Should().Be(4);
    }
}
