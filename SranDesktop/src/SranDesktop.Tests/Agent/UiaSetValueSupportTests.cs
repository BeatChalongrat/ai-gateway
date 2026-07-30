using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>T1: typing into Windows 11 Notepad / RichEdit (TextPattern-only, no
/// ValuePattern) must verify from a NORMALIZED read-back — a trailing \r\n or leading
/// document content no longer reports a real edit as unverified — and must never type
/// until the target window is confirmed foreground. Pure helpers, no live desktop.</summary>
public sealed class UiaSetValueSupportTests
{
    [Fact]
    public void Trailing_Newline_Read_Back_Counts_As_A_Match()
    {
        UiaSetValueTool.ValueReadBackMatches("hello\r\n", "hello", allowSuffix: false)
            .Should().BeTrue();
        UiaSetValueTool.ValueReadBackMatches("hello   ", "hello", allowSuffix: false)
            .Should().BeTrue();
        UiaSetValueTool.ValueReadBackMatches("hello", "hello", allowSuffix: false)
            .Should().BeTrue();
    }

    [Fact]
    public void Document_Control_Accepts_A_Suffix_Match_But_Plain_Control_Does_Not()
    {
        // TextPattern document may hold leading content + our text at the end.
        UiaSetValueTool.ValueReadBackMatches("previous line\r\nhello\r\n", "hello", allowSuffix: true)
            .Should().BeTrue();
        // Without the document flag, only a full (normalized) match is accepted.
        UiaSetValueTool.ValueReadBackMatches("previous line\r\nhello", "hello", allowSuffix: false)
            .Should().BeFalse();
    }

    [Fact]
    public void Wrong_Content_Never_Matches_Even_For_A_Document()
    {
        UiaSetValueTool.ValueReadBackMatches("goodbye world", "hello", allowSuffix: true)
            .Should().BeFalse();
        UiaSetValueTool.ValueReadBackMatches(null, "hello", allowSuffix: true)
            .Should().BeFalse();
        // An empty intended value must not suffix-match everything.
        UiaSetValueTool.ValueReadBackMatches("anything", "", allowSuffix: true)
            .Should().BeFalse();
    }

    [Fact]
    public void Foreground_Confirmed_Immediately_Allows_Typing()
    {
        var hWnd = new IntPtr(0x1234);

        UiaSetValueTool.TryConfirmForeground(hWnd, () => hWnd, attempts: 3, delayMs: 1)
            .Should().BeTrue();
        UiaSetValueTool.ForegroundGuard(hWnd, () => hWnd).Should().BeNull();
    }

    [Fact]
    public void Foreground_That_Cannot_Be_Confirmed_Returns_A_Retryable_Failure_And_Does_Not_Type()
    {
        var hWnd = new IntPtr(0x1234);
        var other = new IntPtr(0x9999);

        UiaSetValueTool.TryConfirmForeground(hWnd, () => other, attempts: 3, delayMs: 1)
            .Should().BeFalse();

        var guard = UiaSetValueTool.ForegroundGuard(hWnd, () => other);
        guard.Should().NotBeNull();
        guard!.Success.Should().BeFalse();
        guard.Effect.Should().NotBeNull();
        guard.Effect!.FailureCode.Should().Be("ForegroundNotConfirmed");
        guard.Effect.Retryable.Should().BeTrue();
    }

    [Fact]
    public void Zero_Handle_Cannot_Be_Confirmed_So_The_Guard_Proceeds()
    {
        // No HWND to confirm (legacy path) — the guard does not block; the same-PID
        // receiver check after typing still gates verification.
        UiaSetValueTool.ForegroundGuard(IntPtr.Zero, () => new IntPtr(0x1)).Should().BeNull();
    }
}
