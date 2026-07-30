using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class NativeInputResultTests
{
    [Fact]
    public void ClickCore_Reports_SetCursorPos_Failure_Without_Sending_Input()
    {
        var sendCalls = 0;

        var result = Win32Input.ClickCore(
            (_, _) => false,
            (_, _) =>
            {
                sendCalls++;
                return 1;
            },
            100, 200, "left", 1);

        result.CursorPositioned.Should().BeFalse();
        result.OsInputAccepted.Should().BeFalse();
        sendCalls.Should().Be(0);
    }

    [Fact]
    public void ClickCore_Reports_Partial_SendInput_Failure()
    {
        var sendCalls = 0;

        var result = Win32Input.ClickCore(
            (_, _) => true,
            (_, _) => ++sendCalls == 1 ? 1u : 0u,
            100, 200, "left", 1);

        result.RequestedInputEvents.Should().Be(2);
        result.AcceptedInputEvents.Should().Be(1);
        result.OsInputAccepted.Should().BeFalse();
    }

    [Fact]
    public void ClickCore_Accepts_Only_All_Requested_Events()
    {
        var result = Win32Input.ClickCore(
            (_, _) => true,
            (_, _) => 1,
            100, 200, "left", 2);

        result.RequestedInputEvents.Should().Be(4);
        result.AcceptedInputEvents.Should().Be(4);
        result.OsInputAccepted.Should().BeTrue();
    }
}
