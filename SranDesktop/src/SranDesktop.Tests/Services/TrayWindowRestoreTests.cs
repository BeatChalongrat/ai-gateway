using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Repro coverage for the tray window-state bug: a window hidden
/// while maximized came back small (symptom A) and its maximize button
/// needed two clicks (symptom B — WindowState property stale vs visual).
/// The plan below is what App.ShowMainWindow applies right after Show().</summary>
public class TrayWindowRestoreTests
{
    [Fact]
    public void Hidden_While_Maximized_Restores_Maximized()
    {
        // Symptom A: must come back maximized, via a Normal resync first
        // (symptom B: the pass through Normal is what makes the stale
        // property truthful so the next maximize click works first time).
        var plan = TrayWindowRestore.AfterShow(
            wasHidden: true, wasMaximizedBeforeHide: true, isMinimized: false);
        plan.ForceNormalFirst.Should().BeTrue();
        plan.ThenMaximize.Should().BeTrue();
    }

    [Fact]
    public void Hidden_While_Normal_Comes_Back_Normal_With_Property_Resync()
    {
        var plan = TrayWindowRestore.AfterShow(
            wasHidden: true, wasMaximizedBeforeHide: false, isMinimized: false);
        plan.ForceNormalFirst.Should().BeTrue();
        plan.ThenMaximize.Should().BeFalse();
    }

    [Fact]
    public void Visible_And_Minimized_Is_Unminimized_Only()
    {
        // The pre-existing taskbar-minimize behaviour (hotkey /
        // notification click) is preserved.
        var plan = TrayWindowRestore.AfterShow(
            wasHidden: false, wasMaximizedBeforeHide: false, isMinimized: true);
        plan.ForceNormalFirst.Should().BeTrue();
        plan.ThenMaximize.Should().BeFalse();
    }

    [Fact]
    public void Visible_Foreground_Window_Is_Left_Alone()
    {
        // A notification click while the window is already up (possibly
        // maximized by the user just now) must not touch its state — even
        // if a stale wasMaximizedBeforeHide flag is still set from an
        // earlier hide.
        var plan = TrayWindowRestore.AfterShow(
            wasHidden: false, wasMaximizedBeforeHide: true, isMinimized: false);
        plan.ForceNormalFirst.Should().BeFalse();
        plan.ThenMaximize.Should().BeFalse();
    }
}
