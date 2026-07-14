using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class ComputerToolTests
{
    // ── Win32Input.ParseKeyCombo ────────────────────────────────────────────

    [Fact]
    public void ParseKeyCombo_Single_Key()
    {
        var (mods, key) = Win32Input.ParseKeyCombo("Enter");
        mods.Should().BeEmpty();
        key.Should().Be(0x0D);
    }

    [Fact]
    public void ParseKeyCombo_Ctrl_C()
    {
        var (mods, key) = Win32Input.ParseKeyCombo("Ctrl+C");
        mods.Should().Equal(new ushort[] { 0xA2 });
        key.Should().Be(0x43); // 'C'
    }

    [Fact]
    public void ParseKeyCombo_Ctrl_Shift_S()
    {
        var (mods, key) = Win32Input.ParseKeyCombo("Ctrl+Shift+S");
        mods.Should().Equal(new ushort[] { 0xA2, 0xA0 });
        key.Should().Be(0x53); // 'S'
    }

    [Theory]
    [InlineData("F1", 0x70)]
    [InlineData("f12", 0x7B)]
    [InlineData("Escape", 0x1B)]
    [InlineData("Tab", 0x09)]
    [InlineData("PageDown", 0x22)]
    [InlineData("7", 0x37)]
    public void ParseKeyCombo_Special_Keys(string combo, int expected)
    {
        var (_, key) = Win32Input.ParseKeyCombo(combo);
        key.Should().Be((ushort)expected);
    }

    [Fact]
    public void ParseKeyCombo_Alt_F4()
    {
        var (mods, key) = Win32Input.ParseKeyCombo("Alt+F4");
        mods.Should().Equal(new ushort[] { 0xA4 });
        key.Should().Be(0x73);
    }

    [Fact]
    public void ParseKeyCombo_Unknown_Key_Throws()
    {
        var act = () => Win32Input.ParseKeyCombo("Bogus+Key");
        act.Should().Throw<ArgumentException>();
    }

    // ── ComputerTypeTool credential guard ───────────────────────────────────

    [Theory]
    [InlineData("sk-proj-abcdef1234567890")]
    [InlineData("my key is gw_live_abc123")]
    [InlineData("ghp_16C7e42F292c6912E7710c838347Ae178B4a")]
    [InlineData("password: hunter2")]
    [InlineData("pwd = s3cret!")]
    [InlineData("4111 1111 1111 1111")]
    [InlineData("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload")]
    public void LooksLikeCredential_Blocks_Secrets(string text)
    {
        ComputerTypeTool.LooksLikeCredential(text, out _).Should().BeTrue(text);
    }

    [Theory]
    [InlineData("hello world")]
    [InlineData("Meeting notes for Q3 planning")]
    [InlineData("The password policy requires rotation")]
    [InlineData("สวัสดีครับ ทดสอบภาษาไทย")]
    public void LooksLikeCredential_Allows_Normal_Text(string text)
    {
        ComputerTypeTool.LooksLikeCredential(text, out _).Should().BeFalse(text);
    }
}
