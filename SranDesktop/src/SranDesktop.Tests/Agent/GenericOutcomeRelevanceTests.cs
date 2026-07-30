using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Regression cover for the false success observed live on 2026-07-30.
///
/// <para>The user asked, in Thai, "ดูใน docker ตอนนี้ที่ว่ามีอะไรรันอยู่". Three
/// steps failed — <c>docker ps</c> twice and a skill call — one unrelated step
/// succeeded ("check open windows"), and the run finished as <b>Completed</b>
/// with the goal struck through and the message "Runtime verification: The
/// persisted Definition of Done is satisfied by verified evidence."</para>
///
/// <para>A Thai goal compiles to <see cref="OutcomeKind.Generic"/> because
/// <c>InferKind</c> matches English keywords only, and Generic is the weakest
/// verification path. These tests pin the two holes that let the unrelated step
/// close the goal, and the counter-case that must keep working.</para>
/// </summary>
public sealed class GenericOutcomeRelevanceTests
{
    private const string ThaiDockerGoal = "ดูใน docker ตอนนี้ที่ว่ามีอะไรรันอยู่";

    /// <summary>
    /// The environment-dependent hole: the relevance check searched the content
    /// blob, which appends the tool's own output. "check open windows" listed the
    /// user's open windows and one of them was <i>Docker Desktop</i>, so the token
    /// "docker" appeared and the goal was marked satisfied — verified by the fact
    /// that the user happened to have the GUI open. Structured evidence, which is
    /// what the tool actually observed, says nothing about docker.
    /// </summary>
    [Fact]
    public void Window_List_That_Merely_Mentions_Docker_Does_Not_Verify_A_Docker_Goal()
    {
        var (outcome, step, action) = Correlated("get_computer_state");

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified(
                // Free-text content — the window titles. This is the blob that
                // used to be searched, and why closing Docker Desktop would have
                // changed the verdict on an otherwise identical run.
                "Open windows: Docker Desktop, Visual Studio Code, SRAN Chat",
                ["screen_capture_id=cap-1", "capture_scope=primary_display"]),
            true);

        result.Matched.Should().BeFalse(
            "a window list is not evidence about what docker is running, and "
            + "whether the verdict flips must not depend on which GUI the user "
            + "left open");
        result.FailureCode.Should().Be("EffectOutcomeMismatch");
    }

    /// <summary>
    /// The scoping hole: the rule was gated on <c>AttemptCount &gt; 1</c> and an
    /// uncleared failure code, which only covers "a capability that did not do
    /// the failed work cannot be credited with having RECOVERED it". The live
    /// defect was not a recovery — the unrelated observation ran as its own
    /// first-attempt step, so the gate returned early and never checked.
    /// </summary>
    [Fact]
    public void An_Unrelated_First_Attempt_Observation_Does_Not_Verify()
    {
        var (outcome, step, action) = Correlated("get_computer_state");
        step.AttemptCount = 1;
        step.FailureCode = null;

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("windows enumerated",
                ["browser_tabs_observed=0", "screen_capture_id=cap-2"]),
            true);

        result.Matched.Should().BeFalse(
            "relevance is a property of the evidence, not of how many times the "
            + "step had already failed");
    }

    /// <summary>
    /// The counter-case, and the reason this is a relevance rule rather than a
    /// capability rule. Tightening Generic by capability was measured to break
    /// five runtime tests, because the unclassified bucket holds action intents
    /// as well as observation intents. Relevance admits any tool that actually
    /// did the work.
    /// </summary>
    [Fact]
    public void Evidence_That_Is_Actually_About_Docker_Still_Verifies()
    {
        var (outcome, step, action) = Correlated("run_command");

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("CONTAINER ID   IMAGE   STATUS",
                ["process_exit_code=0", "docker_containers_observed=24"]),
            true);

        result.Matched.Should().BeTrue(
            "the capability that answered the question must not be blocked by a "
            + "rule aimed at the one that answered a different question");
    }

    /// <summary>An outcome with no content words of its own cannot be judged for
    /// relevance, so it must not be rejected on that basis — failing closed here
    /// would block every goal whose phrasing is all stop-words.</summary>
    [Fact]
    public void An_Outcome_With_No_Subject_Words_Is_Not_Rejected_For_Relevance()
    {
        var (outcome, step, action) = Correlated("get_computer_state");
        outcome = outcome with { Description = "ดู", IntendedState = "ดู" };

        var result = new SemanticOutcomeMatcher().Match(
            outcome, step, action,
            ToolResult.Verified("state", ["screen_capture_id=cap-3"]),
            true);

        result.Matched.Should().BeTrue();
    }

    private static (TaskOutcome Outcome, TaskStep Step, TaskAction Action) Correlated(
        string toolName)
    {
        var outcome = new TaskOutcome
        {
            OutcomeId = "outcome",
            TaskId = "task",
            Description = ThaiDockerGoal,
            IntendedState = ThaiDockerGoal,
            Kind = OutcomeKind.Generic,
            SourceGoalVersion = 1
        };
        var step = new TaskStep
        {
            StepId = "step",
            MilestoneId = "milestone",
            TaskId = "task",
            OutcomeId = outcome.OutcomeId,
            Description = outcome.Description,
            IntendedOutcome = outcome.IntendedState,
            VerificationPlan = "independent evidence",
            IdempotencyPolicy = "reconcile",
            RetryPolicy = "bounded"
        };
        var action = new TaskAction
        {
            ActionId = "action",
            TaskId = "task",
            PlanId = "plan",
            MilestoneId = "milestone",
            StepId = step.StepId,
            OutcomeId = outcome.OutcomeId,
            ToolCallId = "call",
            ToolName = toolName,
            ArgumentsFingerprint = "hash",
            IdempotencyKey = "key",
            CreatedAtUtc = DateTimeOffset.UtcNow,
            UpdatedAtUtc = DateTimeOffset.UtcNow
        };
        return (outcome, step, action);
    }
}
