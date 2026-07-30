using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>T3 PART 1: the injection ladder resolves the real editable child control by
/// window class, so window-message injection (EM_REPLACESEL / WM_SETTEXT) and WM_GETTEXT
/// read-back target Windows 11 Notepad's RichEditD2DPT rather than the top-level frame.</summary>
public sealed class Win32TextInjectionTests
{
    [Theory]
    [InlineData("RichEditD2DPT")]   // Windows 11 Notepad document
    [InlineData("Edit")]            // classic edit box
    [InlineData("RICHEDIT50W")]     // classic RichEdit
    [InlineData("RichEdit20W")]
    [InlineData("WindowsForms10.EDIT.app.0.141b42a_r6_ad1")]
    [InlineData("TextBox")]
    public void Editable_Control_Classes_Are_Recognized(string className)
    {
        Win32TextInjection.IsEditClassName(className).Should().BeTrue();
    }

    [Theory]
    [InlineData("Notepad")]         // the top-level frame, not the editor
    [InlineData("Button")]
    [InlineData("Static")]
    [InlineData("")]
    [InlineData(null)]
    public void Non_Editable_Classes_Are_Rejected(string? className)
    {
        Win32TextInjection.IsEditClassName(className).Should().BeFalse();
    }
}
