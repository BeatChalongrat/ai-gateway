using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Consent-flow foreground restore (computer-use self-interference
/// fix): record/restore decisions via the injected-Win32 test seam.</summary>
public class ForegroundTargetTrackerTests
{
    private const uint OwnPid = 1000;
    private static readonly IntPtr SranWindow = new(0x5AA);
    private static readonly IntPtr Discord = new(0xD15);

    private sealed class Fake
    {
        public IntPtr Foreground;
        public readonly HashSet<IntPtr> OwnWindows = new();
        public readonly HashSet<IntPtr> DeadWindows = new();
        public readonly List<IntPtr> RestoreCalls = new();
        public bool RestoreSucceeds = true;

        public ForegroundTargetTracker Build() => new(
            getForeground: () => Foreground,
            pidOf: h => OwnWindows.Contains(h) ? OwnPid : 2000,
            ownPid: OwnPid,
            isWindowAlive: h => !DeadWindows.Contains(h),
            forceForeground: h =>
            {
                RestoreCalls.Add(h);
                if (RestoreSucceeds) Foreground = h;
                return RestoreSucceeds;
            });
    }

    [Fact]
    public void Records_A_Real_App_And_Restores_It()
    {
        var fake = new Fake { Foreground = Discord };
        var tracker = fake.Build();

        tracker.RecordBeforeConsent();
        tracker.HasTarget.Should().BeTrue();

        fake.Foreground = SranWindow; // consent dialog stole focus
        tracker.RestoreTarget().Should().BeTrue();
        fake.RestoreCalls.Should().Equal(Discord);
    }

    [Fact]
    public void Sran_Foreground_Does_Not_Overwrite_The_Recorded_Target()
    {
        var fake = new Fake { Foreground = Discord };
        fake.OwnWindows.Add(SranWindow);
        var tracker = fake.Build();

        tracker.RecordBeforeConsent();          // records Discord
        fake.Foreground = SranWindow;
        tracker.RecordBeforeConsent();          // chained consent — SRAN foreground, must keep Discord

        fake.Foreground = SranWindow;
        tracker.RestoreTarget().Should().BeTrue();
        fake.RestoreCalls.Should().Equal(Discord);
    }

    [Fact]
    public void No_Target_Or_Dead_Target_Restores_Nothing()
    {
        var fake = new Fake { Foreground = IntPtr.Zero };
        var tracker = fake.Build();
        tracker.HasTarget.Should().BeFalse();
        tracker.RestoreTarget().Should().BeFalse();

        fake.Foreground = Discord;
        tracker.RecordBeforeConsent();
        fake.DeadWindows.Add(Discord); // app closed since
        tracker.RestoreTarget().Should().BeFalse();
        fake.RestoreCalls.Should().BeEmpty();
    }

    [Fact]
    public void RestoreIfOwnForeground_Only_Fires_When_Sran_Holds_The_Foreground()
    {
        var fake = new Fake { Foreground = Discord };
        fake.OwnWindows.Add(SranWindow);
        var tracker = fake.Build();
        tracker.RecordBeforeConsent();

        // A real app is foreground — leave the world alone.
        tracker.RestoreIfOwnForeground().Should().BeFalse();
        fake.RestoreCalls.Should().BeEmpty();

        // SRAN foreground (post-consent) — restore the target.
        fake.Foreground = SranWindow;
        tracker.RestoreIfOwnForeground().Should().BeTrue();
        fake.RestoreCalls.Should().Equal(Discord);
    }

    [Fact]
    public void Failed_Activation_Reports_False()
    {
        var fake = new Fake { Foreground = Discord, RestoreSucceeds = false };
        var tracker = fake.Build();
        tracker.RecordBeforeConsent();

        tracker.RestoreTarget().Should().BeFalse();
    }

    // ── own-window refusal message (computer_click) ───────────────────────────

    [Fact]
    public void OwnWindow_Refusal_Is_Actionable_When_The_Target_Was_Restored()
    {
        ComputerClickTool.OwnWindowRefusal(restored: true).Should().Be(
            "That point is over SRAN's own window. The target app has been restored to the " +
            "foreground — take a fresh screenshot and try again.");
        ComputerClickTool.OwnWindowRefusal(restored: false)
            .Should().Contain("Take a fresh screenshot", "the fallback must still point forward");
    }
}
