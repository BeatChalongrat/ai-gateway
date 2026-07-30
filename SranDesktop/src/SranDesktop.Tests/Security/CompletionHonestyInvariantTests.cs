using System.Text.RegularExpressions;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// Source-level guards for what <c>Completed</c> is allowed to mean.
///
/// A behavioural test cannot catch the regression these exist for. The way this
/// defect returns is not a wrong answer from a checked path — it is a new path
/// that never asks the question: another completion site that sets
/// <see cref="AgentTaskStatus.Completed"/> without consulting the failure state,
/// or a well-meaning simplification that collapses the two guards back into one.
/// Both leave every functional test green while the status the user reads goes
/// back to meaning "the loop stopped".
/// </summary>
public sealed class CompletionHonestyInvariantTests
{
    [Fact]
    public void Conversation_Completion_Checks_For_Failure_As_Well_As_For_The_Unknown()
    {
        var source = Runtime();
        var body = Method(source, "CompleteConversationAsync");

        body.Should().Contain("FindUnresolvedSideEffectAsync",
            "a dispatched side effect whose outcome is unknown must still block the close");
        body.Should().Contain("FindFailedUnrecoveredActionAsync",
            "work that definitely failed is resolved, so the unknown check alone lets it "
            + "through — which is exactly how a failed run closed as Completed");
    }

    [Fact]
    public void Nothing_Sets_Completed_Without_Consulting_The_Blocking_Action()
    {
        var body = Method(Runtime(), "CompleteConversationAsync");

        // The assignment has to be conditional on the guard's result. `= Completed;`
        // with no condition on the same statement is the shape of the bug.
        var assignment = Regex.Match(
            body,
            @"Task\.Status\s*=(?!=)\s*(?<value>[^;]+);",
            RegexOptions.Singleline);
        assignment.Success.Should().BeTrue("the method must set a terminal status");
        assignment.Groups["value"].Value.Should().Contain("blocking is null",
            "Completed must be the branch taken only when nothing is outstanding "
            + "and nothing failed unrecovered");
    }

    [Fact]
    public void Recovery_Is_Judged_By_The_Step_Not_By_Counting_Failures()
    {
        var body = Method(Runtime(), "FindFailedUnrecoveredActionAsync");

        body.Should().Contain("TaskStepStatus.Verified",
            "a step that ended verified had its work done, however many attempts failed first");
        body.Should().Contain("outcome.Required",
            "only work the user asked for may block the close");
        body.Should().NotContain("SideEffectClass.ReadOnly",
            "a failed read is still an unanswered question — filtering by side effect "
            + "class here would let every failed observation close as success");
    }

    [Fact]
    public void An_Unclassified_Goal_Still_Requires_Evidence_About_Its_Subject()
    {
        var matcher = File.ReadAllText(SourcePath(
            "SranDesktop.Application", "Services", "SemanticOutcomeRuntime.cs"));

        matcher.Should().Contain("OutcomeKind.Generic => EvidenceMentionsOutcomeSubject",
            "Generic is the fallback for goals the interpreter could not classify, which "
            + "is most non-English phrasings; accepting any capability there means the "
            + "first tool that happens to succeed closes the goal");
        matcher.Should().NotContain("OutcomeKind.Generic => true",
            "restoring the blanket accept is the single edit that reopens this hole");

        // The behaviour itself is proved end to end, against the real runtime and
        // the real plan, by PlanStatusFidelityTests — asserting it a second time
        // here through a test-only accessor would mean adding one to production code.
    }

    private static string Runtime() => File.ReadAllText(SourcePath(
        "SranDesktop.Application", "Services", "DurableTaskRuntime.cs"));

    /// <summary>The body of one method, so a match cannot come from an unrelated
    /// part of a 3000-line file.</summary>
    private static string Method(string source, string name)
    {
        // Anchor on the declaration, not on a call site — the runtime calls
        // these methods as well as defining them.
        var declaration = Regex.Match(
            source,
            $@"^[ \t]*(?:private|public|internal|protected).*\b{Regex.Escape(name)}\(",
            RegexOptions.Multiline);
        declaration.Success.Should().BeTrue($"{name} should be declared");
        var start = declaration.Index;
        var open = source.IndexOf('{', start);
        var depth = 0;
        for (var index = open; index < source.Length; index++)
        {
            if (source[index] == '{') depth++;
            else if (source[index] == '}' && --depth == 0)
                return source[open..(index + 1)];
        }
        throw new InvalidOperationException($"{name} has no closing brace.");
    }

    private static string SourcePath(params string[] parts) =>
        Path.Combine([RepositoryRoot(), "src", .. parts]);

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException("The repository root was not found.");
    }
}
