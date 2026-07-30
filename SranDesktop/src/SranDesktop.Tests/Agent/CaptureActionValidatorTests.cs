using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class CaptureActionValidatorTests
{
    private static readonly DateTimeOffset Now =
        new(2026, 7, 19, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Rejects_Stale_Capture()
    {
        Validate(Capture(capturedAt: Now.AddSeconds(-6))).FailureCode
            .Should().Be(CaptureValidationFailure.CaptureStale);
    }

    [Fact]
    public void Rejects_Hwnd_That_No_Longer_Exists()
    {
        Validate(Capture(), Live() with { WindowExists = false }).FailureCode
            .Should().Be(CaptureValidationFailure.WindowNoLongerExists);
    }

    [Fact]
    public void Rejects_Pid_Mismatch()
    {
        Validate(Capture(), Live() with { ProcessId = 99 }).FailureCode
            .Should().Be(CaptureValidationFailure.ProcessMismatch);
    }

    [Theory]
    [InlineData(101, 100, 800, 600)]
    [InlineData(100, 100, 801, 600)]
    public void Rejects_Window_Move_Or_Resize(int x, int y, int width, int height)
    {
        var live = Live() with
        {
            WindowRectPhysical = new PhysicalRect(x, y, width, height)
        };

        Validate(Capture(), live, new ComputerUseOptions
        {
            MaximumGeometryDifference = 0
        }).FailureCode.Should().Be(CaptureValidationFailure.WindowGeometryChanged);
    }

    [Fact]
    public void Rejects_Foreground_Window_Mismatch()
    {
        Validate(Capture(), Live() with { ForegroundWindowHandle = 88 }).FailureCode
            .Should().Be(CaptureValidationFailure.ForegroundWindowMismatch);
    }

    [Fact]
    public void Rejects_Display_Topology_Change()
    {
        Validate(Capture(), Live() with { DisplayTopologyVersion = 6 }).FailureCode
            .Should().Be(CaptureValidationFailure.DisplayTopologyChanged);
    }

    [Fact]
    public void Rejects_Overlay_At_Click_Point()
    {
        Validate(Capture(), Live() with { WindowAtPointHandle = 77 }).FailureCode
            .Should().Be(CaptureValidationFailure.PointCoveredByOtherWindow);
    }

    [Fact]
    public void Accepts_Matching_Fresh_Window_State()
    {
        Validate(Capture()).IsValid.Should().BeTrue();
    }

    private static CaptureValidationResult Validate(
        CaptureContext capture,
        LiveWindowState? live = null,
        ComputerUseOptions? options = null) =>
        CaptureActionValidator.Validate(
            capture, (300, 300), live ?? Live(), options ?? new ComputerUseOptions());

    private static CaptureContext Capture(DateTimeOffset? capturedAt = null) =>
        new(
            CaptureId: "capture-1",
            CapturedAtUtc: capturedAt ?? Now.AddSeconds(-1),
            CaptureType: CaptureType.Window,
            ProcessId: 42,
            WindowHandle: 10,
            WindowTitle: "Test",
            MonitorId: "monitor-1",
            CoordinateSpace: CoordinateSpace.WindowImagePixels,
            ImageWidth: 800,
            ImageHeight: 600,
            SourceRectPhysical: new PhysicalRect(100, 100, 800, 600),
            DpiScaleX: 1.25,
            DpiScaleY: 1.25,
            DisplayTopologyVersion: 5,
            WindowGeometryVersion: 7);

    private static LiveWindowState Live() =>
        new(
            ObservedAtUtc: Now,
            WindowExists: true,
            ProcessId: 42,
            WindowRectPhysical: new PhysicalRect(100, 100, 800, 600),
            DisplayTopologyVersion: 5,
            ForegroundWindowHandle: 10,
            WindowAtPointHandle: 10,
            HostProcessId: 1000);
}
