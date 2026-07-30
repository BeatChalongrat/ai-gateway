using System.Drawing;
using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Security;

public sealed class ScreenCaptureSecurityTests
{
    [Fact]
    public void Allowed_Active_Window_Has_Known_Classification()
    {
        var policy = new ScreenCaptureSecurityPolicy(new ScreenCaptureSecurityOptions());

        var result = policy.Evaluate(new ScreenCaptureSecurityContext(
            CaptureType.Window,
            "notepad",
            "notes.txt - Notepad",
            null,
            800,
            600));

        result.Allowed.Should().BeTrue();
        result.ClassificationKnown.Should().BeTrue();
        result.DecisionCode.Should().Be("Allowed");
    }

    [Theory]
    [InlineData("CredentialUIBroker", "Sign in", "ProtectedApplication")]
    [InlineData("notepad", "Windows Security", "ProtectedWindow")]
    public void Protected_Process_Or_Window_Is_Denied(
        string process,
        string title,
        string expectedCode)
    {
        var policy = new ScreenCaptureSecurityPolicy(new ScreenCaptureSecurityOptions());

        var result = policy.Evaluate(new ScreenCaptureSecurityContext(
            CaptureType.Window,
            process,
            title,
            null,
            800,
            600));

        result.Allowed.Should().BeFalse();
        result.DecisionCode.Should().Be(expectedCode);
    }

    [Theory]
    [InlineData(CaptureType.Monitor)]
    [InlineData(CaptureType.DesktopOverview)]
    public void Unknown_Broad_Capture_Fails_Closed_When_Protection_Is_Required(
        CaptureType captureType)
    {
        var policy = new ScreenCaptureSecurityPolicy(new ScreenCaptureSecurityOptions
        {
            DenyUnknownExternalClassification = true
        });

        var result = policy.Evaluate(new ScreenCaptureSecurityContext(
            captureType,
            null,
            null,
            "monitor-0",
            800,
            600));

        result.Allowed.Should().BeFalse();
        result.DecisionCode.Should().Be("UnknownScreenClassification");
    }

    [Fact]
    public void Configured_Sensitive_Rectangle_Is_Redacted_In_Real_Bitmap()
    {
        var policy = new ScreenCaptureSecurityPolicy(new ScreenCaptureSecurityOptions
        {
            RedactionRules =
            [
                new ScreenRedactionRule(
                    new ImageRect(10, 15, 20, 25),
                    ProcessName: "notepad",
                    CaptureType: CaptureType.Window)
            ]
        });
        var decision = policy.Evaluate(new ScreenCaptureSecurityContext(
            CaptureType.Window,
            "notepad.exe",
            "notes - Notepad",
            null,
            100,
            100));
        using var bitmap = new Bitmap(100, 100);
        using (var graphics = Graphics.FromImage(bitmap))
            graphics.Clear(Color.White);

        new SolidScreenImageRedactor().Redact(bitmap, decision.RedactionRegions);

        decision.DecisionCode.Should().Be("AllowedWithRedaction");
        bitmap.GetPixel(15, 20).ToArgb().Should().Be(Color.Black.ToArgb());
        bitmap.GetPixel(5, 5).ToArgb().Should().Be(Color.White.ToArgb());
    }

    [Fact]
    public void Capture_Indicator_Has_Balanced_Lifetime()
    {
        var indicator = new ScreenCaptureIndicator();
        var states = new List<(bool Active, string? Scope)>();
        indicator.CaptureStateChanged += (active, scope) => states.Add((active, scope));

        using (indicator.Begin("Window"))
            states.Should().ContainSingle().Which.Should().Be((true, "Window"));

        states.Should().Equal((true, "Window"), (false, null));
    }
}
