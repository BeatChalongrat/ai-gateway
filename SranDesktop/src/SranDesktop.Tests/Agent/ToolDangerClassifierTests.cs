using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase C PART 3: consent danger is derived from capability, covering
/// the high-blast-radius tools the old hardcoded list missed.</summary>
public class ToolDangerClassifierTests
{
    [Theory]
    // shell + code
    [InlineData("run_command", ToolCategory.Shell)]
    [InlineData("run_python", ToolCategory.Network)]
    // ALL computer use (was entirely missed)
    [InlineData("computer_click", ToolCategory.Screen)]
    [InlineData("computer_type", ToolCategory.Screen)]
    [InlineData("computer_key", ToolCategory.Screen)]
    [InlineData("computer_scroll", ToolCategory.Screen)]
    [InlineData("computer_drag", ToolCategory.Screen)]
    [InlineData("computer_screenshot", ToolCategory.Screen)]
    [InlineData("open_application", ToolCategory.Screen)]
    // file WRITES incl. the new Phase A tools
    [InlineData("write_file", ToolCategory.FileSystem)]
    [InlineData("edit_file", ToolCategory.FileSystem)]
    [InlineData("create_directory", ToolCategory.FileSystem)]
    [InlineData("move_file", ToolCategory.FileSystem)]
    [InlineData("write_clipboard", ToolCategory.Clipboard)]
    [InlineData("read_clipboard", ToolCategory.Clipboard)]
    [InlineData("open_url", ToolCategory.Browser)]
    // a remote MCP tool (arbitrary Network name)
    [InlineData("acme_jira_create_issue", ToolCategory.Network)]
    public void High_Blast_Radius_Tools_Are_Dangerous(string name, ToolCategory category)
    {
        var d = ToolDangerClassifier.Classify(name, category);
        d.IsDangerous.Should().BeTrue($"{name} can affect the machine or the outside world");
        d.ReasonKey.Should().NotBeEmpty();
    }

    [Theory]
    [InlineData("read_file", ToolCategory.FileSystem)]
    [InlineData("list_directory", ToolCategory.FileSystem)]
    [InlineData("search_files", ToolCategory.FileSystem)]
    [InlineData("web_search", ToolCategory.Network)]
    [InlineData("update_plan", ToolCategory.Network)]
    [InlineData("remember", ToolCategory.Network)]
    [InlineData("use_skill", ToolCategory.Network)]
    public void Read_And_Meta_Tools_Stay_Calm(string name, ToolCategory category)
    {
        ToolDangerClassifier.Classify(name, category).IsDangerous.Should().BeFalse();
    }

    [Fact]
    public void New_Side_Effecting_Tool_Is_Dangerous_By_Default()
    {
        // A hypothetical future tool in a side-effecting category, not safe-listed.
        ToolDangerClassifier.Classify("delete_everything", ToolCategory.FileSystem)
            .IsDangerous.Should().BeTrue();
        ToolDangerClassifier.Classify("mystery_tool", (ToolCategory)999)
            .IsDangerous.Should().BeTrue(); // unknown category → fail-safe
    }

    [Fact]
    public void Reason_Keys_Are_Capability_Specific()
    {
        ToolDangerClassifier.Classify("run_command", ToolCategory.Shell).ReasonKey.Should().Be("danger.shell");
        ToolDangerClassifier.Classify("computer_click", ToolCategory.Screen).ReasonKey.Should().Be("danger.computerUse");
        ToolDangerClassifier.Classify("run_python", ToolCategory.Network).ReasonKey.Should().Be("danger.code");
        ToolDangerClassifier.Classify("acme_remote", ToolCategory.Network).ReasonKey.Should().Be("danger.connector");
    }
}
