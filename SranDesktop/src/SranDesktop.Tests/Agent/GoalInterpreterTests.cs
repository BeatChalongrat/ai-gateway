using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>PROMPT 3 Part B: the interpreter must recognize more command phrasings
/// (Thai + English inflections) as actionable so the durable verification runtime
/// isn't silently bypassed, and must capture multi-clause prohibitions whole.</summary>
public class GoalInterpreterTests
{
    private readonly GoalInterpreter _interpreter = new();

    // ── previously-slipping actionable phrasings ────────────────────────────────

    [Theory]
    // The exact Thai example from the fix prompt: "manage the files in this folder,
    // but do not delete anything at all".
    [InlineData("จัดการไฟล์ในโฟลเดอร์นี้ให้เป็นระเบียบ แต่ห้ามลบอะไรทั้งนั้น")]
    [InlineData("จัดเรียงรูปภาพในโฟลเดอร์นี้")]
    [InlineData("ค้นหาไฟล์ที่ใหญ่ที่สุด")]
    [InlineData("สรุปเอกสารนี้")]
    // English inflected verbs the trailing-space term list would miss.
    [InlineData("organizing the downloads folder")]
    [InlineData("please deleted the temp files")]
    [InlineData("cleaning up the project directory")]
    [InlineData("summarizing the report")]
    [InlineData("installs the SDK")]
    public void Recognizes_Additional_Actionable_Phrasings(string request)
    {
        _interpreter.Interpret(request).IsActionable.Should().BeTrue(request);
    }

    [Fact]
    public void Thai_Example_Is_Actionable_And_Captures_The_Whole_Prohibition()
    {
        var result = _interpreter.Interpret(
            "จัดการไฟล์ในโฟลเดอร์นี้ให้เป็นระเบียบ แต่ห้ามลบอะไรทั้งนั้น");

        result.IsActionable.Should().BeTrue();
        result.Goal.Should().NotBeNull();
        result.Goal!.Constraints.Should().ContainSingle()
            .Which.Should().Contain("ห้ามลบอะไรทั้งนั้น");
    }

    [Fact]
    public void Enumerated_Prohibition_Across_Commas_Is_Captured_Whole()
    {
        // The old regex stopped at the first comma, dropping "rename, or move".
        var result = _interpreter.Interpret(
            "organize the folder, but do not delete, rename, or move any files.");

        result.IsActionable.Should().BeTrue();
        result.Goal!.Constraints.Should()
            .Contain(c => c.Contains("delete") && c.Contains("rename") && c.Contains("move"));
    }

    // ── questions must NOT become actionable ────────────────────────────────────

    [Theory]
    [InlineData("What is the capital of France?")]
    [InlineData("How does authentication work?")]
    [InlineData("โมเดลนี้ทำงานยังไง")]
    [InlineData("ราคาเท่าไหร่")]
    public void Plain_Questions_Are_Not_Actionable(string request)
    {
        _interpreter.Interpret(request).IsActionable.Should().BeFalse(request);
    }

    [Fact]
    public void Empty_Request_Is_Not_Actionable()
    {
        _interpreter.Interpret("   ").IsActionable.Should().BeFalse();
    }
}
