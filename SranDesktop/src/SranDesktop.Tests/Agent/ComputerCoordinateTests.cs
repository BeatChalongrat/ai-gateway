using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Computer-use accuracy overhaul: the screenshot→screen coordinate
/// mapping, virtual-desktop bounds (negative origins), and zoom-region
/// clamping — pure math, no P/Invoke.</summary>
public class ComputerCoordinateTests
{
    [Fact]
    public void ToScreen_Without_A_Mapping_Passes_Coordinates_Through()
    {
        ScreenContext.ToScreen(null, 123, 456).Should().Be((123, 456));
    }

    [Fact]
    public void ToScreen_Applies_Scale_From_A_Downscaled_Screenshot()
    {
        // 3840x2160 captured → 2560x1440 image: scale 1.5.
        var mapping = new ScreenMapping(1.5, 0, 0, 3840, 2160, 2560, 1440);

        ScreenContext.ToScreen(mapping, 1000, 500).Should().Be((1500, 750));
        ScreenContext.ToScreen(mapping, 0, 0).Should().Be((0, 0));
        // Edge pixels stay INSIDE the captured area (Math.Round half-to-even).
        ScreenContext.ToScreen(mapping, 2559, 1439).Should().Be((3838, 2158));
    }

    [Fact]
    public void ToScreen_Applies_A_Negative_Virtual_Desktop_Origin()
    {
        // A 1920x1080 monitor LEFT of the primary: virtual desktop starts at -1920.
        var mapping = new ScreenMapping(1.5, -1920, 0, 5760, 1080, 3840, 720);

        ScreenContext.ToScreen(mapping, 0, 0).Should().Be((-1920, 0));
        ScreenContext.ToScreen(mapping, 1280, 360).Should().Be((0, 540)); // primary's left edge
    }

    [Fact]
    public void ToScreen_Applies_A_Window_Capture_Offset()
    {
        // Unscaled window capture at (800, 200): window-relative pixels become absolute.
        var mapping = new ScreenMapping(1.0, 800, 200, 600, 400, 600, 400);

        ScreenContext.ToScreen(mapping, 10, 20).Should().Be((810, 220));
    }

    [Fact]
    public void InVirtualScreen_Accepts_Negative_Coordinates_Inside_The_Rect()
    {
        var rect = (X: -1920, Y: -200, Width: 5760, Height: 1280);

        ScreenContext.InVirtualScreen(-1920, -200, rect).Should().BeTrue();
        ScreenContext.InVirtualScreen(0, 0, rect).Should().BeTrue();
        ScreenContext.InVirtualScreen(3839, 1079, rect).Should().BeTrue();
        ScreenContext.InVirtualScreen(-1921, 0, rect).Should().BeFalse();
        ScreenContext.InVirtualScreen(3840, 0, rect).Should().BeFalse();
        ScreenContext.InVirtualScreen(0, 1080, rect).Should().BeFalse();
    }

    // ── zoom region mapping ───────────────────────────────────────────────────

    [Fact]
    public void MapZoomRegion_Scales_And_Offsets_The_Region()
    {
        var mapping = new ScreenMapping(2.0, -1920, 0, 5120, 1440, 2560, 720);

        var region = ScreenContext.MapZoomRegion(mapping, 100, 50, 200, 100);

        region.Should().Be((-1720, 100, 400, 200));
    }

    [Fact]
    public void MapZoomRegion_Clamps_To_The_Captured_Area()
    {
        var mapping = new ScreenMapping(1.0, 0, 0, 1920, 1080, 1920, 1080);

        // Region pokes past right/bottom → trimmed, not rejected.
        ScreenContext.MapZoomRegion(mapping, 1800, 1000, 500, 500)
            .Should().Be((1800, 1000, 120, 80));
        // Region starting before the origin is trimmed from the left/top.
        ScreenContext.MapZoomRegion(mapping, -50, -50, 100, 100)
            .Should().Be((0, 0, 50, 50));
    }

    [Fact]
    public void MapZoomRegion_Rejects_Regions_Outside_The_Capture_Or_Empty()
    {
        var mapping = new ScreenMapping(1.0, 0, 0, 1920, 1080, 1920, 1080);

        ScreenContext.MapZoomRegion(mapping, 2000, 0, 100, 100).Should().BeNull();
        ScreenContext.MapZoomRegion(mapping, 100, 100, 0, 50).Should().BeNull();
        ScreenContext.MapZoomRegion(mapping, 100, 100, 50, -1).Should().BeNull();
    }
}
