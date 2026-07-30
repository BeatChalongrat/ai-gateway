using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Subagents;

/// <summary>
/// A supervisor's failure modes are social rather than technical: letting one
/// child's failure erase its siblings' work, letting a child hand itself more
/// access than the parent had, and — the one that produces confidently wrong
/// answers — quietly picking a winner when two children disagree.
/// </summary>
public sealed class SubagentSupervisorTests
{
    private static readonly DateTimeOffset Now = new(2026, 7, 29, 10, 0, 0, TimeSpan.Zero);

    private static ChildScope ParentScope() => new()
    {
        AllowedTools = ["web_search", "web_fetch", "read_file", "mcp_call"],
        WorkspaceRoots = [@"C:\work"],
        ConnectorIds = ["reports", "docs"],
        AccountKeys = ["acct-1"],
        ReadOnly = true
    };

    // ── Scope: a child cannot widen anything ────────────────────────────────

    [Fact]
    public void A_Child_Cannot_Request_A_Tool_The_Parent_Lacks()
    {
        var request = ParentScope() with { AllowedTools = ["web_search", "run_command"] };

        var create = () => SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "researcher", "find X", request);

        create.Should().Throw<InvalidOperationException>()
            .WithMessage("*cannot be given access the parent run does not hold*");
    }

    [Fact]
    public void A_Child_Cannot_Reach_Outside_The_Parents_Workspace()
    {
        var request = ParentScope() with { WorkspaceRoots = [@"C:\work", @"C:\Users\someone\Documents"] };

        var create = () => SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "reader", "read files", request);

        create.Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void A_Read_Only_Parent_Cannot_Produce_A_Mutating_Child()
    {
        var request = ParentScope() with { ReadOnly = false };

        var create = () => SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "writer", "write things", request);

        create.Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void A_Child_Cannot_Use_A_Connector_Or_Account_The_Parent_Lacks()
    {
        var connector = () => SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "r", "g",
            ParentScope() with { ConnectorIds = ["reports", "billing"] });
        var account = () => SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "r", "g",
            ParentScope() with { AccountKeys = ["acct-1", "acct-admin"] });

        connector.Should().Throw<InvalidOperationException>();
        account.Should().Throw<InvalidOperationException>();
    }

    [Fact]
    public void A_Narrower_Child_Is_Allowed()
    {
        var child = SubagentSupervisor.CreateChild(
            "run-1", 0, ParentScope(), "researcher", "find X",
            ParentScope() with { AllowedTools = ["web_search"], ConnectorIds = [] });

        child.Scope.AllowedTools.Should().ContainSingle();
        child.Depth.Should().Be(1);
        child.ParentRunId.Should().Be("run-1");
    }

    [Fact]
    public void Delegation_Depth_Is_Bounded()
    {
        var create = () => SubagentSupervisor.CreateChild(
            "run-1:child-a", SubagentSupervisor.MaximumDepth, ParentScope(),
            "grandchild", "delegate further", ParentScope());

        create.Should().Throw<InvalidOperationException>()
            .WithMessage("*exceeds the limit*");
    }

    // ── Budgets ─────────────────────────────────────────────────────────────

    [Fact]
    public void A_Child_Stops_At_Its_Tool_Call_Budget()
    {
        var child = Child("researcher") with { Budget = new ChildBudget(MaxToolCalls: 5) };

        var stopped = SubagentSupervisor.ApplySpend(child, new ChildSpend(ToolCalls: 6), Now);

        stopped.State.Should().Be(ChildRunState.BudgetExhausted);
        stopped.StateReason.Should().Contain("tool calls");
    }

    [Fact]
    public void A_Child_Stops_At_Its_Token_Budget()
    {
        var child = Child("researcher") with { Budget = new ChildBudget(MaxTokens: 1000) };

        SubagentSupervisor.ApplySpend(child, new ChildSpend(Tokens: 1001), Now)
            .State.Should().Be(ChildRunState.BudgetExhausted);
    }

    [Fact]
    public void Spending_Within_Budget_Does_Not_Stop_The_Child()
    {
        var child = Child("researcher") with { Budget = new ChildBudget(MaxToolCalls: 5) };

        var running = SubagentSupervisor.ApplySpend(child, new ChildSpend(ToolCalls: 4), Now);

        running.State.Should().Be(ChildRunState.Pending);
        running.Spend.ToolCalls.Should().Be(4);
    }

    [Fact]
    public void A_Budget_Stop_Keeps_The_Work_Already_Done()
    {
        var child = Child("researcher") with
        {
            Budget = new ChildBudget(MaxToolCalls: 2),
            Findings = [Finding("c1", "revenue was 4.2 billion", "web:example.com", 0.8)]
        };

        var stopped = SubagentSupervisor.ApplySpend(child, new ChildSpend(ToolCalls: 3), Now);

        stopped.HasUsableWork.Should().BeTrue();
        SubagentSupervisor.Collect([stopped], Now).MergedFindings.Should().ContainSingle(
            "partial evidence is still evidence");
    }

    // ── Partial failure ─────────────────────────────────────────────────────

    [Fact]
    public void One_Failed_Child_Does_Not_Erase_Its_Siblings()
    {
        var good1 = Succeeded("c1", "revenue was 4.2 billion", "web:alpha.com");
        var good2 = Succeeded("c2", "the filing was published in March", "web:beta.org");
        var bad = Child("c3") with
        {
            State = ChildRunState.Failed,
            StateReason = "the connector was unreachable"
        };

        var result = SubagentSupervisor.Collect([good1, good2, bad], Now);

        result.Succeeded.Should().Be(2);
        result.Failed.Should().Be(1);
        result.PartialSuccess.Should().BeTrue();
        result.MergedFindings.Should().HaveCount(2);
    }

    [Fact]
    public void A_Timed_Out_Child_Contributes_What_It_Found()
    {
        var partial = Child("c1") with
        {
            State = ChildRunState.TimedOut,
            Findings = [Finding("c1", "the report exists", "mcp:reports/list", 0.7)]
        };

        var result = SubagentSupervisor.Collect([partial], Now);

        result.Failed.Should().Be(1);
        result.MergedFindings.Should().ContainSingle();
    }

    [Fact]
    public void A_Cancelled_Child_Leaves_Its_Siblings_Running()
    {
        var running = Child("c1") with { State = ChildRunState.Running };
        var cancelled = SubagentSupervisor.Cancel(Child("c2"), "user cancelled", Now);

        cancelled.State.Should().Be(ChildRunState.Cancelled);
        running.State.Should().Be(ChildRunState.Running);
    }

    [Fact]
    public void Cancelling_A_Finished_Child_Does_Nothing()
    {
        var done = Succeeded("c1", "a fact", "web:x.com");

        SubagentSupervisor.Cancel(done, "too late", Now).State
            .Should().Be(ChildRunState.Succeeded);
    }

    [Fact]
    public void A_Restart_Keeps_Findings_And_Spends_A_Retry()
    {
        var failed = Child("c1") with
        {
            State = ChildRunState.Failed,
            Budget = new ChildBudget(MaxRetries: 2),
            Findings = [Finding("c1", "a partial fact", "web:x.com", 0.6)]
        };

        var restarted = SubagentSupervisor.PrepareRestart(failed, Now);

        restarted.State.Should().Be(ChildRunState.Pending);
        restarted.Spend.Retries.Should().Be(1);
        restarted.Findings.Should().ContainSingle(
            "a restart that throws away completed work makes restarts too expensive to use");
    }

    [Fact]
    public void Restarts_Are_Bounded()
    {
        var exhausted = Child("c1") with
        {
            State = ChildRunState.Failed,
            Budget = new ChildBudget(MaxRetries: 1),
            Spend = new ChildSpend(Retries: 1)
        };

        SubagentSupervisor.PrepareRestart(exhausted, Now).State
            .Should().Be(ChildRunState.BudgetExhausted);
    }

    [Fact]
    public void A_Child_That_Needs_The_User_Escalates_To_The_Parent()
    {
        var asking = Child("c1") with { State = ChildRunState.NeedsUser };

        SubagentSupervisor.Collect([asking, Succeeded("c2", "a fact", "web:x.com")], Now)
            .RequiresUser.Should().BeTrue();
    }

    // ── Deduplication and conflict ──────────────────────────────────────────

    [Fact]
    public void The_Same_Fact_From_Two_Children_Is_Merged_Once_With_Both_Provenances()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:alpha.com");
        var b = Succeeded("c2", "revenue was 4.2 billion", "web:beta.org");

        var result = SubagentSupervisor.Collect([a, b], Now);

        result.MergedFindings.Should().ContainSingle("one fact, not two");
        result.MergedFindings[0].Provenance.Should().Contain("alpha.com").And.Contain("beta.org");
    }

    [Fact]
    public void Two_Children_Reading_One_Source_Do_Not_Corroborate_Each_Other()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:wire.com", sourceId: "s1", confidence: 0.7);
        var b = Succeeded("c2", "revenue was 4.2 billion", "web:wire.com", sourceId: "s1", confidence: 0.7);

        var merged = SubagentSupervisor.Collect([a, b], Now).MergedFindings.Single();

        merged.Confidence.Should().Be(0.7,
            "the same source read twice is not independent confirmation");
    }

    [Fact]
    public void Independent_Children_Agreeing_Raises_Confidence_Slightly()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:alpha.com", sourceId: "s1", confidence: 0.7);
        var b = Succeeded("c2", "revenue was 4.2 billion", "web:beta.org", sourceId: "s2", confidence: 0.7);

        SubagentSupervisor.Collect([a, b], Now).MergedFindings.Single()
            .Confidence.Should().BeGreaterThan(0.7);
    }

    [Fact]
    public void Contradictory_Numbers_Are_Reported_Not_Silently_Resolved()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:alpha.com", confidence: 0.7);
        var b = Succeeded("c2", "revenue was 3.1 billion", "web:beta.org", confidence: 0.7);

        var result = SubagentSupervisor.Collect([a, b], Now);

        result.Conflicts.Should().ContainSingle();
        result.UnresolvedConflicts.Should().ContainSingle(
            "comparable evidence on both sides must not be resolved by picking one");
        result.RequiresUser.Should().BeTrue();
        result.MergedFindings.Should().HaveCount(2, "both claims stay visible");
    }

    [Fact]
    public void Clearly_Stronger_Evidence_Resolves_A_Conflict()
    {
        var weak = Succeeded("c1", "revenue was 3.1 billion", "web:someblog.net", confidence: 0.4);
        var strong = Succeeded("c2", "revenue was 4.2 billion", "filing:sec.gov", confidence: 0.95);

        var conflict = SubagentSupervisor.Collect([weak, strong], Now).Conflicts.Single();

        conflict.Resolution.Should().Be(ConflictResolution.ResolvedByEvidence);
        conflict.Preferred!.Claim.Should().Contain("4.2");
        conflict.Explanation.Should().Contain("sec.gov");
    }

    [Fact]
    public void A_Newer_Observation_Resolves_A_Same_Strength_Conflict()
    {
        var older = Succeeded("c1", "the status is pending", "mcp:x", confidence: 0.8)
            with
        { Findings = [Finding("c1", "the status is pending", "mcp:x", 0.8, observed: Now.AddHours(-5))] };
        var newer = Succeeded("c2", "the status is approved", "mcp:x", confidence: 0.8)
            with
        { Findings = [Finding("c2", "the status is approved", "mcp:x", 0.8, observed: Now)] };

        var conflict = SubagentSupervisor.Collect([older, newer], Now).Conflicts.Single();

        conflict.Resolution.Should().Be(ConflictResolution.ResolvedByRecency);
        conflict.Preferred!.Claim.Should().Contain("approved");
    }

    [Fact]
    public void A_Negation_Counts_As_A_Conflict()
    {
        var yes = Succeeded("c1", "the merger was approved by the regulator", "web:a.com", confidence: 0.7);
        var no = Succeeded("c2", "the merger was not approved by the regulator", "web:b.com", confidence: 0.7);

        SubagentSupervisor.Collect([yes, no], Now).Conflicts.Should().ContainSingle();
    }

    [Fact]
    public void Unrelated_Facts_Are_Not_Treated_As_Conflicting()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:a.com");
        var b = Succeeded("c2", "the head office is in Berlin", "web:b.com");

        SubagentSupervisor.Collect([a, b], Now).Conflicts.Should().BeEmpty();
    }

    [Fact]
    public void The_Summary_Shows_Unresolved_Disagreement()
    {
        var a = Succeeded("c1", "revenue was 4.2 billion", "web:alpha.com", confidence: 0.7);
        var b = Succeeded("c2", "revenue was 3.1 billion", "web:beta.org", confidence: 0.7);

        var summary = SubagentSupervisor.Summarize(SubagentSupervisor.Collect([a, b], Now));

        summary.Should().Contain("Unresolved");
        summary.Should().Contain("4.2").And.Contain("3.1");
        summary.Should().Contain("needs a decision from the user");
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    private static ChildRun Child(string id) => new()
    {
        ChildRunId = id,
        ParentRunId = "run-1",
        Role = "researcher",
        Goal = "find something",
        Scope = ParentScope(),
        Depth = 1
    };

    private static ChildRun Succeeded(
        string id, string claim, string provenance,
        string? sourceId = null, double confidence = 0.8) =>
        Child(id) with
        {
            State = ChildRunState.Succeeded,
            Result = claim,
            Findings = [Finding(id, claim, provenance, confidence, sourceId)],
            CompletedUtc = Now
        };

    private static ChildFinding Finding(
        string childId, string claim, string provenance,
        double confidence, string? sourceId = null, DateTimeOffset? observed = null) =>
        new(childId, claim,
            new string(claim.Where(c => char.IsLetterOrDigit(c) || c == ' ').ToArray())
                .ToLowerInvariant().Trim(),
            provenance, confidence, sourceId, observed);
}
