using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>FIX-C: the consent dialog must show a human-readable, localized
/// summary — a friendly tool name and "label: value" lines — never raw JSON.</summary>
public sealed class ToolConsentViewModelTests
{
    private static ToolConsentRequest Request(string tool, string argsJson) =>
        new("call-1", tool, "desc", ToolCategory.Shell, argsJson, "scope");

    [Fact]
    public void DisplayName_Uses_The_Localized_Dictionary_Entry()
    {
        var vm = new ToolConsentViewModel(Request("run_command", """{"command":"dir"}"""));

        vm.DisplayName.Should().NotBe("run_command", "the raw id is not for humans");
        vm.DisplayName.Should().NotStartWith("tool.name.", "a missing key must not leak");
    }

    [Fact]
    public void DisplayName_Falls_Back_To_The_Raw_Id_For_Unknown_Tools()
    {
        var vm = new ToolConsentViewModel(Request("some_new_tool", "{}"));

        vm.DisplayName.Should().Be("some_new_tool");
    }

    [Fact]
    public void Arguments_Render_As_Label_Value_Lines_Not_Json()
    {
        var vm = new ToolConsentViewModel(Request("run_command",
            """{"command":"npm test","working_directory":"C:\\proj"}"""));

        vm.ArgumentsFormatted.Should().NotContain("{").And.NotContain("\"",
            "raw JSON is unreadable for the person deciding");
        vm.ArgumentsFormatted.Should().Contain("npm test").And.Contain("C:\\proj");
        vm.ArgumentsFormatted.Should().Contain(":", "each line is 'label: value'");
    }

    [Fact]
    public void Long_Values_Are_Truncated_With_A_Marker()
    {
        var longContent = new string('x', 5000);
        var vm = new ToolConsentViewModel(Request("write_file",
            $$"""{"path":"C:\\a.txt","content":"{{longContent}}"}"""));

        vm.ArgumentsFormatted.Length.Should().BeLessThan(1000);
        vm.ArgumentsFormatted.Should().Contain("…");
    }

    [Fact]
    public void Null_Arguments_Are_Skipped_And_Empty_Objects_Show_A_Placeholder()
    {
        var vm = new ToolConsentViewModel(Request("computer_screenshot", "{}"));

        vm.ArgumentsFormatted.Should().Be("—");
    }

    [Fact]
    public void Malformed_Json_Falls_Back_To_The_Raw_Text()
    {
        var vm = new ToolConsentViewModel(Request("run_command", "not-json"));

        vm.ArgumentsFormatted.Should().Contain("not-json");
    }
}
