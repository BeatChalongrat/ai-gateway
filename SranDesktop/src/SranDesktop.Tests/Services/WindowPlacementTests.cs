using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 11 Task 2 — where the main window opens after a restart.
///
/// The failure that matters is not "the size was slightly off": it is a
/// window restored onto a monitor that is no longer attached, which opens
/// off-screen and looks exactly like the app failing to start. Every
/// off-screen case below has to end with a window the user can grab.
/// </summary>
public sealed class WindowPlacementTests
{
    private static readonly ScreenArea Primary = new(0, 0, 1920, 1040);
    private static readonly ScreenArea SecondRight = new(1920, 0, 1920, 1040);
    private static readonly IReadOnlyList<ScreenArea> OneScreen = new[] { Primary };
    private static readonly IReadOnlyList<ScreenArea> TwoScreens = new[] { Primary, SecondRight };

    private const double MinW = 800;
    private const double MinH = 500;

    private static WindowBounds? Restore(WindowBounds? saved, IReadOnlyList<ScreenArea> screens)
        => WindowPlacement.Restore(saved, screens, MinW, MinH);

    [Fact]
    public void Nothing_Saved_Means_Keep_The_Default()
    {
        Restore(null, OneScreen).Should().BeNull(
            "a fresh install must fall through to the XAML size + CenterScreen");
    }

    [Fact]
    public void Restores_An_On_Screen_Window_Untouched()
    {
        var saved = new WindowBounds(300, 200, 1200, 760, false);

        var got = Restore(saved, OneScreen);

        got.Should().Be(saved);
    }

    [Fact]
    public void Remembers_The_Maximized_State()
    {
        var saved = new WindowBounds(100, 100, 1200, 760, IsMaximized: true);

        var got = Restore(saved, OneScreen);

        got!.Value.IsMaximized.Should().BeTrue();
        // The restore rectangle survives too, so un-maximizing goes back to
        // the size the user actually chose rather than filling the screen.
        got.Value.Width.Should().Be(1200);
        got.Value.Height.Should().Be(760);
    }

    [Fact]
    public void A_Window_From_An_Unplugged_Second_Monitor_Comes_Back_Onto_The_Primary()
    {
        // Saved while the window lived on the right-hand monitor…
        var saved = new WindowBounds(2400, 300, 1200, 760, false);

        // …which is no longer attached.
        var got = Restore(saved, OneScreen)!.Value;

        got.Left.Should().BeInRange(Primary.Left, Primary.Right - WindowPlacement.MinVisible);
        got.Top.Should().BeInRange(Primary.Top, Primary.Bottom - WindowPlacement.MinVisible);
        (got.Left + got.Width).Should().BeGreaterThan(Primary.Left + WindowPlacement.MinVisible);
    }

    [Fact]
    public void Keeps_The_Window_On_Its_Own_Monitor_When_That_Monitor_Is_Still_There()
    {
        var saved = new WindowBounds(2400, 300, 1200, 760, false);

        var got = Restore(saved, TwoScreens);

        got.Should().Be(saved, "the second monitor is attached — do not move it");
    }

    [Fact]
    public void Pulls_A_Window_Dragged_Off_The_Right_Edge_Back_Into_View()
    {
        var saved = new WindowBounds(1900, 100, 1200, 760, false);

        var got = Restore(saved, OneScreen)!.Value;

        got.Left.Should().BeLessThanOrEqualTo(Primary.Right - WindowPlacement.MinVisible);
        (got.Left + got.Width).Should().BeLessThanOrEqualTo(Primary.Right + 0.001);
    }

    [Fact]
    public void Pulls_A_Window_Pushed_Above_The_Work_Area_Back_Down()
    {
        // Negative Top hides the TITLE BAR — the window becomes undraggable
        // even though most of it is visible.
        var saved = new WindowBounds(300, -400, 1200, 760, false);

        var got = Restore(saved, OneScreen)!.Value;

        got.Top.Should().BeGreaterThanOrEqualTo(Primary.Top);
    }

    [Fact]
    public void Shrinks_A_Window_Bigger_Than_The_Current_Screen()
    {
        // The classic "saved on a 4K monitor, opened on a laptop" case.
        var saved = new WindowBounds(0, 0, 3800, 2000, false);

        var got = Restore(saved, OneScreen)!.Value;

        got.Width.Should().BeLessThanOrEqualTo(Primary.Width);
        got.Height.Should().BeLessThanOrEqualTo(Primary.Height);
        got.Left.Should().BeGreaterThanOrEqualTo(Primary.Left);
        got.Top.Should().BeGreaterThanOrEqualTo(Primary.Top);
    }

    [Fact]
    public void Never_Restores_Below_The_Window_Minimums()
    {
        var saved = new WindowBounds(100, 100, 120, 90, false);

        var got = Restore(saved, OneScreen)!.Value;

        got.Width.Should().BeGreaterThanOrEqualTo(MinW);
        got.Height.Should().BeGreaterThanOrEqualTo(MinH);
    }

    [Theory]
    [InlineData(double.NaN, 100, 1200, 760)]
    [InlineData(100, double.NaN, 1200, 760)]
    [InlineData(100, 100, 0, 760)]
    [InlineData(100, 100, 1200, -5)]
    [InlineData(100, 100, double.PositiveInfinity, 760)]
    public void Garbage_Falls_Back_To_The_Default(double l, double t, double w, double h)
    {
        // A hand-edited or half-written settings.json must not produce an
        // invisible window.
        Restore(new WindowBounds(l, t, w, h, false), OneScreen).Should().BeNull();
    }

    [Fact]
    public void No_Monitors_Reported_Falls_Back_To_The_Default()
    {
        Restore(new WindowBounds(100, 100, 1200, 760, false), Array.Empty<ScreenArea>())
            .Should().BeNull();
    }

    [Fact]
    public void A_Window_Straddling_Two_Monitors_Stays_On_The_One_It_Mostly_Occupies()
    {
        // 80% on the second monitor.
        var saved = new WindowBounds(1700, 100, 1000, 600, false);

        var got = Restore(saved, TwoScreens)!.Value;

        got.Should().Be(saved, "both monitors are present and it is reachable");
    }
}
