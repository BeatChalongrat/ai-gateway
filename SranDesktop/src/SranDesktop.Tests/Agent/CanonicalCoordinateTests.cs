using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class CanonicalCoordinateTests
{
    [Theory]
    [InlineData(1.0)]
    [InlineData(1.25)]
    [InlineData(1.5)]
    [InlineData(1.75)]
    [InlineData(2.0)]
    public void Physical_Mapping_Does_Not_Double_Apply_Windows_Dpi(double dpiScale)
    {
        var capture = Capture(200, 100, 1200, 800, 1200, 800, dpiScale);

        CoordinateMapper.ToPhysical(capture, 300, 200).Should().Be((500, 300));
    }

    [Fact]
    public void Resized_Model_Image_Uses_Independent_Axis_Scales()
    {
        var capture = Capture(-1920, -200, 3840, 1800, 1920, 1200);

        CoordinateMapper.ToPhysical(capture, 960, 600).Should().Be((0, 700));
    }

    [Fact]
    public void Negative_Virtual_Origin_Maps_Vision_Bounding_Box()
    {
        var capture = Capture(-2560, 0, 2560, 1440, 1280, 720, 1.5);

        CoordinateMapper.ToPhysical(capture, new ImageRect(100, 50, 80, 40))
            .Should().Be(new PhysicalRect(-2360, 100, 160, 80));
    }

    [Fact]
    public void Screen_Context_Retains_Captures_By_Id()
    {
        var store = new ScreenContext();
        var first = Capture(0, 0, 1920, 1080, 1920, 1080);
        var second = Capture(1920, 0, 2560, 1440, 1280, 720);

        store.Set(first);
        store.Set(second);

        store.TryGet(first.CaptureId, out var resolved).Should().BeTrue();
        resolved.Should().Be(first);
        store.Last.Should().Be(second);
    }

    private static CaptureContext Capture(
        int x, int y, int sourceWidth, int sourceHeight,
        int imageWidth, int imageHeight, double dpiScale = 1) =>
        new(
            CaptureId: Guid.NewGuid().ToString("N"),
            CapturedAtUtc: DateTimeOffset.UtcNow,
            CaptureType: CaptureType.VirtualDesktop,
            ProcessId: null,
            WindowHandle: null,
            WindowTitle: null,
            MonitorId: null,
            CoordinateSpace: CoordinateSpace.VirtualDesktopImagePixels,
            ImageWidth: imageWidth,
            ImageHeight: imageHeight,
            SourceRectPhysical: new PhysicalRect(x, y, sourceWidth, sourceHeight),
            DpiScaleX: dpiScale,
            DpiScaleY: dpiScale,
            DisplayTopologyVersion: 1,
            WindowGeometryVersion: 0);
}
