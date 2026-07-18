using FluentAssertions;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Humanized tool cards for non-technical users: the one-line
/// argument summary (Humanize), the labeled expanded input (BuildInputDisplay)
/// and the emoji folder listing (MaybePrettifyListing).</summary>
public class ToolCardHumanizeTests
{
    public ToolCardHumanizeTests()
    {
        // Labels come from the locale dictionary — pin EN so assertions don't
        // depend on the OS language of the machine running the tests.
        LocaleService.Instance.Locale = "en";
    }

    // ── Humanize: one-line header summary ────────────────────────────────────

    [Theory]
    [InlineData("list_directory", @"{""path"":""C:\\Users\\Beat\\_zeno_backups""}", "_zeno_backups")]
    [InlineData("read_file", @"{""path"":""C:\\repo\\src\\Program.cs""}", "Program.cs")]
    [InlineData("list_directory", @"{""path"":""C:\\repo\\docs\\""}", "docs")]
    [InlineData("web_search", @"{""query"":""ราคาทองวันนี้""}", "ราคาทองวันนี้")]
    [InlineData("computer_key", @"{""keys"":""Ctrl+Shift+M""}", "Ctrl+Shift+M")]
    [InlineData("computer_click", @"{""x"":960,""y"":668}", "(960, 668)")]
    [InlineData("uia_click", @"{""element_id"":12}", "#12")]
    [InlineData("browser_tabs", @"{""action"":""close"",""tab_id"":3}", "close")]
    [InlineData("computer_screenshot", "{}", "")]
    [InlineData("get_computer_state", "{}", "")]
    public void Humanize_Extracts_The_Salient_Value(string tool, string json, string expected)
    {
        ToolExecutionItemViewModel.Humanize(tool, json).Should().Be(expected);
    }

    [Fact]
    public void Humanize_Joins_Move_Source_And_Destination_As_Leaves()
    {
        ToolExecutionItemViewModel.Humanize("move_file",
                @"{""source"":""C:\\a\\old.txt"",""destination"":""C:\\b\\new.txt""}")
            .Should().Be("old.txt → new.txt");
    }

    [Fact]
    public void Humanize_Combines_Ref_And_Text_For_Browser_Type()
    {
        ToolExecutionItemViewModel.Humanize("browser_type", @"{""ref"":3,""text"":""hello""}")
            .Should().Be("#3 hello");
    }

    [Fact]
    public void Humanize_Truncates_Long_Commands_With_An_Ellipsis()
    {
        var command = new string('x', 100);
        var result = ToolExecutionItemViewModel.Humanize("run_command", $@"{{""command"":""{command}""}}");

        result.Should().HaveLength(71); // 70 + ellipsis
        result.Should().EndWith("…");
    }

    [Fact]
    public void Humanize_Falls_Back_To_Compact_Json_For_Unknown_Tools_And_Bad_Input()
    {
        ToolExecutionItemViewModel.Humanize("mystery_mcp_tool", @"{""a"":1}")
            .Should().Be(@"{""a"":1}");
        // Missing y coordinate → the tuple can't be built → compact fallback.
        ToolExecutionItemViewModel.Humanize("computer_click", @"{""x"":10}")
            .Should().Be(@"{""x"":10}");
        // Invalid JSON never throws.
        ToolExecutionItemViewModel.Humanize("web_search", "not json").Should().Be("not json");
        // Non-object argument payloads stay compact JSON.
        ToolExecutionItemViewModel.Humanize("list_directory", "[1,2]").Should().Be("[1,2]");
        // Compact fallback is capped so the card header can't explode.
        var huge = @"{""blob"":""" + new string('y', 200) + @"""}";
        ToolExecutionItemViewModel.Humanize("mystery_mcp_tool", huge).Length.Should().Be(93); // 90 + "..."
    }

    // ── BuildInputDisplay: expanded "label: value" lines ─────────────────────

    [Fact]
    public void InputDisplay_Uses_Localized_Labels_For_Known_Args()
    {
        ToolExecutionItemViewModel.BuildInputDisplay(@"{""path"":""C:\\x\\y.txt""}")
            .Should().Be(@"Location: C:\x\y.txt");
    }

    [Fact]
    public void InputDisplay_Falls_Back_To_The_Raw_Param_Name()
    {
        ToolExecutionItemViewModel.BuildInputDisplay(@"{""zzz_custom"":""v"",""x"":960}")
            .Should().Be("zzz_custom: v" + Environment.NewLine + "x: 960");
    }

    [Fact]
    public void InputDisplay_Caps_Long_Values()
    {
        var display = ToolExecutionItemViewModel.BuildInputDisplay(
            $@"{{""content"":""{new string('z', 900)}""}}");

        display.Should().StartWith("Content: ");
        display.Length.Should().BeLessThan(600);
        display.Should().EndWith("…");
    }

    [Fact]
    public void InputDisplay_Handles_Empty_And_Non_Object_Payloads()
    {
        ToolExecutionItemViewModel.BuildInputDisplay("{}").Should().Be("—");
        // Non-object → pretty-printed JSON fallback.
        ToolExecutionItemViewModel.BuildInputDisplay("[1,2]").Should().Contain("1");
        ToolExecutionItemViewModel.BuildInputDisplay("not json").Should().Be("not json");
    }

    // ── MaybePrettifyListing: emoji folder listing ───────────────────────────

    [Fact]
    public void Listing_Swaps_D_And_F_Markers_For_Emoji()
    {
        var listing = "D docs\r\nF readme.md (1 KB)\nTotal: 2 entries";

        ToolExecutionItemViewModel.MaybePrettifyListing("list_directory", success: true, listing)
            .Should().Be("📁 docs" + Environment.NewLine
                       + "📄 readme.md (1 KB)" + Environment.NewLine
                       + "Total: 2 entries");
    }

    [Fact]
    public void Listing_Passes_Through_For_Other_Tools_Failures_And_Null()
    {
        const string text = "D looks-like-a-listing";
        ToolExecutionItemViewModel.MaybePrettifyListing("read_file", true, text).Should().Be(text);
        ToolExecutionItemViewModel.MaybePrettifyListing("list_directory", false, text).Should().Be(text);
        ToolExecutionItemViewModel.MaybePrettifyListing("list_directory", true, null).Should().BeNull();
        ToolExecutionItemViewModel.MaybePrettifyListing("list_directory", true, "").Should().Be("");
    }

    [Fact]
    public void Listing_Only_Rewrites_Line_LEADING_Markers()
    {
        // "D "/"F " inside a name must survive; only line starts are markers.
        ToolExecutionItemViewModel.MaybePrettifyListing("list_directory", true, "F My D Drive notes.txt")
            .Should().Be("📄 My D Drive notes.txt");
    }
}
