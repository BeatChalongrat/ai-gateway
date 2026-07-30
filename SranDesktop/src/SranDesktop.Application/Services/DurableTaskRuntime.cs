using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

public sealed class DurableTaskRuntime : IDurableTaskRuntime
{
    public const string ContextMarker = "[durable_task_invariants]";
    private readonly IAgentTaskStore _store;
    private readonly GoalInterpreter _goalInterpreter;
    private readonly CapabilityRouter _router;
    private readonly OutcomeCompiler _outcomeCompiler;
    private readonly PlanCoverageValidator _coverageValidator;
    private readonly SemanticOutcomeMatcher _outcomeMatcher;
    private readonly GoalRelevanceGate _goalRelevance;
    private readonly RuntimeStabilizationOptions _stabilization;
    private readonly ISkillRegistry _skillRegistry;
    private readonly ISkillResolver _skillResolver;
    private readonly ICapabilityInventory _capabilityInventory;
    private readonly IEnvironmentObserver _environmentObserver;
    private readonly ISkillVerifier _skillVerifier;
    private readonly ISkillExecutor _skillExecutor;
    private readonly ICompensationCoordinator _compensationCoordinator;
    private int _capabilityHealthLoaded;
    private long _tasksCreated;
    private long _checkpointsSaved;
    private long _actionsDispatched;
    private long _actionsVerified;
    private long _duplicatesPrevented;
    private long _falseSuccessPrevented;
    private long _resumeReconciliations;
    private long _planRevisions;

    public DurableTaskRuntime(
        IAgentTaskStore store,
        GoalInterpreter goalInterpreter,
        CapabilityRouter router,
        RealWorldSkillRuntime? realWorldRuntime = null,
        OutcomeCompiler? outcomeCompiler = null,
        PlanCoverageValidator? coverageValidator = null,
        SemanticOutcomeMatcher? outcomeMatcher = null,
        GoalRelevanceGate? goalRelevance = null,
        RuntimeStabilizationOptions? stabilization = null)
    {
        var realWorld = realWorldRuntime ?? new RealWorldSkillRuntime();
        _store = store;
        _goalInterpreter = goalInterpreter;
        _router = router;
        _outcomeCompiler = outcomeCompiler ?? new OutcomeCompiler();
        _coverageValidator = coverageValidator ?? new PlanCoverageValidator();
        _outcomeMatcher = outcomeMatcher ?? new SemanticOutcomeMatcher();
        _goalRelevance = goalRelevance ?? new GoalRelevanceGate();
        _stabilization = stabilization ?? new RuntimeStabilizationOptions();
        _skillRegistry = realWorld;
        _skillResolver = realWorld;
        _capabilityInventory = realWorld;
        _environmentObserver = realWorld;
        _skillVerifier = realWorld;
        _skillExecutor = realWorld;
        _compensationCoordinator = realWorld;
    }

    public async Task<DurableTurnContext> CreateRunAsync(
        DurableRunRequest request,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (string.IsNullOrWhiteSpace(request.ConversationId)
            || string.IsNullOrWhiteSpace(request.OwnerScope)
            || string.IsNullOrWhiteSpace(request.UserIntent))
            throw new ArgumentException("Conversation, owner scope, and user intent are required.");
        if (request.RecursionDepth < 0 || request.RecursionDepth > 3)
            throw new InvalidOperationException("The durable child-run recursion limit was exceeded.");
        if (request.ChildRunBudget is < 0 or > 32)
            throw new InvalidOperationException("The durable child-run budget is invalid.");

        await EnsureCapabilityHealthLoadedAsync(ct);
        if (await _store.GetActiveByConversationAsync(request.ConversationId, ct) is { } active)
        {
            if (!string.Equals(active.Task.OwnerScope, request.OwnerScope, StringComparison.Ordinal))
                throw new UnauthorizedAccessException("The active run belongs to another owner scope.");
            return RefreshContext(active);
        }

        var interpretation = _goalInterpreter.Interpret(
            request.UserIntent,
            hasPriorContext: false);
        var goal = interpretation.Goal ?? SynthesizeGoal(request.UserIntent);
        var mode = interpretation.IsActionable
            ? interpretation.Mode
            : AgentTaskMode.Conversation;
        var taskId = Id("task");
        var skillResolution = _skillResolver.Resolve(goal, taskId, worldState: null);
        var snapshot = CreateInitialSnapshot(
            request.ConversationId,
            request.OwnerScope,
            goal,
            mode,
            taskId,
            skillResolution.Skill,
            request);
        // Skill ambiguity is about a goal expressed in words. A caller that has
        // already named the operation and its arguments is not asking us to pick
        // between skills, and holding it for clarification would stop work with a
        // question that has no answer.
        var routable = mode != AgentTaskMode.Conversation && !request.OperationIsFullySpecified;
        if (routable && skillResolution.Ambiguous && interpretation.ClarificationQuestion is null)
            snapshot.Goal.MissingInformation.Add(
                "The requested outcome matches multiple runtime skills without a safe score gap. " +
                $"Candidates: {string.Join(", ", skillResolution.CandidateSkillIds)}.");
        if (routable
            && (interpretation.ClarificationQuestion is not null || skillResolution.Ambiguous))
            snapshot.Task.Status = AgentTaskStatus.WaitingForClarification;

        await _store.CreateAsync(
            snapshot.Goal,
            snapshot.Task,
            snapshot.Plan,
            snapshot.Checkpoint!,
            ct);
        Interlocked.Increment(ref _tasksCreated);
        Interlocked.Increment(ref _checkpointsSaved);
        await AppendEventAsync(snapshot, "RunCreated", null,
            new
            {
                request.TriggerType,
                request.ParentRunId,
                request.RecursionDepth,
                request.ChildRunBudget
            }, ct);
        await AppendEventAsync(snapshot, "TaskCreated", null,
            new { snapshot.Task.Mode, snapshot.Task.Status, request.TriggerType }, ct);
        await AppendEventAsync(snapshot, "GoalInterpreted", null,
            new
            {
                snapshot.Goal.NormalizedGoal,
                snapshot.Goal.Constraints,
                snapshot.Goal.MissingInformation
            }, ct);
        await AppendEventAsync(snapshot, "PlanCreated", null,
            new
            {
                snapshot.Plan.Version,
                milestones = snapshot.Plan.Milestones.Count,
                steps = snapshot.Plan.Milestones.Sum(item => item.Steps.Count)
            }, ct);
        await AppendEventAsync(snapshot, "SkillResolved", null,
            new
            {
                skillResolution.Skill.SkillId,
                skillResolution.Skill.Version,
                skillResolution.Score,
                skillResolution.ScoreGap,
                skillResolution.Ambiguous,
                skillResolution.CandidateSkillIds
            }, ct);
        if (snapshot.Task.Status == AgentTaskStatus.WaitingForClarification)
            await AppendEventAsync(snapshot, "ClarificationRequested", null,
                new
                {
                    reason = interpretation.ClarificationQuestion
                             ?? "Ambiguous runtime skill selection.",
                    skillResolution.CandidateSkillIds
                }, ct);
        await SaveMetricAsync(snapshot, "skill_resolution_score",
            skillResolution.Score,
            new
            {
                skillResolution.Skill.SkillId,
                skillResolution.ScoreGap,
                skillResolution.Ambiguous
            }, ct);

        var facts = await _environmentObserver.ObserveAsync(
            snapshot.Goal,
            skillResolution.Skill,
            ct);
        if (facts.Count > 0)
        {
            var world = CreateEnvironmentWorldState(snapshot, facts);
            await _store.SaveWorldStateAsync(world, ct);
            snapshot = snapshot with { WorldState = world };
            await AppendEventAsync(snapshot, "ObservationCreated", null,
                new
                {
                    source = world.Source,
                    trust = ObservationTrust.TrustedConfiguration,
                    world.Version,
                    fact_count = facts.Count
                }, ct);
        }
        return RefreshContext(snapshot);
    }

    public async Task<DurableTurnContext> CreateChildRunAsync(
        string parentRunId,
        DurableRunRequest request,
        CancellationToken ct = default)
    {
        var parent = await RequireSnapshotAsync(parentRunId, ct);
        if (parent.Task.RecursionDepth >= 3)
            throw new InvalidOperationException("The durable child-run recursion limit was exceeded.");
        if (await _store.CountChildrenAsync(parentRunId, ct) >= parent.Task.ChildRunBudget)
            throw new InvalidOperationException("The durable child-run budget was exhausted.");

        var inherited = request with
        {
            TriggerType = AgentTriggerType.Subagent,
            ParentRunId = parentRunId,
            RecursionDepth = parent.Task.RecursionDepth + 1,
            ChildRunBudget = Math.Min(request.ChildRunBudget, parent.Task.ChildRunBudget),
            SecurityContextJson = parent.Task.SecurityContextJson,
            PermissionContextJson = parent.Task.PermissionContextJson,
            WorkspaceContextJson = parent.Task.WorkspaceContextJson,
            ModelContextJson = parent.Task.ModelContextJson
        };
        var child = await CreateRunAsync(inherited, ct);
        await AppendEventAsync(parent, "ChildRunCreated", null,
            new
            {
                child_run_id = child.TaskId,
                child_conversation_id = child.ConversationId,
                inherited.RecursionDepth
            }, ct);
        return child;
    }

    public async Task<DurableTurnContext?> StartOrResumeAsync(
        string conversationId,
        IReadOnlyList<GatewayMessage> messages,
        string ownerScope,
        CancellationToken ct = default)
    {
        await EnsureCapabilityHealthLoadedAsync(ct);
        var existing = await _store.GetActiveByConversationAsync(conversationId, ct);
        if (existing is not null)
        {
            if (!string.Equals(existing.Task.OwnerScope, ownerScope, StringComparison.Ordinal))
                throw new UnauthorizedAccessException("The durable task belongs to a different owner scope.");

            existing = await HydrateLegacyOutcomeCoverageAsync(existing, ct);

            if (string.IsNullOrWhiteSpace(existing.Task.SkillId))
            {
                var resolution = _skillResolver.Resolve(
                    existing.Goal, existing.Task.TaskId, existing.WorldState);
                var expectedVersion = existing.Task.Version;
                existing.Task.SkillId = resolution.Skill.SkillId;
                existing.Task.SkillVersion = resolution.Skill.Version;
                existing.Task.SkillExecutionId = $"skill_execution_{Guid.NewGuid():N}";
                if (!await _store.UpdateTaskAsync(existing.Task, expectedVersion, ct))
                    throw new InvalidOperationException(
                        "Task changed concurrently while resolving its runtime skill.");
                existing = await _store.GetAsync(existing.Task.TaskId, ct) ?? existing;
                await AppendEventAsync(existing, "SkillResolved", null,
                    new
                    {
                        resolution.Skill.SkillId,
                        resolution.Skill.Version,
                        resolution.Score,
                        resolution.ScoreGap,
                        resolution.Ambiguous,
                        resolution.CandidateSkillIds
                    }, ct);
            }

            existing = await ResolveClarificationAsync(existing, messages, ct);
            var reconciliation = existing.Checkpoint is { PendingActionId: not null }
                                 || existing.Checkpoint?.ResumeSafetyStatus
                                    == ResumeSafetyStatus.ReconciliationRequired;
            if (existing.Checkpoint is not null
                && !CheckpointIntegrity.IsValid(existing.Checkpoint))
            {
                var expected = existing.Task.Version;
                existing.Task.Status = AgentTaskStatus.Failed;
                existing.Task.FailureReason = "The latest checkpoint failed its integrity check.";
                await _store.UpdateTaskAsync(existing.Task, expected, ct);
                return new DurableTurnContext(existing.Task.TaskId, conversationId,
                    existing.Task.Mode, BuildPinnedContext(existing), true);
            }

            if (reconciliation)
            {
                Interlocked.Increment(ref _resumeReconciliations);
                var expected = existing.Task.Version;
                existing.Task.Status = AgentTaskStatus.Recovering;
                if (await _store.UpdateTaskAsync(existing.Task, expected, ct))
                    existing = await _store.GetAsync(existing.Task.TaskId, ct) ?? existing;
            }

            await AppendEventAsync(existing, "TaskResumed", null,
                new { reconciliation_required = reconciliation }, ct);
            return new DurableTurnContext(existing.Task.TaskId, conversationId,
                existing.Task.Mode, BuildPinnedContext(existing), reconciliation);
        }

        var userMessages = messages
            .Where(m => string.Equals(m.Role, "user", StringComparison.Ordinal)
                        && m.Content is string)
            .ToList();
        var request = userMessages.LastOrDefault()?.Content as string;
        if (string.IsNullOrWhiteSpace(request)) return null;

        return await CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Chat,
            request,
            ownerScope,
            SecurityContextJson: JsonSerializer.Serialize(new
            {
                owner_scope = ownerScope,
                source = "interactive_chat"
            }),
            ModelContextJson: JsonSerializer.Serialize(new
            {
                message_count = messages.Count
            })), ct);
    }

    public async Task<DurableTurnContext> ApplyPlanUpdateAsync(
        DurableTurnContext context,
        string argumentsJson,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var proposed = PlanParser.Parse(argumentsJson);
        if (proposed.Count == 0) return RefreshContext(snapshot);

        var oldPlan = snapshot.Plan;
        var oldSteps = oldPlan.Milestones.SelectMany(m => m.Steps)
            .ToDictionary(s => s.Description, StringComparer.OrdinalIgnoreCase);
        var planId = Id("plan");
        var milestoneId = Id("milestone");
        var steps = new List<TaskStep>();
        string? previousId = null;
        foreach (var item in proposed)
        {
            oldSteps.TryGetValue(item.Step, out var prior);
            var matchingOutcomes = oldPlan.Outcomes
                .Where(outcome => PlanCoverageValidator.SemanticallyMatches(outcome, item.Step))
                .ToList();
            var outcome = matchingOutcomes.Count == 1
                ? matchingOutcomes[0]
                : oldPlan.Outcomes.Count == 1
                    ? oldPlan.Outcomes[0]
                    : null;
            var stepId = prior?.StepId ?? Id("step");
            var verified = prior?.Status == TaskStepStatus.Verified;
            var step = new TaskStep
            {
                StepId = stepId,
                OutcomeId = outcome?.OutcomeId,
                MilestoneId = milestoneId,
                TaskId = snapshot.Task.TaskId,
                Description = item.Step,
                IntendedOutcome = outcome?.IntendedState ?? item.Step,
                Dependencies = previousId is null ? [] : [previousId],
                PreferredCapabilities = outcome is null
                    ? CapabilityRouter.SuggestCapabilities(item.Step)
                    : oldPlan.Milestones.SelectMany(milestone => milestone.Steps)
                        .FirstOrDefault(existing => existing.OutcomeId == outcome.OutcomeId)
                        ?.PreferredCapabilities.ToList()
                        ?? CapabilityRouter.SuggestCapabilities(item.Step),
                ExpectedEffects = [outcome?.IntendedState ?? $"Verified outcome: {item.Step}"],
                VerificationPlan = outcome is null
                    ? "Use independent structured evidence; model status is not evidence."
                    : string.Join("; ", outcome.VerificationRequirements),
                RiskLevel = AgentSafetyPolicy.InferRisk(item.Step),
                SideEffectClass = AgentSafetyPolicy.ClassifyText(item.Step),
                IdempotencyPolicy = "Reconcile prior outcome before repeating a side effect.",
                RetryPolicy = "Maximum five attempts with a fresh observation after state failures.",
                RollbackOrCompensation = prior?.RollbackOrCompensation,
                Status = verified ? TaskStepStatus.Verified : TaskStepStatus.Pending,
                AttemptCount = prior?.AttemptCount ?? 0,
                EvidenceIds = prior?.EvidenceIds ?? []
            };
            steps.Add(step);
            previousId = stepId;
        }
        PromoteReadySteps(steps);

        var outcomes = oldPlan.Outcomes.Select(CloneOutcome).ToList();
        var coverage = _coverageValidator.Validate(
            snapshot.Goal, oldPlan, outcomes, steps, snapshot.Goal.Constraints);
        if (!coverage.Accepted)
        {
            await AppendEventAsync(snapshot, "PlanProposalRejected", null,
                new
                {
                    coverage.FailureCode,
                    coverage.Reason,
                    coverage.MissingOutcomeIds,
                    proposed_steps = proposed.Select(item => item.Step)
                }, ct);
            return RefreshContext(snapshot);
        }

        var plan = new TaskPlan
        {
            PlanId = planId,
            TaskId = snapshot.Task.TaskId,
            Version = oldPlan.Version + 1,
            GoalVersion = snapshot.Goal.Version,
            PlanningReason = "Model plan proposal validated by the durable runtime.",
            CreatedBy = "model-proposal/runtime-validated",
            CreatedAtUtc = DateTimeOffset.UtcNow,
            SupersedesPlanId = oldPlan.PlanId,
            Constraints = snapshot.Goal.Constraints.ToList(),
            Outcomes = outcomes,
            Milestones =
            [
                new TaskMilestone
                {
                    MilestoneId = milestoneId,
                    TaskId = snapshot.Task.TaskId,
                    PlanId = planId,
                    Title = "Complete the verified task outcome",
                    Objective = snapshot.Goal.DesiredOutcome,
                    DefinitionOfDone = snapshot.Goal.DefinitionOfDone
                        .Select(CloneDefinitionOfDone).ToList(),
                    Status = MilestoneStatus.Ready,
                    Sequence = 1,
                    Steps = steps
                }
            ]
        };
        oldPlan.Status = PlanStatus.Superseded;
        await _store.SavePlanAsync(oldPlan, ct);
        await _store.SavePlanAsync(plan, ct);

        var expectedVersion = snapshot.Task.Version;
        snapshot.Task.CurrentPlanVersion = plan.Version;
        snapshot.Task.CurrentMilestoneId = milestoneId;
        snapshot.Task.CurrentStepId = steps.FirstOrDefault(s => s.Status != TaskStepStatus.Verified)?.StepId;
        snapshot.Task.Status = snapshot.Task.CurrentStepId is null
            ? AgentTaskStatus.VerificationPending
            : AgentTaskStatus.Ready;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
            throw new InvalidOperationException("Task changed concurrently while revising its plan.");
        snapshot = (await _store.GetAsync(snapshot.Task.TaskId, ct))!;
        await SaveCheckpointAsync(snapshot, null, ResumeSafetyStatus.Safe, ct);
        Interlocked.Increment(ref _planRevisions);
        await AppendEventAsync(snapshot, "PlanRevised", null,
            new
            {
                version = plan.Version,
                supersedes = oldPlan.PlanId,
                outcome_coverage = outcomes.Count,
                coverage.Reason
            }, ct);
        return RefreshContext(snapshot);
    }

    public async Task<DurableActionPreparation> PrepareActionAsync(
        DurableTurnContext context,
        string toolCallId,
        string toolName,
        ToolCategory category,
        string argumentsJson,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        if (snapshot.Task.Status == AgentTaskStatus.WaitingForClarification)
            return Block("MissingInformation", "The task requires clarification before execution.");
        if (snapshot.Task.Status is AgentTaskStatus.Failed or AgentTaskStatus.Cancelled
            or AgentTaskStatus.Abandoned or AgentTaskStatus.Completed)
            return Block("TaskNotExecutable", $"Task status is {snapshot.Task.Status}.");

        var step = SelectNextStep(snapshot.Plan);
        if (step is null)
            return Block("NoReadyStep", "No dependency-complete step is ready to execute.");
        if (step.AttemptCount >= snapshot.Task.RetryBudget)
            return Block("RetryBudgetExceeded", "The step retry budget is exhausted.");
        if (snapshot.Plan.Milestones.SelectMany(m => m.Steps).Sum(s => s.AttemptCount)
            >= snapshot.Task.ActionBudget)
            return Block("ActionBudgetExceeded", "The task action budget is exhausted.");

        var skill = snapshot.Task.SkillId is null
            ? null
            : _skillRegistry.GetSkill(snapshot.Task.SkillId);
        if (skill is null)
            return Block("SkillUnavailable",
                "The persisted task skill is not registered in this runtime.");
        var milestone = snapshot.Plan.Milestones.Single(item =>
            item.MilestoneId == step.MilestoneId);
        var outcome = snapshot.Plan.Outcomes.FirstOrDefault(item =>
            item.OutcomeId == step.OutcomeId);
        if (outcome is null)
            return Block("GoalCoverageFailure",
                "The current step is not linked to an authoritative outcome.");

        // Anti-flail 3 — never let the agent close/quit the target app (Alt+F4 and
        // friends) while the task is still unfinished; that throws away all progress.
        // A hard safety block, ahead of relevance.
        if (IsDestructiveWindowAction(toolName, argumentsJson))
            return Block("DestructiveActionBlocked",
                "Closing or quitting the application is not allowed while the task is " +
                "unfinished — it would discard the work in progress. Continue the task instead.",
                step.SideEffectClass);

        var relevance = _goalRelevance.Evaluate(
            snapshot.Goal, outcome, step, toolName, argumentsJson);
        if (!relevance.Allowed)
        {
            await AppendEventAsync(snapshot, "ActionRejected", null,
                new
                {
                    relevance.FailureCode,
                    relevance.Reason,
                    outcome.OutcomeId,
                    requested_capability = toolName,
                    pre_dispatch = true
                }, ct);
            return Block(relevance.FailureCode ?? "GoalIrrelevantAction",
                relevance.Reason, step.SideEffectClass,
                AgentSafetyPolicy.RequiresInvariantApproval(step.SideEffectClass));
        }

        // Anti-flail 1 — don't reopen an app that is already open and verified. Placed
        // AFTER relevance so an irrelevant open (e.g. on a calculate/observe outcome)
        // is still rejected as GoalIrrelevantAction; this specifically catches the
        // re-open churn on enabling outcomes (TextValue/FileState) where A1 makes
        // open_application relevant — a weak model firing open_application repeatedly
        // instead of moving on to type/save.
        if (string.Equals(toolName, "open_application", StringComparison.Ordinal))
        {
            var requestedApp = JsonString(argumentsJson, "application");
            var alreadyReady = snapshot.Plan.Outcomes.Any(item =>
                item.Kind == OutcomeKind.ApplicationReady
                && item.Status == OutcomeStatus.Verified
                && (requestedApp is null
                    || string.Equals(item.ExpectedApplication, requestedApp,
                        StringComparison.OrdinalIgnoreCase)));
            if (alreadyReady)
                return Block("ApplicationAlreadyReady",
                    $"{requestedApp ?? "The application"} is already open and verified — do not " +
                    "reopen it. Move on: read_screen_elements, then uia_set_value on the editor.",
                    step.SideEffectClass);
        }

        // Anti-churn: a capability that has already failed the per-step limit must not
        // be dispatched again on this step — force a switch of approach (and let the
        // reason flow back as a tool result so the model, plus the R1 nudge, redirect
        // instead of looping the same failing tool). Observation + uia_set_value stay
        // dispatchable so re-observing and the verified text path are always possible.
        var stepActions = await _store.GetActionsForStepAsync(
            snapshot.Task.TaskId, step.StepId, ct);
        if (IsCapabilityExhaustedForStep(toolName, stepActions))
        {
            await AppendEventAsync(snapshot, "CapabilityExhausted", null,
                new
                {
                    failure_code = "CapabilityExhaustedForStep",
                    requested_capability = toolName,
                    outcome.OutcomeId,
                    failure_limit = CapabilityFailureLimitPerStep
                }, ct);
            return Block("CapabilityExhaustedForStep",
                $"'{toolName}' has already failed {CapabilityFailureLimitPerStep} times on this " +
                $"step; do not repeat it — {SwitchSuggestion(outcome, toolName)}",
                step.SideEffectClass,
                AgentSafetyPolicy.RequiresInvariantApproval(step.SideEffectClass));
        }

        // Anti-flail 2 — aggregate typing guard covering ALL typing capabilities,
        // uia_set_value included (unlike the per-capability rule above, which exempts
        // it). Once typing has failed the aggregate limit on this step, block further
        // typing (observation stays open so the model can re-observe); with the R1
        // nudge ceiling this converges to a clear paused/incomplete state instead of
        // grinding uia_set_value to step 100.
        if (IsTypingCapability(toolName) && IsTypingExhaustedForStep(stepActions))
        {
            await AppendEventAsync(snapshot, "TypingExhausted", null,
                new
                {
                    failure_code = "TypingExhaustedForStep",
                    requested_capability = toolName,
                    outcome.OutcomeId,
                    failure_limit = TypingFailureLimitPerStep
                }, ct);
            return Block("TypingExhaustedForStep",
                $"Typing has failed {TypingFailureLimitPerStep} times on this step. Stop retyping — " +
                "re-observe with read_screen_elements to confirm whether the text is already present; " +
                "if it is not, pause and reassess rather than looping.",
                step.SideEffectClass,
                AgentSafetyPolicy.RequiresInvariantApproval(step.SideEffectClass));
        }

        var skillDecision = _skillExecutor.PrepareAction(
            skill,
            new SkillExecutionContext(
                snapshot.Task.TaskId,
                snapshot.Goal,
                snapshot.Plan.Version,
                milestone,
                step,
                snapshot.WorldState?.Version ?? 0,
                _capabilityInventory.GetAll(),
                snapshot.Goal.Constraints,
                snapshot.WorldState),
            toolName,
            argumentsJson);
        if (!skillDecision.Allowed)
            return Block(
                skillDecision.FailureCode ?? "SkillPolicyRejected",
                skillDecision.Reason,
                step.SideEffectClass,
                AgentSafetyPolicy.RequiresInvariantApproval(step.SideEffectClass));
        var missingCapabilities = skill.RequiredCapabilities
            .Where(required => !HasAvailableCapability(required))
            .ToList();
        if (missingCapabilities.Count > 0)
            return Block("SkillPreconditionFailed",
                $"Required skill capabilities are unavailable: {string.Join(", ", missingCapabilities)}.");
        if (_capabilityInventory.Get(toolName) is
            {
                Availability: CapabilityAvailability.Unavailable
            } or {
                Health: CapabilityHealthState.Unhealthy,
                Attempts: >= 3
            })
            return Block("CapabilityUnavailable",
                $"Capability '{toolName}' is marked unavailable and must not be dispatched.");

        var route = _router.Route(
            step, outcome, toolName, category, argumentsJson, snapshot.WorldState,
            _capabilityInventory.GetAll());
        if (!route.Allowed)
        {
            await AppendEventAsync(snapshot, "RoutingDecisionRejected", null,
                new
                {
                    failure_code = route.FailureCode ?? "CapabilityPolicyRejected",
                    route.Reason,
                    route.SelectedCapability,
                    route.Candidates,
                    outcome.OutcomeId,
                    requested_capability = toolName
                }, ct);
            return Block(route.FailureCode ?? "CapabilityPolicyRejected", route.Reason,
                route.SideEffectClass, route.RequiresApproval);
        }
        if (route.RequiresApproval)
        {
            var approval = await _store.GetValidApprovalAsync(
                snapshot.Task.TaskId,
                step.StepId,
                ApprovalFingerprint(toolName, argumentsJson),
                DateTimeOffset.UtcNow,
                ct);
            if (approval is null)
                return Block(
                    "ApprovalRequired",
                    "A scoped, unexpired approval for this exact consequential action is required.",
                    route.SideEffectClass,
                    approval: true);
        }

        if (context.ReconciliationRequired && !CapabilityRouter.IsObservationTool(toolName))
            return Block("UnknownOutcome",
                "A prior action has an unknown outcome. Refresh state and reconcile it before another side effect.",
                route.SideEffectClass, route.RequiresApproval);
        if (step.Status == TaskStepStatus.Failed
            && RequiresFreshObservation(step.FailureCode)
            && !CapabilityRouter.IsObservationTool(toolName))
            return Block("FreshObservationRequired",
                "A fresh observation is required before retrying this failed stateful action.",
                route.SideEffectClass, route.RequiresApproval);

        var fingerprint = Hash(argumentsJson);
        var priorAttempt = await _store.GetLatestActionForStepAsync(
            snapshot.Task.TaskId, step.StepId, ct);
        var recoveryStrategy = JsonString(argumentsJson, "recovery_strategy");
        if (priorAttempt is { Status: TaskActionStatus.Failed })
        {
            var freshObservation = snapshot.WorldState is { } observed
                                   && observed.Version > priorAttempt.ObservationVersion
                                   && CapabilityRouter.IsObservationTool(observed.Source);
            var argumentsChanged = !string.Equals(
                fingerprint, priorAttempt.ArgumentsFingerprint, StringComparison.Ordinal);
            var capabilityChanged = !string.Equals(
                toolName, priorAttempt.ToolName, StringComparison.Ordinal);
            var strategyChanged = !string.IsNullOrWhiteSpace(recoveryStrategy)
                                  && !string.Equals(recoveryStrategy,
                                      priorAttempt.RecoveryStrategy, StringComparison.Ordinal);
            var planChanged = !string.Equals(
                snapshot.Plan.PlanId, priorAttempt.PlanId, StringComparison.Ordinal);
            if (!freshObservation && !argumentsChanged && !capabilityChanged
                && !strategyChanged && !planChanged)
            {
                await AppendEventAsync(snapshot, "RetryRejected", priorAttempt.ActionId,
                    new
                    {
                        failure_code = "BlindRetryBlocked",
                        priorAttempt.FailureCode,
                        priorAttempt.ObservationVersion,
                        current_observation_version = snapshot.WorldState?.Version ?? 0,
                        requested_capability = toolName
                    }, ct);
                return Block("BlindRetryBlocked",
                    "Retry requires a fresh observation, changed capability/arguments, " +
                    "new recovery strategy, or validated plan revision.",
                    route.SideEffectClass, route.RequiresApproval);
            }
        }
        // Observations are point-in-time reads, not side effects. Their identity must
        // include the state version that they inspect so recovery can re-observe after
        // an action. Mutating actions deliberately keep a stable key across state
        // versions to prevent duplicate dispatch after interruption.
        var observationVersionKey = CapabilityRouter.IsObservationTool(toolName)
            ? $"|world:{snapshot.WorldState?.Version ?? 0}"
            : string.Empty;
        var baseIdempotencyKey = Hash(
            $"{snapshot.Task.TaskId}|{step.StepId}|{toolName}|{fingerprint}" +
            observationVersionKey);
        var existing = await _store.GetActionByIdempotencyKeyAsync(
            snapshot.Task.TaskId, baseIdempotencyKey, ct);
        if (existing is { Status: TaskActionStatus.Verified or TaskActionStatus.Dispatching
            or TaskActionStatus.Dispatched or TaskActionStatus.UnknownOutcome })
        {
            Interlocked.Increment(ref _duplicatesPrevented);
            await AppendEventAsync(snapshot, "DuplicateSideEffectPrevented", existing.ActionId,
                new { existing.Status, toolName }, ct);
            return Block("DuplicateActionPrevented",
                "This action was already dispatched or verified. Reconcile state instead of repeating it.",
                route.SideEffectClass, route.RequiresApproval);
        }

        var attempt = step.AttemptCount + 1;
        var idempotencyKey = existing is { Status: TaskActionStatus.Failed }
            ? $"{baseIdempotencyKey}:retry:{attempt}"
            : baseIdempotencyKey;
        var now = DateTimeOffset.UtcNow;
        var action = new TaskAction
        {
            ActionId = Id("action"),
            TaskId = snapshot.Task.TaskId,
            PlanId = snapshot.Plan.PlanId,
            MilestoneId = step.MilestoneId,
            StepId = step.StepId,
            OutcomeId = outcome.OutcomeId,
            ToolCallId = toolCallId,
            ToolName = toolName,
            ArgumentsFingerprint = fingerprint,
            IdempotencyKey = idempotencyKey,
            SideEffectClass = route.SideEffectClass,
            Status = TaskActionStatus.Dispatching,
            Attempt = attempt,
            ParentActionId = priorAttempt?.ActionId,
            RetryId = priorAttempt is null ? null : Id("retry"),
            ObservationVersion = snapshot.WorldState?.Version ?? 0,
            RecoveryStrategy = recoveryStrategy,
            CapabilityChangeReason = priorAttempt is not null
                                     && !string.Equals(priorAttempt.ToolName, toolName,
                                         StringComparison.Ordinal)
                ? $"Capability changed from {priorAttempt.ToolName} to {toolName}."
                : null,
            CreatedAtUtc = now,
            UpdatedAtUtc = now
        };

        step.AttemptCount = attempt;
        step.Status = TaskStepStatus.ExecutionPending;
        step.ToolName = toolName;
        step.StructuredArgumentsReference = $"sha256:{fingerprint}";
        step.CurrentIdempotencyKey = action.IdempotencyKey;
        step.StartedAtUtc ??= now;
        var expectedTaskVersion = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Running;
        snapshot.Task.CurrentMilestoneId = step.MilestoneId;
        snapshot.Task.CurrentStepId = step.StepId;
        snapshot.Task.CheckpointVersion++;
        var checkpoint = CreateCheckpoint(snapshot, action.ActionId,
            route.SideEffectClass >= SideEffectClass.ReversibleMediumRisk
                ? ResumeSafetyStatus.ReconciliationRequired
                : ResumeSafetyStatus.Safe);
        var taskEvent = CreateEvent(snapshot, "ActionDispatching", action.ActionId,
            new
            {
                toolName,
                selected_capability = route.SelectedCapability,
                action.IdempotencyKey,
                route.Reason,
                route.Candidates,
                relevance = relevance.Reason,
                outcome.OutcomeId,
                action.ParentActionId,
                action.RetryId,
                action.RecoveryStrategy,
                action.ObservationVersion,
                action.CapabilityChangeReason
            }) with
        {
            ParentActionId = action.ParentActionId,
            RetryId = action.RetryId
        };
        if (!await _store.CommitActionPreparationAsync(
                snapshot.Task, expectedTaskVersion, step, action, checkpoint, taskEvent, ct))
            return Block("ConcurrentTaskUpdate",
                "Task state changed while preparing the action; reload before executing.",
                route.SideEffectClass, route.RequiresApproval);

        Interlocked.Increment(ref _actionsDispatched);
        Interlocked.Increment(ref _checkpointsSaved);
        if (priorAttempt is not null)
        {
            await AppendEventAsync(
                await RequireSnapshotAsync(snapshot.Task.TaskId, ct),
                "RetryScheduled",
                action.ActionId,
                new
                {
                    action.Attempt,
                    action.RetryId,
                    previous_action_id = priorAttempt.ActionId,
                    previous_outcome = priorAttempt.Status.ToString(),
                    reason = priorAttempt.FailureCode ?? "validated_recovery_change",
                    backoff_ms = 250,
                    blind_retry = false
                },
                ct);
        }
        var request = new AgentActionRequest
        {
            ActionId = action.ActionId,
            TaskId = snapshot.Task.TaskId,
            GoalId = snapshot.Goal.GoalId,
            PlanId = snapshot.Plan.PlanId,
            PlanVersion = snapshot.Plan.Version,
            MilestoneId = step.MilestoneId,
            StepId = step.StepId,
            OutcomeId = outcome.OutcomeId,
            SkillId = skill.SkillId,
            Intent = step.Description,
            RequestedCapability = toolName,
            ExpectedApplication = outcome.ExpectedApplication,
            ExpectedProcess = outcome.ExpectedProcess,
            ExpectedWindow = outcome.ExpectedApplication,
            TargetIntent = outcome.IntendedState,
            RequiredReadiness = RequiredReadiness(outcome, toolName),
            ExpectedEffect = outcome.IntendedState,
            VerificationPlan = step.VerificationPlan,
            IdempotencyKey = action.IdempotencyKey,
            RiskLevel = step.RiskLevel,
            SideEffectClass = route.SideEffectClass,
            Constraints = snapshot.Goal.Constraints,
            WorldStateVersion = snapshot.WorldState?.Version ?? 0,
            CheckpointId = checkpoint.CheckpointId
        };
        return new DurableActionPreparation(true, null, null, action,
            route.SideEffectClass, route.RequiresApproval)
        {
            Request = request,
            Route = route
        };
    }

    public async Task RecordApprovalAsync(
        DurableTurnContext context,
        string toolCallId,
        string toolName,
        string argumentsJson,
        string scope,
        bool granted,
        DateTimeOffset expiresAtUtc,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var step = SelectNextStep(snapshot.Plan)
                   ?? throw new InvalidOperationException(
                       "An approval cannot be recorded without an active step.");
        var approval = new TaskApproval
        {
            ApprovalId = Id("approval"),
            TaskId = snapshot.Task.TaskId,
            StepId = step.StepId,
            ToolCallId = toolCallId,
            ToolName = toolName,
            ActionFingerprint = ApprovalFingerprint(toolName, argumentsJson),
            Scope = scope,
            Status = granted ? "Granted" : "Denied",
            ExpiresAtUtc = granted ? expiresAtUtc : DateTimeOffset.UtcNow,
            CreatedAtUtc = DateTimeOffset.UtcNow
        };
        await _store.SaveApprovalAsync(approval, ct);
        await _store.AppendEventAsync(CreateEvent(
            snapshot,
            granted ? "ApprovalGranted" : "ApprovalDenied",
            null,
            new
            {
                approval.ApprovalId,
                approval.Scope,
                approval.ExpiresAtUtc,
                approval.ActionFingerprint
            },
            toolCallId: toolCallId), ct);
    }

    public async Task<DurableActionOutcome> RecordResultAsync(
        DurableTurnContext context,
        DurableActionPreparation preparation,
        ToolResult result,
        long durationMs,
        CancellationToken ct = default)
    {
        if (!preparation.Proceed || preparation.Action is null)
            throw new InvalidOperationException("An unprepared action cannot record a result.");
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var action = await _store.GetActionAsync(preparation.Action.ActionId, ct)
                     ?? throw new InvalidDataException("Prepared action was not persisted.");
        var step = snapshot.Plan.Milestones.SelectMany(m => m.Steps)
            .Single(s => s.StepId == action.StepId);
        var outcome = snapshot.Plan.Outcomes.Single(item =>
            item.OutcomeId == step.OutcomeId && item.OutcomeId == action.OutcomeId);
        var normalized = NormalizeResult(snapshot, outcome, step, action, result, durationMs);
        var failureDecision = RuntimeFailurePolicy.Classify(
            result,
            action.SideEffectClass,
            dispatched: true);
        var reconciliationObservation = context.ReconciliationRequired
                                        && CapabilityRouter.IsObservationTool(action.ToolName);
        var reconciliationResolution = reconciliationObservation
            ? ReconciliationResolution(result)
            : null;
        if (reconciliationObservation)
        {
            var observationVerified = result.Effect?.Verified == true;
            normalized = normalized with
            {
                NativeSuccess = result.Success,
                ExpectedEffectObserved = observationVerified,
                Verified = observationVerified,
                StepCompleted = false,
                FailureCode = reconciliationResolution is null
                    ? "UnknownOutcome"
                    : observationVerified ? null : "EffectNotObserved",
                VerificationReason = reconciliationResolution is null
                    ? "The observation did not determine whether the pending side effect occurred."
                    : $"The pending side effect was reconciled as occurred={reconciliationResolution.Value}."
            };
        }
        var capability = _capabilityInventory.RecordResult(
            action.ToolName,
            normalized.NativeSuccess,
            normalized.Verified,
            durationMs,
            normalized.FailureReason);
        await _store.SaveCapabilityHealthAsync(capability, ct);
        await SaveMetricAsync(snapshot, "tool_execution",
            normalized.Verified ? 1 : 0,
            new
            {
                action.ToolName,
                normalized.NativeSuccess,
                normalized.Verified,
                normalized.FailureCode,
                duration_ms = durationMs,
                capability.Health,
                capability.FailureRate
            }, ct);
        var evidenceId = Id("evidence");
        var evidence = CreateEvidence(snapshot, step, action, normalized, result, evidenceId);
        normalized = normalized with { EvidenceIds = new[] { evidenceId } };
        step.EvidenceIds.Add(evidenceId);
        if (!step.ObservationIds.Contains(evidenceId))
            step.ObservationIds.Add(evidenceId);
        step.ToolOutcome = failureDecision.Outcome;
        step.FailureClassification = failureDecision.Classification;
        step.VerificationDisposition = normalized.Verified
            ? VerificationDisposition.Verified
            : normalized.Executed
                ? VerificationDisposition.Inconclusive
                : VerificationDisposition.Failed;
        if (normalized.SideEffects.Count > 0
            && step.SideEffectClass >= SideEffectClass.ReversibleMediumRisk)
        {
            var target = string.Join(",", normalized.SideEffects);
            var compensation = _compensationCoordinator.Plan(
                action,
                target,
                "state captured by pre-action checkpoint",
                normalized.Verified ? "verified post-action state" : "unknown post-action state",
                step.RollbackOrCompensation);
            await _store.SaveCompensationAsync(compensation, ct);
        }

        action.UpdatedAtUtc = DateTimeOffset.UtcNow;
        TaskAction? reconciledAction = null;
        var reconciliationUnresolved = reconciliationObservation
                                       && reconciliationResolution is null;
        if (reconciliationObservation)
        {
            reconciledAction = snapshot.Checkpoint?.PendingActionId is { Length: > 0 } pendingId
                ? await _store.GetActionAsync(pendingId, ct)
                : null;
            action.Status = normalized.Verified
                ? TaskActionStatus.Verified
                : TaskActionStatus.Failed;
            action.FailureCode = normalized.FailureCode;
            action.FailureReason = normalized.FailureReason;
            step.FailureCode = reconciliationUnresolved ? "UnknownOutcome" : null;
            if (reconciliationUnresolved)
            {
                step.Status = TaskStepStatus.VerificationPending;
                outcome.Status = OutcomeStatus.EffectPending;
                snapshot.Task.Status = AgentTaskStatus.Paused;
            }
            else if (reconciliationResolution == false)
            {
                step.Status = TaskStepStatus.Ready;
                outcome.Status = OutcomeStatus.Ready;
                snapshot.Task.Status = AgentTaskStatus.Running;
                if (reconciledAction is not null)
                {
                    reconciledAction.Status = TaskActionStatus.Failed;
                    reconciledAction.FailureCode = "ReconciledNotOccurred";
                    reconciledAction.FailureReason =
                        "Fresh observation verified that the pending side effect did not occur.";
                    reconciledAction.UpdatedAtUtc = DateTimeOffset.UtcNow;
                    await _store.UpdateActionAsync(reconciledAction, ct);
                }
            }
            else
            {
                step.Status = TaskStepStatus.VerificationPending;
                outcome.Status = OutcomeStatus.EffectPending;
                snapshot.Task.Status = AgentTaskStatus.VerificationPending;
                if (reconciledAction is not null)
                {
                    reconciledAction.FailureCode = "ReconciledOccurred";
                    reconciledAction.FailureReason =
                        "Fresh observation verified that the side effect occurred; outcome verification remains required.";
                    reconciledAction.UpdatedAtUtc = DateTimeOffset.UtcNow;
                    await _store.UpdateActionAsync(reconciledAction, ct);
                }
            }
        }
        else if (!normalized.Executed || !normalized.NativeSuccess)
        {
            action.Status = TaskActionStatus.Failed;
            action.FailureCode = normalized.FailureCode;
            action.FailureReason = normalized.FailureReason;
            step.Status = TaskStepStatus.Failed;
            step.FailureCode = normalized.FailureCode;
            snapshot.Task.Status = AgentTaskStatus.Recovering;
            outcome.Status = OutcomeStatus.Failed;
            outcome.VerificationResult = normalized.FailureReason;
        }
        else if (!normalized.Verified)
        {
            action.Status = TaskActionStatus.VerificationPending;
            step.Status = TaskStepStatus.VerificationPending;
            step.FailureCode = normalized.FailureCode ?? "VerificationPending";
            snapshot.Task.Status = AgentTaskStatus.VerificationPending;
            outcome.Status = OutcomeStatus.EffectPending;
            outcome.VerificationResult = normalized.VerificationReason;
            Interlocked.Increment(ref _falseSuccessPrevented);
        }
        else if (CapabilityRouter.IsObservationTool(action.ToolName)
                 && (context.ReconciliationRequired
                     || step.SideEffectClass != SideEffectClass.ReadOnly))
        {
            action.Status = TaskActionStatus.Verified;
            step.Status = TaskStepStatus.Ready;
            step.FailureCode = null;
            snapshot.Task.Status = AgentTaskStatus.Running;
            outcome.Status = OutcomeStatus.Ready;
        }
        else if (normalized.StepCompleted)
        {
            action.Status = TaskActionStatus.Verified;
            step.Status = TaskStepStatus.Verified;
            step.FailureCode = null;
            snapshot.Task.Status = AgentTaskStatus.VerificationPending;
            outcome.Status = OutcomeStatus.Verified;
            outcome.VerificationResult = normalized.VerificationReason;
            if (!outcome.EvidenceIds.Contains(evidenceId))
                outcome.EvidenceIds.Add(evidenceId);
            Interlocked.Increment(ref _actionsVerified);
            step.CompletedAtUtc = DateTimeOffset.UtcNow;
        }
        else
        {
            action.Status = TaskActionStatus.ResultReceived;
            step.Status = TaskStepStatus.VerificationPending;
            snapshot.Task.Status = AgentTaskStatus.VerificationPending;
        }

        PromoteReadySteps(snapshot.Plan.Milestones.SelectMany(m => m.Steps).ToList());
        PromoteReadyOutcomes(snapshot.Plan.Outcomes);
        var next = SelectNextStep(snapshot.Plan);
        snapshot.Task.CurrentStepId = next?.StepId;
        snapshot.Task.CurrentMilestoneId = next?.MilestoneId ?? step.MilestoneId;
        var world = CreateWorldState(snapshot, action, normalized);
        snapshot.Task.CheckpointVersion++;
        var expectedTaskVersion = snapshot.Task.Version;
        var checkpoint = CreateCheckpoint(
            snapshot,
            reconciliationUnresolved
                ? reconciledAction?.ActionId
                : failureDecision.ReconciliationRequired
                  && action.SideEffectClass != SideEffectClass.ReadOnly
                    ? action.ActionId
                    : null,
            reconciliationUnresolved
            || failureDecision.ReconciliationRequired
               && action.SideEffectClass != SideEffectClass.ReadOnly
                ? ResumeSafetyStatus.ReconciliationRequired
                : ResumeSafetyStatus.Safe,
            world.Version, action.Status == TaskActionStatus.Verified ? action.ActionId : null);
        snapshot.Task.CurrentCheckpointId = checkpoint.CheckpointId;
        var events = new List<TaskEvent>
        {
            CreateEvent(snapshot, "ToolResultReceived", action.ActionId,
                new
                {
                    normalized.NativeSuccess,
                    normalized.FailureCode,
                    capability.Health,
                    capability.FailureRate,
                    capability.AverageLatencyMs
                }),
            CreateEvent(snapshot, "VerificationCompleted", action.ActionId,
                new { normalized.Verified, normalized.StepCompleted, evidenceId },
                world.Version, checkpoint.CheckpointId)
        };
        if (step.Status == TaskStepStatus.Verified)
            events.Add(CreateEvent(snapshot, "StepCompleted", action.ActionId,
                new { step.StepId, evidenceId }));
        else if (step.Status == TaskStepStatus.Failed)
            events.Add(CreateEvent(snapshot, "RecoveryStarted", action.ActionId,
                new { step.FailureCode, normalized.Retryable }));
        if (reconciliationObservation)
            events.Add(CreateEvent(snapshot, "UnknownOutcomeReconciled", action.ActionId,
                new
                {
                    pending_action_id = reconciledAction?.ActionId,
                    occurred = reconciliationResolution,
                    unresolved = reconciliationUnresolved,
                    observation_version = world.Version
                }, world.Version, checkpoint.CheckpointId));

        if (!await _store.CommitActionOutcomeAsync(
                snapshot.Task, expectedTaskVersion, snapshot.Plan, step, action, evidence, world,
                checkpoint, events, ct))
            throw new InvalidOperationException("Task changed concurrently while recording an action result.");
        Interlocked.Increment(ref _checkpointsSaved);
        await _store.SaveVerificationAsync(new TaskVerificationResult
        {
            VerificationId = Id("verification"),
            TaskId = snapshot.Task.TaskId,
            StepId = step.StepId,
            ActionId = action.ActionId,
            Disposition = step.VerificationDisposition ?? VerificationDisposition.Inconclusive,
            ExpectedState = outcome.IntendedState,
            ActualStateSummary = normalized.VerificationReason
                                 ?? normalized.FailureReason
                                 ?? "No independently verified state was available.",
            EvidenceIds = [evidenceId],
            SuggestedRecoveryAction = failureDecision.ReconciliationRequired
                ? "Reconcile current state before another side effect."
                : failureDecision.RetryAllowed
                    ? "Retry within the bounded runtime retry budget."
                    : null,
            CreatedAtUtc = DateTimeOffset.UtcNow
        }, ct);
        snapshot = (await _store.GetAsync(snapshot.Task.TaskId, ct))!;
        return new DurableActionOutcome(normalized, RefreshContext(snapshot));
    }

    public async Task<DurableCompletionDecision> EvaluateCompletionAsync(
        DurableTurnContext context,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var steps = snapshot.Plan.Milestones.SelectMany(m => m.Steps).ToList();
        if (snapshot.Task.Status == AgentTaskStatus.WaitingForClarification)
            return new(false, snapshot.Task.Status,
                "The task is waiting for clarification; no completion is claimed.", []);
        if (steps.Count == 0 || steps.Any(s => s.Status is not (
                TaskStepStatus.Verified or TaskStepStatus.Skipped)))
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            return new(false, snapshot.Task.Status,
                "The task is not complete because one or more required steps lack verification.",
                steps.SelectMany(s => s.EvidenceIds).Distinct().ToList());
        }
        var requiredOutcomes = snapshot.Plan.Outcomes
            .Where(outcome => outcome.Required && !outcome.Prohibited)
            .ToList();
        if (requiredOutcomes.Count == 0
            || requiredOutcomes.Any(outcome => outcome.Status != OutcomeStatus.Verified))
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            return new(false, AgentTaskStatus.VerificationPending,
                "The task is not complete because one or more required outcomes are unverified.",
                requiredOutcomes.SelectMany(outcome => outcome.EvidenceIds).Distinct().ToList());
        }

        var evidenceIds = steps.SelectMany(s => s.EvidenceIds).Distinct().ToList();
        var persistedEvidence = await _store.GetEvidenceAsync(snapshot.Task.TaskId, ct);
        var skill = snapshot.Task.SkillId is null
            ? null
            : _skillRegistry.GetSkill(snapshot.Task.SkillId);
        if (skill is null)
            return new(false, AgentTaskStatus.VerificationPending,
                "The task cannot complete because its persisted skill version is unavailable.",
                evidenceIds);
        var skillResult = _skillVerifier.Verify(
            skill, snapshot.Task, snapshot.Plan, persistedEvidence);
        if (!skillResult.Verified)
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            await AppendEventAsync(snapshot, "SkillVerificationFailed", null,
                new
                {
                    skillResult.SkillId,
                    skillResult.SkillVersion,
                    skillResult.FailureCode,
                    skillResult.FailureReason
                }, ct);
            return new(false, AgentTaskStatus.VerificationPending,
                skillResult.FailureReason
                ?? "The selected skill has not produced independently verified evidence.",
                evidenceIds);
        }
        var hasApproval = await _store.HasGrantedApprovalAsync(
            snapshot.Task.TaskId, DateTimeOffset.UtcNow, ct);
        foreach (var milestone in snapshot.Plan.Milestones)
        {
            var milestoneEvidence = milestone.Steps
                .SelectMany(s => s.EvidenceIds)
                .Distinct()
                .ToList();
            if (milestone.Steps.Any(s => s.Status is not (
                    TaskStepStatus.Verified or TaskStepStatus.Skipped))
                || milestoneEvidence.Count == 0)
            {
                milestone.Status = MilestoneStatus.VerificationPending;
                continue;
            }

            var milestoneItemsSatisfied = true;
            foreach (var item in milestone.DefinitionOfDone.Where(i => i.Required))
            {
                var matching = MatchingEvidence(
                    item,
                    persistedEvidence.Where(e => milestoneEvidence.Contains(e.EvidenceId)),
                    snapshot.WorldState,
                    hasApproval);
                if (matching.Count == 0)
                {
                    item.Status = DefinitionOfDoneStatus.NotVerifiable;
                    item.VerificationResult =
                        $"No evidence matching {item.VerificationType} is available.";
                    milestoneItemsSatisfied = false;
                    continue;
                }
                SatisfyDefinitionOfDone(item, matching);
            }
            milestone.Status = milestoneItemsSatisfied
                ? MilestoneStatus.Verified
                : MilestoneStatus.VerificationPending;
            if (milestoneItemsSatisfied)
                foreach (var evidenceId in milestoneEvidence)
                    if (!milestone.EvidenceIds.Contains(evidenceId))
                        milestone.EvidenceIds.Add(evidenceId);
        }
        snapshot.Plan.Status = snapshot.Plan.Milestones.All(
            m => m.Status == MilestoneStatus.Verified)
            ? PlanStatus.Completed
            : PlanStatus.Active;
        await _store.SavePlanAsync(snapshot.Plan, ct);
        if (snapshot.Plan.Status != PlanStatus.Completed)
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            return new(false, AgentTaskStatus.VerificationPending,
                "One or more milestones remain unverified.", evidenceIds);
        }

        foreach (var item in snapshot.Goal.DefinitionOfDone.Where(i => i.Required))
        {
            if (item.Status == DefinitionOfDoneStatus.WaivedByUser) continue;
            var matching = MatchingEvidence(
                item, persistedEvidence, snapshot.WorldState, hasApproval);
            if (matching.Count == 0)
            {
                item.Status = DefinitionOfDoneStatus.NotVerifiable;
                item.VerificationResult =
                    $"No evidence matching {item.VerificationType} is available.";
                continue;
            }
            SatisfyDefinitionOfDone(item, matching);
        }
        snapshot.Goal.UpdatedAtUtc = DateTimeOffset.UtcNow;
        await _store.SaveGoalAsync(snapshot.Goal, ct);
        if (snapshot.Goal.DefinitionOfDone.Any(i =>
                i.Required && i.Status is not (
                    DefinitionOfDoneStatus.Satisfied or DefinitionOfDoneStatus.WaivedByUser)))
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            return new(false, AgentTaskStatus.VerificationPending,
                "Required Definition of Done items remain unsatisfied.", evidenceIds);
        }

        // Final belt-and-suspenders value invariant (T5 PART A). The matcher (T4) already
        // gates outcome.Status on a real read-back, and MatchingEvidence checks evidence
        // TYPE only — so this last gate INDEPENDENTLY re-confirms, from persisted evidence,
        // that every required TextValue outcome carrying a concrete ExpectedValue has at
        // least one piece of evidence whose ACTUALLY-READ value matches. Completion can
        // never be claimed from an intent echo, no matter which upstream path set the
        // status. (Values referenced as "outcome:<id>" are resolved by the matcher path;
        // FileState uses hashes and carries no ExpectedValue here — both left untouched.)
        foreach (var textOutcome in requiredOutcomes)
        {
            if (textOutcome.Kind is not OutcomeKind.TextValue) continue;
            if (textOutcome.ExpectedValue is not { Length: > 0 } expectedValue) continue;
            if (expectedValue.StartsWith("outcome:", StringComparison.Ordinal)) continue;
            if (!EvidenceConfirmsOutcomeValue(persistedEvidence, textOutcome.OutcomeId, expectedValue))
            {
                Interlocked.Increment(ref _falseSuccessPrevented);
                await AppendEventAsync(snapshot, "CompletionValueUnverified", null,
                    new { textOutcome.OutcomeId, expected = expectedValue }, ct);
                return new(false, AgentTaskStatus.VerificationPending,
                    $"Completion blocked: no independently read value matches the expected " +
                    $"text '{expectedValue}' for a required outcome. Observe the control's real " +
                    "state before completion is claimed.", evidenceIds);
            }
        }

        var expected = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Completed;
        snapshot.Task.CompletedAtUtc = DateTimeOffset.UtcNow;
        snapshot.Task.CurrentStepId = null;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException("Task changed concurrently during completion.");
        snapshot = (await _store.GetAsync(snapshot.Task.TaskId, ct))!;
        await SaveCheckpointAsync(snapshot, null, ResumeSafetyStatus.Safe, ct);
        await AppendEventAsync(snapshot, "TaskCompleted", null,
            new
            {
                evidence_ids = evidenceIds,
                skill.SkillId,
                skill.Version,
                skill_execution_id = snapshot.Task.SkillExecutionId
            }, ct);
        await AppendEventAsync(snapshot, "RunCompleted", null,
            new
            {
                mode = snapshot.Task.Mode.ToString(),
                verified = true,
                evidence_count = evidenceIds.Count
            }, ct);
        await SaveMetricAsync(snapshot, "task_verified_completion", 1,
            new
            {
                skill.SkillId,
                evidence_count = evidenceIds.Count,
                checkpoint_version = snapshot.Task.CheckpointVersion
            }, ct);
        return new(true, AgentTaskStatus.Completed,
            "The persisted Definition of Done is satisfied by verified evidence.", evidenceIds);
    }

    public async Task CompleteConversationAsync(
        DurableTurnContext context,
        string finalResultReference,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        if (snapshot.Task.Mode != AgentTaskMode.Conversation)
            throw new InvalidOperationException(
                "Only a conversation-mode run can complete without tool verification.");
        if (snapshot.Task.Status == AgentTaskStatus.Completed)
            return;

        // A conversation-mode turn can still have dispatched a real side effect
        // (a browser type, a click) whose outcome never resolved — a killed
        // relay, a timeout, an inconclusive postcondition. Closing that as a
        // completed run claiming "no side effect" is exactly the false success
        // this runtime exists to prevent, so the unresolved case ends as
        // NeedsUser and the event states the side effect truthfully.
        //
        // The unresolved check answers "is anything still unknown?". It does not
        // answer "did anything definitely fail?", and a definite failure is
        // resolved — so a turn whose real work failed outright walked straight
        // past this guard and closed as Completed with its goal struck through.
        // Completed has to mean the goal was reached, not that the loop stopped.
        var unresolved = await FindUnresolvedSideEffectAsync(snapshot, ct);
        var failed = unresolved is null
            ? await FindFailedUnrecoveredActionAsync(snapshot, ct)
            : null;
        var blocking = unresolved ?? failed;
        if (blocking is not null)
        {
            // The run state machine only reaches NeedsUser through the unknown
            // -outcome state; walk it rather than forcing an illegal jump.
            snapshot = await MoveAsync(snapshot, AgentTaskStatus.UnknownOutcome, ct);
            snapshot = await MoveAsync(snapshot, AgentTaskStatus.NeedsUser, ct);
        }
        var expected = snapshot.Task.Version;
        snapshot.Task.Status = blocking is null
            ? AgentTaskStatus.Completed
            : snapshot.Task.Status;
        snapshot.Task.CompletedAtUtc = blocking is null ? DateTimeOffset.UtcNow : null;
        snapshot.Task.FinalResultReference = finalResultReference;
        snapshot.Task.CurrentStepId = blocking?.StepId;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException(
                "Conversation run changed concurrently while completing.");
        snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
        await SaveCheckpointAsync(
            snapshot,
            unresolved?.ActionId,
            blocking is null
                ? ResumeSafetyStatus.Safe
                : ResumeSafetyStatus.ReconciliationRequired,
            ct);
        if (blocking is not null)
        {
            Interlocked.Increment(ref _falseSuccessPrevented);
            await AppendEventAsync(snapshot, "RunNeedsUser", blocking.ActionId,
                unresolved is not null
                    ? new
                    {
                        mode = AgentTaskMode.Conversation.ToString(),
                        final_result_reference = finalResultReference,
                        side_effects = true,
                        failure_code = "UnknownOutcome",
                        reason = "A dispatched side effect never resolved; the user must "
                                 + "confirm the real state before this run is closed.",
                        blocking.ToolName,
                        action_status = blocking.Status.ToString()
                    }
                    : new
                    {
                        mode = AgentTaskMode.Conversation.ToString(),
                        final_result_reference = finalResultReference,
                        side_effects = false,
                        failure_code = blocking.FailureCode ?? "ActionFailed",
                        reason = "Work this turn required failed and nothing recovered it, "
                                 + "so the request has not been carried out.",
                        blocking.ToolName,
                        action_status = blocking.Status.ToString()
                    }, ct);
            return;
        }
        await AppendEventAsync(snapshot, "RunCompleted", null,
            new
            {
                mode = AgentTaskMode.Conversation.ToString(),
                final_result_reference = finalResultReference,
                side_effects = false
            }, ct);
    }

    /// <summary>Advances the run one legal state at a time; a transition the
    /// state machine rejects is skipped rather than throwing, so the caller
    /// still ends in a non-success state.</summary>
    private async Task<DurableTaskSnapshot> MoveAsync(
        DurableTaskSnapshot snapshot,
        AgentTaskStatus next,
        CancellationToken ct)
    {
        if (snapshot.Task.Status == next
            || !AgentRunStateMachine.CanTransition(snapshot.Task.Status, next))
            return snapshot;
        var expected = snapshot.Task.Version;
        snapshot.Task.Status = next;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException(
                "Conversation run changed concurrently while completing.");
        return await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
    }

    /// <summary>
    /// The first action that failed outright and that nothing recovered, or null
    /// when every failure was followed by success.
    ///
    /// Recovery is read from the STEP, not from the action list: a retry — with
    /// the same capability or a different one — runs on the same step, so a step
    /// that ended Verified or Skipped had its work done in the end and its
    /// earlier failures are history. A step still sitting in Failed is work the
    /// user asked for that never happened. This is what keeps "try, fail, succeed
    /// another way" a completed run while "try, fail, give up" is not.
    /// </summary>
    private async Task<TaskAction?> FindFailedUnrecoveredActionAsync(
        DurableTaskSnapshot snapshot,
        CancellationToken ct)
    {
        foreach (var step in snapshot.Plan.Milestones.SelectMany(item => item.Steps))
        {
            if (step.Status is TaskStepStatus.Verified or TaskStepStatus.Skipped)
                continue;
            // Only work bound to something the user actually asked for blocks the
            // close; an optional or prohibited outcome failing is not a broken promise.
            if (step.OutcomeId is { Length: > 0 } outcomeId
                && snapshot.Plan.Outcomes.FirstOrDefault(item => item.OutcomeId == outcomeId)
                    is { } outcome
                && (!outcome.Required || outcome.Prohibited))
                continue;
            foreach (var action in await _store.GetActionsForStepAsync(
                         snapshot.Task.TaskId, step.StepId, ct))
                if (action.Status == TaskActionStatus.Failed)
                    return action;
        }
        return null;
    }

    /// <summary>The first dispatched mutating action of this run whose outcome
    /// is still unknown, or null when nothing is outstanding.</summary>
    private async Task<TaskAction?> FindUnresolvedSideEffectAsync(
        DurableTaskSnapshot snapshot,
        CancellationToken ct)
    {
        foreach (var step in snapshot.Plan.Milestones.SelectMany(item => item.Steps))
        {
            foreach (var action in await _store.GetActionsForStepAsync(
                         snapshot.Task.TaskId, step.StepId, ct))
            {
                if (action.SideEffectClass == SideEffectClass.ReadOnly)
                    continue;
                if (action.Status is TaskActionStatus.Dispatching
                    or TaskActionStatus.Dispatched
                    or TaskActionStatus.ResultReceived
                    or TaskActionStatus.VerificationPending
                    or TaskActionStatus.UnknownOutcome)
                    return action;
            }
        }
        return null;
    }

    public async Task PauseAsync(
        DurableTurnContext context,
        string reason,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var expected = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Paused;
        snapshot.Task.PausedAtUtc = DateTimeOffset.UtcNow;
        snapshot.Task.FailureReason = reason;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException("Task changed concurrently while pausing.");
        snapshot = (await _store.GetAsync(snapshot.Task.TaskId, ct))!;
        await SaveCheckpointAsync(snapshot, snapshot.Checkpoint?.PendingActionId,
            snapshot.Checkpoint?.PendingActionId is null
                ? ResumeSafetyStatus.Safe
                : ResumeSafetyStatus.ReconciliationRequired, ct);
        await AppendEventAsync(snapshot, "TaskPaused", null, new { reason }, ct);
    }

    public async Task CancelAsync(
        string taskId,
        string reason,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(taskId, ct);
        if (snapshot.Task.Status is AgentTaskStatus.Completed or AgentTaskStatus.Failed
            or AgentTaskStatus.Cancelled or AgentTaskStatus.Abandoned)
            return;

        var expected = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Cancelling;
        snapshot.Task.CancellationRequestedAtUtc = DateTimeOffset.UtcNow;
        snapshot.Task.FailureSummary = reason;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException("Run changed concurrently while cancellation began.");
        snapshot = await RequireSnapshotAsync(taskId, ct);
        await AppendEventAsync(snapshot, "RunCancelling", null,
            new { reason = SensitiveDataRedactor.Redact(reason) }, ct);

        expected = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Cancelled;
        snapshot.Task.CompletedAtUtc = DateTimeOffset.UtcNow;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expected, ct))
            throw new InvalidOperationException("Run changed concurrently while cancellation completed.");
        snapshot = await RequireSnapshotAsync(taskId, ct);
        await SaveCheckpointAsync(
            snapshot,
            snapshot.Checkpoint?.PendingActionId,
            snapshot.Checkpoint?.PendingActionId is null
                ? ResumeSafetyStatus.Safe
                : ResumeSafetyStatus.ReconciliationRequired,
            ct);
        await AppendEventAsync(snapshot, "RunCancelled", null,
            new
            {
                reason = SensitiveDataRedactor.Redact(reason),
                pending_action = snapshot.Checkpoint?.PendingActionId is not null
            }, ct);

        var children = (await _store.GetIncompleteAsync(ownerScope: null, ct: ct))
            .Where(item => string.Equals(
                item.Task.ParentRunId,
                taskId,
                StringComparison.Ordinal))
            .Select(item => item.Task.TaskId)
            .ToArray();
        foreach (var childId in children)
            await CancelAsync(
                childId,
                "Parent run cancellation propagated to this child run.",
                ct);
    }

    public async Task<IReadOnlyList<DurableRecoveryResult>> RecoverInterruptedRunsAsync(
        string? ownerScope = null,
        CancellationToken ct = default)
    {
        var incomplete = await _store.GetIncompleteAsync(ownerScope, ct);
        var results = new List<DurableRecoveryResult>(incomplete.Count);
        foreach (var initial in incomplete)
        {
            ct.ThrowIfCancellationRequested();
            var snapshot = initial;
            var previous = snapshot.Task.Status;
            if (snapshot.Checkpoint is not null
                && !CheckpointIntegrity.IsValid(snapshot.Checkpoint))
            {
                var expected = snapshot.Task.Version;
                snapshot.Task.Status = AgentTaskStatus.Failed;
                snapshot.Task.FailureSummary = "Checkpoint integrity validation failed.";
                await _store.UpdateTaskAsync(snapshot.Task, expected, ct);
                results.Add(new DurableRecoveryResult(
                    snapshot.Task.TaskId,
                    previous,
                    AgentTaskStatus.Failed,
                    ResumeSafetyStatus.Corrupted,
                    true,
                    "Checkpoint integrity validation failed."));
                continue;
            }

            if (previous is AgentTaskStatus.WaitingForClarification
                or AgentTaskStatus.WaitingForApproval
                or AgentTaskStatus.NeedsUser
                or AgentTaskStatus.Paused)
            {
                results.Add(new DurableRecoveryResult(
                    snapshot.Task.TaskId,
                    previous,
                    previous,
                    snapshot.Checkpoint?.ResumeSafetyStatus ?? ResumeSafetyStatus.Safe,
                    true,
                    "The run remains paused for an explicit user decision."));
                continue;
            }

            var expectedVersion = snapshot.Task.Version;
            snapshot.Task.Status = AgentTaskStatus.Interrupted;
            if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
                throw new InvalidOperationException(
                    "Run changed concurrently while marking restart interruption.");
            snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
            await AppendEventAsync(snapshot, "RunInterrupted", null,
                new { previous_status = previous.ToString() }, ct);

            var reconciliation = snapshot.Checkpoint is { PendingActionId: not null }
                                 || snapshot.Checkpoint?.ResumeSafetyStatus
                                    == ResumeSafetyStatus.ReconciliationRequired;
            expectedVersion = snapshot.Task.Version;
            snapshot.Task.Status = reconciliation
                ? AgentTaskStatus.Reconciling
                : AgentTaskStatus.Resuming;
            if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
                throw new InvalidOperationException(
                    "Run changed concurrently while applying restart recovery.");
            snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);

            if (!reconciliation)
            {
                expectedVersion = snapshot.Task.Version;
                snapshot.Task.Status = AgentTaskStatus.Ready;
                if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
                    throw new InvalidOperationException(
                        "Run changed concurrently while resuming from a safe checkpoint.");
                snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
            }
            await AppendEventAsync(snapshot,
                reconciliation ? "ReconciliationRequired" : "RunResumed",
                snapshot.Checkpoint?.PendingActionId,
                new
                {
                    checkpoint_id = snapshot.Checkpoint?.CheckpointId,
                    reconciliation_required = reconciliation
                }, ct);
            results.Add(new DurableRecoveryResult(
                snapshot.Task.TaskId,
                previous,
                snapshot.Task.Status,
                reconciliation
                    ? ResumeSafetyStatus.ReconciliationRequired
                    : ResumeSafetyStatus.Safe,
                reconciliation,
                reconciliation
                    ? "A dispatched action has an unknown outcome and requires reconciliation."
                    : "The run resumed from a safe durable checkpoint."));
        }
        return results;
    }

    public async Task<IReadOnlyList<TaskEvent>> GetEventsAsync(
        string taskId,
        long afterSequence = 0,
        CancellationToken ct = default) =>
        (await _store.GetEventsAsync(taskId, ct))
        .Where(item => item.Sequence > afterSequence)
        .OrderBy(item => item.Sequence)
        .ToArray();

    public async Task<DurableTurnContext> RecordUnknownOutcomeAsync(
        DurableTurnContext context,
        DurableActionPreparation preparation,
        string reason,
        CancellationToken ct = default)
    {
        if (!preparation.Proceed || preparation.Action is null)
            throw new InvalidOperationException(
                "Only a dispatched durable action can have an unknown outcome.");
        var snapshot = await RequireSnapshotAsync(context.TaskId, ct);
        var action = await _store.GetActionAsync(preparation.Action.ActionId, ct)
                     ?? throw new InvalidDataException("The dispatched action was not persisted.");
        var step = snapshot.Plan.Milestones.SelectMany(item => item.Steps)
            .Single(item => item.StepId == action.StepId);
        action.Status = TaskActionStatus.UnknownOutcome;
        action.FailureCode = "UnknownOutcome";
        action.FailureReason = reason;
        action.UpdatedAtUtc = DateTimeOffset.UtcNow;
        await _store.UpdateActionAsync(action, ct);
        step.Status = TaskStepStatus.VerificationPending;
        step.FailureCode = "UnknownOutcome";
        step.ToolOutcome = DurableToolOutcome.UnknownOutcome;
        step.FailureClassification = RuntimeFailureClassification.UnknownOutcome;
        step.VerificationDisposition = VerificationDisposition.Inconclusive;
        await _store.SaveStepAsync(step, ct);

        var expectedVersion = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Paused;
        snapshot.Task.PausedAtUtc = DateTimeOffset.UtcNow;
        snapshot.Task.FailureReason = reason;
        snapshot.Task.CheckpointVersion++;
        var checkpoint = CreateCheckpoint(
            snapshot,
            action.ActionId,
            ResumeSafetyStatus.ReconciliationRequired);
        snapshot.Task.CurrentCheckpointId = checkpoint.CheckpointId;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
            throw new InvalidOperationException(
                "Task changed concurrently while recording an unknown outcome.");
        snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
        await _store.SaveCheckpointAsync(checkpoint, ct);
        Interlocked.Increment(ref _checkpointsSaved);
        await AppendEventAsync(snapshot, "TaskPaused", action.ActionId,
            new
            {
                reason,
                failure_code = "UnknownOutcome",
                reconciliation_required = true
            }, ct);
        return new DurableTurnContext(
            snapshot.Task.TaskId,
            snapshot.Task.ConversationId,
            snapshot.Task.Mode,
            BuildPinnedContext(snapshot with { Checkpoint = checkpoint }),
            true);
    }

    public RuntimeMetricSnapshot GetMetrics() => new(
        Interlocked.Read(ref _tasksCreated),
        Interlocked.Read(ref _checkpointsSaved),
        Interlocked.Read(ref _actionsDispatched),
        Interlocked.Read(ref _actionsVerified),
        Interlocked.Read(ref _duplicatesPrevented),
        Interlocked.Read(ref _falseSuccessPrevented),
        Interlocked.Read(ref _resumeReconciliations),
        Interlocked.Read(ref _planRevisions));

    public async Task<AuthoritativePlanView> GetAuthoritativePlanAsync(
        string taskId,
        CancellationToken ct = default)
    {
        var snapshot = await RequireSnapshotAsync(taskId, ct);
        var currentStepId = snapshot.Task.CurrentStepId
                            ?? snapshot.Plan.Milestones.SelectMany(item => item.Steps)
                                .FirstOrDefault(item => item.Status is TaskStepStatus.InProgress
                                    or TaskStepStatus.ExecutionPending
                                    or TaskStepStatus.VerificationPending
                                    or TaskStepStatus.Failed)?.StepId;
        var latestAction = currentStepId is null
            ? null
            : await _store.GetLatestActionForStepAsync(taskId, currentStepId, ct);
        return AuthoritativePlanView.From(snapshot, latestAction);
    }

    public async Task<string> ExportMetricsAsync(
        string taskId,
        string format,
        CancellationToken ct = default)
    {
        var metrics = await _store.GetMetricsAsync(taskId, ct);
        if (string.Equals(format, "json", StringComparison.OrdinalIgnoreCase))
            return JsonSerializer.Serialize(
                metrics.Select(metric => new
                {
                    metric.MetricId,
                    metric.TaskId,
                    metric.SkillId,
                    metric.Name,
                    metric.Value,
                    DimensionsJson = SensitiveDataRedactor.Redact(metric.DimensionsJson),
                    metric.TimestampUtc
                }),
                new JsonSerializerOptions { WriteIndented = true });
        if (!string.Equals(format, "csv", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Metrics format must be 'json' or 'csv'.", nameof(format));

        var lines = new List<string>
        {
            "metric_id,task_id,skill_id,name,value,timestamp_utc,dimensions_json"
        };
        lines.AddRange(metrics.Select(metric => string.Join(",",
            Csv(metric.MetricId),
            Csv(metric.TaskId),
            Csv(metric.SkillId),
            Csv(metric.Name),
            metric.Value.ToString(System.Globalization.CultureInfo.InvariantCulture),
            Csv(metric.TimestampUtc.ToUniversalTime().ToString("O")),
            Csv(SensitiveDataRedactor.Redact(metric.DimensionsJson)))));
        return string.Join(Environment.NewLine, lines);
    }

    private AgentToolResult NormalizeResult(
        DurableTaskSnapshot snapshot,
        TaskOutcome outcome,
        TaskStep step,
        TaskAction action,
        ToolResult result,
        long durationMs)
    {
        var computer = result.ComputerAction;
        var effect = result.Effect;
        var nativeSuccess = effect?.NativeSuccess ?? computer?.OsInputAccepted ?? result.Success;
        var effectObserved = effect?.ExpectedEffectObserved ?? computer?.EffectVerified ?? false;
        var toolReportedVerified = effect?.Verified ?? computer?.EffectVerified ?? false;
        // There used to be a short-circuit here: a ReadOnly tool that returned
        // Success was credited with having observed the intended effect, for
        // StateObserved / Generic / ProcessState / FileState outcomes. That is
        // the same conflation T4 removed one layer down — "the call did not
        // error" is not "the thing I was asked about was seen" — and it is what
        // let a successful "check open windows" stand in as evidence for a goal
        // about docker containers.
        //
        // Effect observation now comes only from the tool saying it observed
        // something. The three read tools that reported bare success
        // (list_directory, search_files, web_fetch) were changed to report their
        // observation with evidence instead, which is where that claim belongs:
        // the tool knows whether it read anything, and the runtime does not.
        var matchOutcome = outcome;
        if (outcome.ExpectedValue is { } outcomeReference
            && outcomeReference.StartsWith("outcome:", StringComparison.Ordinal))
        {
            var sourceId = outcomeReference["outcome:".Length..];
            var source = snapshot.Plan.Outcomes.FirstOrDefault(item =>
                item.OutcomeId == sourceId && item.Status == OutcomeStatus.Verified);
            if (source?.ExpectedValue is { Length: > 0 } resolvedValue)
                matchOutcome = outcome with { ExpectedValue = resolvedValue };
        }
        var match = _outcomeMatcher.Match(
            matchOutcome, step, action, result, toolReportedVerified);
        var verified = match.Matched;
        var stepCompleted = verified;
        var now = DateTimeOffset.UtcNow;
        return new AgentToolResult(
            action.ToolCallId,
            snapshot.Task.TaskId,
            snapshot.Plan.PlanId,
            step.MilestoneId,
            step.StepId,
            action.ActionId,
            action.ToolName,
            true,
            true,
            true,
            result.Success || result.Error is not null,
            nativeSuccess,
            effect?.ExitCode,
            effect?.StatusCode,
            result.Success,
            effectObserved,
            verified,
            stepCompleted,
            effect?.FailureCode ?? computer?.FailureCode ?? match.FailureCode
                ?? (result.Success ? null : "ToolExecutionFailed"),
            effect?.FailureReason ?? computer?.FailureReason
                ?? (!match.Matched ? match.Reason : result.Error),
            effect?.Retryable ?? computer?.Retryable ?? !result.Success,
            effect?.SideEffects ?? Array.Empty<string>(),
            Array.Empty<string>(),
            action.CreatedAtUtc,
            now,
            durationMs,
            SensitiveDataRedactor.Classify(result.Content))
        {
            GoalId = snapshot.Goal.GoalId,
            OutcomeId = outcome.OutcomeId,
            PlanVersion = snapshot.Plan.Version,
            Dispatched = true,
            NativeAccepted = nativeSuccess,
            TargetResolved = computer?.TargetResolved ?? effect is not null,
            TargetValidated = computer?.TargetValidated ?? effect is not null,
            ReceiverConfirmed = computer?.TargetValidated ?? effect is not null,
            EffectObserved = effectObserved,
            EffectMatchedOutcome = match.Matched,
            UnknownOutcome = false,
            ActualApplication = outcome.ExpectedApplication,
            ActualHwnd = computer?.WindowHandle,
            ActualPid = computer?.ProcessId,
            ActualControlIdentity = match.Evidence.FirstOrDefault(value =>
                value.Contains("control", StringComparison.OrdinalIgnoreCase)
                || value.Contains("element", StringComparison.OrdinalIgnoreCase)),
            BeforeState = null,
            AfterState = match.Evidence.FirstOrDefault(),
            VerificationReason = match.Reason
        };
    }

    private DurableTaskSnapshot CreateInitialSnapshot(
        string conversationId,
        string ownerScope,
        GoalSpec goal,
        AgentTaskMode mode,
        string taskId,
        SkillDefinition skill,
        DurableRunRequest request)
    {
        var now = DateTimeOffset.UtcNow;
        var planId = Id("plan");
        var milestoneId = Id("milestone");
        var compiled = _outcomeCompiler.Compile(goal, taskId, milestoneId, skill);
        goal.DefinitionOfDone.Clear();
        goal.DefinitionOfDone.AddRange(compiled.DefinitionOfDone);
        var steps = compiled.Steps.ToList();
        var milestone = new TaskMilestone
        {
            MilestoneId = milestoneId,
            TaskId = taskId,
            PlanId = planId,
            Title = mode == AgentTaskMode.SimpleAction
                ? "Complete the requested action"
                : "Reach the verified requested outcome",
            Objective = goal.DesiredOutcome,
            DefinitionOfDone = goal.DefinitionOfDone.Select(CloneDefinitionOfDone).ToList(),
            Status = MilestoneStatus.Ready,
            Sequence = 1,
            Steps = steps
        };
        var plan = new TaskPlan
        {
            PlanId = planId,
            TaskId = taskId,
            Version = 1,
            GoalVersion = goal.Version,
            PlanningReason = "Initial receding-horizon plan.",
            CreatedBy = "runtime",
            CreatedAtUtc = now,
            Constraints = goal.Constraints.ToList(),
            Outcomes = compiled.Outcomes.ToList(),
            Milestones = [milestone]
        };
        var task = new AgentTask
        {
            TaskId = taskId,
            ConversationId = conversationId,
            GoalId = goal.GoalId,
            Mode = mode,
            Status = AgentTaskStatus.Ready,
            CurrentPlanVersion = 1,
            CurrentMilestoneId = milestoneId,
            CurrentStepId = steps.FirstOrDefault(step => step.Status == TaskStepStatus.Ready)?.StepId,
            CreatedAtUtc = now,
            UpdatedAtUtc = now,
            RetryBudget = 5,
            ActionBudget = mode == AgentTaskMode.LongHorizonTask ? 200 : 50,
            ModelCallBudget = mode == AgentTaskMode.LongHorizonTask ? 150 : 50,
            TimeBudget = mode == AgentTaskMode.LongHorizonTask
                ? TimeSpan.FromHours(2)
                : TimeSpan.FromMinutes(20),
            RiskLevel = goal.RiskLevel,
            CheckpointVersion = 1,
            OwnerScope = ownerScope,
            ParentRunId = request.ParentRunId,
            TriggerType = request.TriggerType,
            UserIntent = request.UserIntent,
            SecurityContextJson = request.SecurityContextJson,
            PermissionContextJson = request.PermissionContextJson,
            WorkspaceContextJson = request.WorkspaceContextJson,
            ModelContextJson = request.ModelContextJson,
            ChildRunBudget = request.ChildRunBudget,
            RecursionDepth = request.RecursionDepth,
            SkillId = skill.SkillId,
            SkillVersion = skill.Version,
            SkillExecutionId = $"skill_execution_{Guid.NewGuid():N}"
        };
        var partial = new DurableTaskSnapshot(goal, task, plan, null, null);
        var checkpoint = CreateCheckpoint(partial, null, ResumeSafetyStatus.Safe);
        task.CurrentCheckpointId = checkpoint.CheckpointId;
        return partial with { Checkpoint = checkpoint };
    }

    private async Task SaveCheckpointAsync(
        DurableTaskSnapshot snapshot,
        string? pendingActionId,
        ResumeSafetyStatus safety,
        CancellationToken ct)
    {
        var expectedTaskVersion = snapshot.Task.Version;
        snapshot.Task.CheckpointVersion++;
        var checkpoint = CreateCheckpoint(snapshot, pendingActionId, safety);
        snapshot.Task.CurrentCheckpointId = checkpoint.CheckpointId;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expectedTaskVersion, ct))
            throw new InvalidOperationException("Task changed concurrently while checkpointing.");
        await _store.SaveCheckpointAsync(checkpoint, ct);
        Interlocked.Increment(ref _checkpointsSaved);
    }

    private static TaskCheckpoint CreateCheckpoint(
        DurableTaskSnapshot snapshot,
        string? pendingActionId,
        ResumeSafetyStatus safety,
        int? worldStateVersion = null,
        string? lastCompletedActionId = null)
    {
        var invariant = BuildPinnedContext(snapshot);
        var checkpoint = new TaskCheckpoint
        {
            CheckpointId = Id("checkpoint"),
            TaskId = snapshot.Task.TaskId,
            PlanVersion = snapshot.Plan.Version,
            CurrentMilestoneId = snapshot.Task.CurrentMilestoneId,
            CurrentStepId = snapshot.Task.CurrentStepId,
            TaskStatus = snapshot.Task.Status,
            WorldStateVersion = worldStateVersion ?? snapshot.WorldState?.Version ?? 0,
            LastCompletedActionId = lastCompletedActionId
                                    ?? snapshot.Checkpoint?.LastCompletedActionId,
            PendingActionId = pendingActionId,
            ApprovalState = "No unscoped approval is implied by this checkpoint.",
            ContextInvariantSnapshot = invariant,
            CreatedAtUtc = DateTimeOffset.UtcNow,
            ResumeSafetyStatus = safety,
            Checksum = string.Empty,
            Version = snapshot.Task.CheckpointVersion
        };
        return checkpoint with { Checksum = CheckpointIntegrity.Compute(checkpoint) };
    }

    private static WorldStateSnapshot CreateWorldState(
        DurableTaskSnapshot snapshot,
        TaskAction action,
        AgentToolResult result) =>
        new()
        {
            WorldStateId = Id("world"),
            TaskId = snapshot.Task.TaskId,
            Version = (snapshot.WorldState?.Version ?? 0) + 1,
            ObservedAtUtc = DateTimeOffset.UtcNow,
            Source = action.ToolName,
            Confidence = result.Verified ? 1.0 : 0.5,
            ActiveProcess = result.ToolName == "open_application" ? "verified by tool" : null,
            Permissions = result.Authorized ? ["action_authorized"] : [],
            Artifacts = result.SideEffects.ToList(),
            Unknowns = result.Verified ? [] : ["Expected effect is not independently verified."],
            Provenance = $"tool_call:{result.ToolCallId};action:{result.ActionId}",
            ExpiresAtUtc = DateTimeOffset.UtcNow.AddMinutes(
                result.ToolName.StartsWith("computer_", StringComparison.Ordinal) ? 1 : 15)
        };

    private static WorldStateSnapshot CreateEnvironmentWorldState(
        DurableTaskSnapshot snapshot,
        IReadOnlyList<EnvironmentFact> facts)
    {
        string? Value(string name) => facts.FirstOrDefault(
            fact => string.Equals(fact.Name, name, StringComparison.Ordinal))?.Value;
        return new WorldStateSnapshot
        {
            WorldStateId = Id("world"),
            TaskId = snapshot.Task.TaskId,
            Version = (snapshot.WorldState?.Version ?? 0) + 1,
            ObservedAtUtc = DateTimeOffset.UtcNow,
            Source = "environment_observer",
            Confidence = facts.Count == 0 ? 0 : facts.Average(fact => fact.Confidence),
            CurrentProject = Value("working_directory"),
            DiskState = Value("system_drive_free_bytes") is { } free
                ? $"available_bytes={free}"
                : null,
            Artifacts = [],
            Unknowns = facts.Where(fact => !fact.Verified)
                .Select(fact => fact.Name)
                .ToList(),
            Provenance = string.Join(";", facts.Select(fact =>
                $"{fact.Name}:{fact.Source}:{fact.ObservedAtUtc:O}")),
            ExpiresAtUtc = facts.Where(fact => fact.ExpiresAtUtc is not null)
                .Select(fact => fact.ExpiresAtUtc)
                .Min()
        };
    }

    private static TaskEvidence CreateEvidence(
        DurableTaskSnapshot snapshot,
        TaskStep step,
        TaskAction action,
        AgentToolResult result,
        ToolResult raw,
        string evidenceId) =>
        new()
        {
            EvidenceId = evidenceId,
            TaskId = snapshot.Task.TaskId,
            OutcomeId = step.OutcomeId,
            StepId = step.StepId,
            ActionId = action.ActionId,
            EvidenceType = raw.ComputerAction is not null
                ? EvidenceType.UiaState
                : raw.Effect?.ExitCode is not null
                  || raw.Effect?.Evidence.Any(value =>
                      value.StartsWith("process_exit_code=", StringComparison.Ordinal)) == true
                    ? EvidenceType.ProcessExit
                    : result.Verified
                        ? EvidenceType.Verification
                        : EvidenceType.ToolResult,
            Source = action.ToolName,
            Timestamp = DateTimeOffset.UtcNow,
            Summary = SensitiveDataRedactor.Redact(
                raw.Success ? Truncate(raw.Content, 500) : raw.Error),
            StructuredData = JsonSerializer.Serialize(new
            {
                result.NativeSuccess,
                result.ExpectedEffectObserved,
                result.Verified,
                result.FailureCode,
                result.ExitCode,
                observation_trust = UntrustedObservationBoundary
                    .ClassifySource(action.ToolName).ToString(),
                evidence = raw.Effect?.Evidence ?? Array.Empty<string>()
            }),
            Confidence = result.Verified ? 1.0 : 0.5,
            SensitiveDataClassification = SensitiveDataRedactor.Classify(raw.Content),
            RetentionPolicy = raw.ImageDataUri is null ? "task-default" : "target-image-short-lived",
            TrustLevel = UntrustedObservationBoundary.ClassifySource(action.ToolName),
            ProvenanceJson = JsonSerializer.Serialize(new
            {
                source_type = action.ToolName,
                tool_call_id = action.ToolCallId,
                action_id = action.ActionId,
                parent_run_id = snapshot.Task.ParentRunId,
                trust = UntrustedObservationBoundary.ClassifySource(action.ToolName).ToString()
            }),
            AllowedUses = ["read", "summarize", "extract_facts", "verify"],
            ProhibitedUses =
            [
                "change_policy",
                "grant_permission",
                "select_external_destination",
                "authorize_side_effect"
            ]
        };

    private static void PromoteReadySteps(IReadOnlyList<TaskStep> steps)
    {
        var verified = steps.Where(s => s.Status == TaskStepStatus.Verified)
            .Select(s => s.StepId).ToHashSet(StringComparer.Ordinal);
        foreach (var step in steps.Where(s => s.Status == TaskStepStatus.Pending))
            if (step.Dependencies.All(verified.Contains))
                step.Status = TaskStepStatus.Ready;
    }

    private static TaskStep? SelectNextStep(TaskPlan plan)
    {
        var steps = plan.Milestones.OrderBy(m => m.Sequence).SelectMany(m => m.Steps).ToList();
        PromoteReadySteps(steps);
        return steps.FirstOrDefault(s => s.Status is TaskStepStatus.Ready
            or TaskStepStatus.InProgress or TaskStepStatus.ExecutionPending
            or TaskStepStatus.VerificationPending or TaskStepStatus.Failed);
    }

    private static bool RequiresFreshObservation(string? failureCode) =>
        failureCode is "StaleObservation" or "WrongWindow" or "TargetNotFound"
            or "AmbiguousTarget" or "VerificationFailed" or "EffectMismatch"
            // R3′: computer_type now reports read-back-unavailable under its own code
            // instead of "EffectMismatch"; keep the same require-fresh-observation
            // behaviour it had before the split (non-regressive).
            or "ReadBackUnavailable";

    private async Task<DurableTaskSnapshot> RequireSnapshotAsync(
        string taskId,
        CancellationToken ct) =>
        await _store.GetAsync(taskId, ct)
        ?? throw new InvalidDataException($"Durable task '{taskId}' was not found.");

    private async Task<DurableTaskSnapshot> HydrateLegacyOutcomeCoverageAsync(
        DurableTaskSnapshot snapshot,
        CancellationToken ct)
    {
        var existingSteps = snapshot.Plan.Milestones.SelectMany(item => item.Steps).ToList();
        if (snapshot.Plan.Outcomes.Count > 0
            && existingSteps.All(step => !string.IsNullOrWhiteSpace(step.OutcomeId)))
            return snapshot;

        var skill = snapshot.Task.SkillId is { Length: > 0 } skillId
            ? _skillRegistry.GetSkill(skillId)
            : null;
        skill ??= _skillResolver.Resolve(snapshot.Goal, snapshot.Task.TaskId,
            snapshot.WorldState).Skill;
        var milestone = snapshot.Plan.Milestones.OrderBy(item => item.Sequence).First();
        var untouched = existingSteps.All(step => step.AttemptCount == 0
                                                  && step.EvidenceIds.Count == 0);
        if (untouched)
        {
            var graph = _outcomeCompiler.Compile(
                snapshot.Goal, snapshot.Task.TaskId, milestone.MilestoneId, skill);
            snapshot.Plan.Outcomes.Clear();
            snapshot.Plan.Outcomes.AddRange(graph.Outcomes);
            milestone.Steps.Clear();
            milestone.Steps.AddRange(graph.Steps);
            foreach (var item in graph.DefinitionOfDone)
                if (milestone.DefinitionOfDone.All(existing => existing.Id != item.Id))
                    milestone.DefinitionOfDone.Add(item);
        }
        else
        {
            snapshot.Plan.Outcomes.Clear();
            foreach (var currentMilestone in snapshot.Plan.Milestones)
            {
                for (var index = 0; index < currentMilestone.Steps.Count; index++)
                {
                    var step = currentMilestone.Steps[index];
                    var outcomeId = $"outcome_legacy_{step.StepId}";
                    snapshot.Plan.Outcomes.Add(new TaskOutcome
                    {
                        OutcomeId = outcomeId,
                        TaskId = snapshot.Task.TaskId,
                        Description = step.Description,
                        IntendedState = step.IntendedOutcome,
                        Dependencies = step.Dependencies.Select(dependency =>
                            $"outcome_legacy_{dependency}").ToList(),
                        Kind = OutcomeKind.Generic,
                        VerificationRequirements = [step.VerificationPlan],
                        EvidenceRequirements = ["legacy evidence correlated to the preserved step"],
                        SourceGoalVersion = snapshot.Goal.Version,
                        Status = step.Status == TaskStepStatus.Verified
                            ? OutcomeStatus.Verified
                            : step.Status == TaskStepStatus.Failed
                                ? OutcomeStatus.Failed
                                : step.Status == TaskStepStatus.Ready
                                    ? OutcomeStatus.Ready
                                    : OutcomeStatus.Pending,
                        EvidenceIds = step.EvidenceIds.ToList()
                    });
                    currentMilestone.Steps[index] = step with { OutcomeId = outcomeId };
                }
            }
        }

        await _store.SavePlanAsync(snapshot.Plan, ct);
        snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
        await AppendEventAsync(snapshot, "LegacyPlanOutcomeCoverageHydrated", null,
            new
            {
                outcome_count = snapshot.Plan.Outcomes.Count,
                preserved_executed_steps = !untouched,
                backward_compatible = true
            }, ct);
        return snapshot;
    }

    private async Task<DurableTaskSnapshot> ResolveClarificationAsync(
        DurableTaskSnapshot snapshot,
        IReadOnlyList<GatewayMessage> messages,
        CancellationToken ct)
    {
        if (snapshot.Task.Status != AgentTaskStatus.WaitingForClarification)
            return snapshot;
        var reply = messages
            .Where(message => string.Equals(message.Role, "user", StringComparison.Ordinal)
                              && message.Content is string)
            .Select(message => (string)message.Content!)
            .LastOrDefault();
        if (string.IsNullOrWhiteSpace(reply)
            || string.Equals(
                reply.Trim(),
                snapshot.Goal.OriginalUserRequest.Trim(),
                StringComparison.Ordinal))
            return snapshot;

        snapshot.Goal.Version++;
        snapshot.Goal.NormalizedGoal =
            $"{snapshot.Goal.OriginalUserRequest} [clarification: {reply.Trim()}]";
        snapshot.Goal.DesiredOutcome =
            $"Verified completion of the clarified target: {reply.Trim()}";
        snapshot.Goal.MissingInformation.Clear();
        snapshot.Goal.UpdatedAtUtc = DateTimeOffset.UtcNow;
        await _store.SaveGoalAsync(snapshot.Goal, ct);

        snapshot = snapshot with
        {
            Plan = snapshot.Plan with { GoalVersion = snapshot.Goal.Version }
        };
        var currentStep = snapshot.Plan.Milestones
            .SelectMany(milestone => milestone.Steps)
            .FirstOrDefault(step => step.StepId == snapshot.Task.CurrentStepId);
        if (currentStep is not null)
        {
            var replacement = currentStep with
            {
                Description = snapshot.Goal.NormalizedGoal,
                IntendedOutcome = snapshot.Goal.DesiredOutcome,
                Status = TaskStepStatus.Ready,
                FailureCode = null
            };
            var milestone = snapshot.Plan.Milestones.Single(
                item => item.MilestoneId == currentStep.MilestoneId);
            milestone.Steps[milestone.Steps.IndexOf(currentStep)] = replacement;
            await _store.SavePlanAsync(snapshot.Plan, ct);
        }

        var expectedVersion = snapshot.Task.Version;
        snapshot.Task.Status = AgentTaskStatus.Ready;
        if (!await _store.UpdateTaskAsync(snapshot.Task, expectedVersion, ct))
            throw new InvalidOperationException(
                "Task changed concurrently while applying clarification.");
        snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
        await SaveCheckpointAsync(snapshot, null, ResumeSafetyStatus.Safe, ct);
        snapshot = await RequireSnapshotAsync(snapshot.Task.TaskId, ct);
        await AppendEventAsync(
            snapshot,
            "ClarificationResolved",
            null,
            new { goal_version = snapshot.Goal.Version },
            ct);
        return snapshot;
    }

    private static DurableTurnContext RefreshContext(DurableTaskSnapshot snapshot) =>
        new(snapshot.Task.TaskId, snapshot.Task.ConversationId, snapshot.Task.Mode,
            BuildPinnedContext(snapshot),
            snapshot.Checkpoint?.ResumeSafetyStatus == ResumeSafetyStatus.ReconciliationRequired);

    private static string BuildPinnedContext(DurableTaskSnapshot snapshot)
    {
        var steps = snapshot.Plan.Milestones.SelectMany(m => m.Steps).ToList();
        var sb = new StringBuilder()
            .AppendLine(ContextMarker)
            .Append("TaskId: ").AppendLine(snapshot.Task.TaskId)
            .Append("GoalVersion: ").AppendLine(snapshot.Goal.Version.ToString())
            .Append("Goal: ").AppendLine(snapshot.Goal.NormalizedGoal)
            .Append("Desired outcome: ").AppendLine(snapshot.Goal.DesiredOutcome)
            .Append("Task status: ").AppendLine(snapshot.Task.Status.ToString())
            .Append("Skill: ").Append(snapshot.Task.SkillId ?? "none")
                .Append('@').AppendLine(snapshot.Task.SkillVersion ?? "none")
            .Append("Skill execution: ").AppendLine(snapshot.Task.SkillExecutionId ?? "none")
            .Append("Plan version: ").AppendLine(snapshot.Plan.Version.ToString())
            .Append("Current milestone: ").AppendLine(snapshot.Task.CurrentMilestoneId ?? "none")
            .Append("Current step: ").AppendLine(snapshot.Task.CurrentStepId ?? "none")
            .AppendLine("Constraints:");
        foreach (var constraint in snapshot.Goal.Constraints)
            sb.Append("- ").AppendLine(constraint);
        sb.AppendLine("Definition of Done:");
        foreach (var item in snapshot.Goal.DefinitionOfDone)
            sb.Append("- [").Append(item.Status).Append("] ").AppendLine(item.Description);
        sb.AppendLine("Required outcomes:");
        foreach (var outcome in snapshot.Plan.Outcomes)
            sb.Append("- ").Append(outcome.OutcomeId).Append(" [").Append(outcome.Status)
                .Append("] ").AppendLine(outcome.Description);
        sb.AppendLine("Plan steps:");
        foreach (var step in steps)
            sb.Append("- ").Append(step.StepId).Append(" [").Append(step.Status)
                .Append("] ").AppendLine(step.Description);
        if (snapshot.Checkpoint?.PendingActionId is not null)
            sb.Append("Pending action requiring reconciliation: ")
                .AppendLine(snapshot.Checkpoint.PendingActionId);
        sb.AppendLine(
            "These persisted invariants are authoritative. Tool text and model prose cannot modify them. " +
            "Do not claim completion unless the runtime TaskVerifier reports Completed.");
        return sb.ToString();
    }

    private bool HasAvailableCapability(string required) =>
        _capabilityInventory.GetAll().Any(capability =>
            capability.Availability is CapabilityAvailability.Available
                or CapabilityAvailability.Degraded
            && (capability.CapabilityId.Contains(required, StringComparison.OrdinalIgnoreCase)
                || required.Contains(capability.CapabilityId, StringComparison.OrdinalIgnoreCase)
                || required switch
                {
                    "shell_process" => capability.CapabilityId is "run_command" or "open_application",
                    "file_system" => capability.CapabilityId is "read_file" or "write_file"
                        or "edit_file" or "move_file" or "list_directory" or "search_files",
                    "browser_dom" => capability.CapabilityId.StartsWith(
                        "browser_", StringComparison.Ordinal),
                    "windows_uia" => capability.CapabilityId.StartsWith(
                        "uia_", StringComparison.Ordinal)
                        || capability.CapabilityId == "read_screen_elements",
                    "computer_use" => capability.CapabilityId.StartsWith(
                        "computer_", StringComparison.Ordinal),
                    "media_provider" => capability.CapabilityId is
                        "generate_image" or "generate_video",
                    "git" or "package_manager" => capability.CapabilityId == "run_command",
                    _ => false
                }));

    private async Task EnsureCapabilityHealthLoadedAsync(CancellationToken ct)
    {
        if (Interlocked.Exchange(ref _capabilityHealthLoaded, 1) != 0) return;
        var persisted = await _store.GetCapabilityHealthAsync(ct);
        foreach (var capability in persisted)
        {
            _capabilityInventory.Synchronize(
                [(capability.CapabilityId, capability.VerificationSupport)]);
            if (capability.Attempts > 0)
                for (var attempt = 0L; attempt < capability.Attempts; attempt++)
                    _capabilityInventory.RecordResult(
                        capability.CapabilityId,
                        attempt >= capability.Failures,
                        capability.VerificationSupport,
                        (long)capability.AverageLatencyMs,
                        capability.LastFailure);
        }
    }

    private Task SaveMetricAsync(
        DurableTaskSnapshot snapshot,
        string name,
        double value,
        object dimensions,
        CancellationToken ct) =>
        !_stabilization.RealRuntimeMetrics
            ? Task.CompletedTask
            : _store.SaveMetricAsync(new DurableMetric
        {
            MetricId = Id("metric"),
            TaskId = snapshot.Task.TaskId,
            SkillId = snapshot.Task.SkillId,
            Name = name,
            Value = value,
            DimensionsJson = JsonSerializer.Serialize(dimensions),
            TimestampUtc = DateTimeOffset.UtcNow
        }, ct);

    private async Task AppendEventAsync(
        DurableTaskSnapshot snapshot,
        string eventType,
        string? actionId,
        object data,
        CancellationToken ct) =>
        await _store.AppendEventAsync(CreateEvent(snapshot, eventType, actionId, data), ct);

    private static TaskEvent CreateEvent(
        DurableTaskSnapshot snapshot,
        string eventType,
        string? actionId,
        object data,
        int? worldStateVersion = null,
        string? checkpointId = null,
        string? toolCallId = null) =>
        new()
        {
            EventId = Id("event"),
            TaskId = snapshot.Task.TaskId,
            EventType = eventType,
            PlanId = snapshot.Plan.PlanId,
            MilestoneId = snapshot.Task.CurrentMilestoneId,
            StepId = snapshot.Task.CurrentStepId,
            OutcomeId = snapshot.Plan.Milestones.SelectMany(milestone => milestone.Steps)
                .FirstOrDefault(step => step.StepId == snapshot.Task.CurrentStepId)?.OutcomeId,
            ActionId = actionId,
            ToolCallId = toolCallId,
            CheckpointId = checkpointId,
            WorldStateVersion = worldStateVersion,
            TimestampUtc = DateTimeOffset.UtcNow,
            DataJson = JsonSerializer.Serialize(data)
        };

    private static DefinitionOfDoneItem CloneDefinitionOfDone(DefinitionOfDoneItem item) =>
        new()
        {
            Id = item.Id,
            Description = item.Description,
            VerificationType = item.VerificationType,
            RequiredEvidence = item.RequiredEvidence,
            Required = item.Required,
            Status = item.Status,
            VerificationResult = item.VerificationResult,
            EvidenceIds = item.EvidenceIds.ToList()
            ,OutcomeId = item.OutcomeId
        };

    private static TaskOutcome CloneOutcome(TaskOutcome outcome) => outcome with
    {
        Dependencies = outcome.Dependencies.ToList(),
        VerificationRequirements = outcome.VerificationRequirements.ToList(),
        EvidenceRequirements = outcome.EvidenceRequirements.ToList(),
        ProducedArtifacts = outcome.ProducedArtifacts.ToList(),
        EvidenceIds = outcome.EvidenceIds.ToList()
    };

    private static void PromoteReadyOutcomes(IReadOnlyList<TaskOutcome> outcomes)
    {
        var verified = outcomes.Where(outcome => outcome.Status == OutcomeStatus.Verified)
            .Select(outcome => outcome.OutcomeId)
            .ToHashSet(StringComparer.Ordinal);
        foreach (var outcome in outcomes.Where(outcome => outcome.Status == OutcomeStatus.Pending))
            if (outcome.Dependencies.All(verified.Contains))
                outcome.Status = OutcomeStatus.Ready;
    }

    private static ApplicationReadinessLevel RequiredReadiness(
        TaskOutcome outcome,
        string capability) => outcome.Kind switch
        {
            OutcomeKind.ApplicationReady => ApplicationReadinessLevel.WindowVisible,
            OutcomeKind.StateObserved => ApplicationReadinessLevel.EffectObservable,
            OutcomeKind.TextValue when capability is "uia_set_value" or "browser_type" =>
                ApplicationReadinessLevel.TargetControlReady,
            OutcomeKind.TextValue => ApplicationReadinessLevel.InputReady,
            OutcomeKind.OperationProcessed => ApplicationReadinessLevel.ReadyForRequestedAction,
            _ => ApplicationReadinessLevel.ProcessStarted
        };

    /// <summary>True when some persisted evidence for <paramref name="outcomeId"/> holds
    /// an actually-read value that matches <paramref name="expected"/>. Reads the
    /// structured `evidence` token array out of each evidence record's StructuredData and
    /// applies the SAME rule the matcher uses (SemanticOutcomeMatcher.EvidenceConfirmsValue)
    /// — never the free-text Summary, which could echo intent. (T5 PART A.)</summary>
    internal static bool EvidenceConfirmsOutcomeValue(
        IReadOnlyList<TaskEvidence> persistedEvidence,
        string outcomeId,
        string expected)
    {
        foreach (var record in persistedEvidence)
        {
            if (!string.Equals(record.OutcomeId, outcomeId, StringComparison.Ordinal))
                continue;
            var tokens = ReadEvidenceTokens(record.StructuredData);
            if (tokens.Count > 0
                && SemanticOutcomeMatcher.EvidenceConfirmsValue(tokens, expected))
                return true;
        }
        return false;
    }

    /// <summary>Extracts the string[] under the "evidence" property of an evidence
    /// record's StructuredData JSON (as written by CreateEvidence). Returns empty on any
    /// shape mismatch — a malformed record can never satisfy the value invariant.</summary>
    private static IReadOnlyList<string> ReadEvidenceTokens(string? structuredData)
    {
        if (string.IsNullOrWhiteSpace(structuredData))
            return Array.Empty<string>();
        try
        {
            using var doc = JsonDocument.Parse(structuredData);
            if (doc.RootElement.ValueKind != JsonValueKind.Object
                || !doc.RootElement.TryGetProperty("evidence", out var array)
                || array.ValueKind != JsonValueKind.Array)
                return Array.Empty<string>();
            var tokens = new List<string>();
            foreach (var element in array.EnumerateArray())
                if (element.ValueKind == JsonValueKind.String
                    && element.GetString() is { } token)
                    tokens.Add(token);
            return tokens;
        }
        catch (JsonException)
        {
            return Array.Empty<string>();
        }
    }

    private static List<string> MatchingEvidence(
        DefinitionOfDoneItem item,
        IEnumerable<TaskEvidence> evidence,
        WorldStateSnapshot? worldState,
        bool hasApproval)
    {
        var candidates = evidence.Where(value => value.Confidence >= 0.9)
            .Where(value => item.OutcomeId is null
                            || string.Equals(value.OutcomeId, item.OutcomeId,
                                StringComparison.Ordinal))
            .ToList();
        return item.VerificationType switch
        {
            "WorldState" when worldState?.Confidence >= 0.9 =>
                candidates.Select(value => value.EvidenceId).Take(1).ToList(),
            "ApprovalRecord" when hasApproval =>
                candidates.Select(value => value.EvidenceId).Take(1).ToList(),
            "ProcessOutput" => candidates
                .Where(value => value.StructuredData.Contains(
                    "version=", StringComparison.OrdinalIgnoreCase))
                .Select(value => value.EvidenceId).ToList(),
            "ProcessExit" => candidates
                .Where(value => value.EvidenceType == EvidenceType.ProcessExit)
                .Select(value => value.EvidenceId).ToList(),
            "ConstraintAudit" => candidates
                .Where(value => value.StructuredData.Contains(
                    "constraint_preserved=true", StringComparison.OrdinalIgnoreCase))
                .Select(value => value.EvidenceId).ToList(),
            "IndependentStateObservation" or "IndependentVerification" =>
                candidates.Select(value => value.EvidenceId).ToList(),
            "OutcomeEvidence" => candidates
                .Where(value => value.EvidenceType is EvidenceType.Verification
                    or EvidenceType.UiaState or EvidenceType.DomState
                    or EvidenceType.WindowState or EvidenceType.ProcessExit
                    or EvidenceType.FileHash or EvidenceType.FileMetadata)
                .Select(value => value.EvidenceId).ToList(),
            _ => []
        };
    }

    private static void SatisfyDefinitionOfDone(
        DefinitionOfDoneItem item,
        IReadOnlyList<string> evidenceIds)
    {
        item.Status = DefinitionOfDoneStatus.Satisfied;
        item.VerificationResult =
            $"Satisfied by evidence matching {item.VerificationType}.";
        foreach (var evidenceId in evidenceIds)
            if (!item.EvidenceIds.Contains(evidenceId))
                item.EvidenceIds.Add(evidenceId);
    }

    private static DurableActionPreparation Block(
        string code,
        string reason,
        SideEffectClass sideEffect = SideEffectClass.ReadOnly,
        bool approval = false) =>
        new(false, code, reason, null, sideEffect, approval);

    /// <summary>Anti-churn: how many times one capability may fail on a single step
    /// before it must be swapped for a different approach. Keeps a weak model (and the
    /// R1 recovery loop) from grinding the same failing tool — e.g. computer_type /
    /// clipboard on a Notepad field — instead of switching to the verified path.</summary>
    internal const int CapabilityFailureLimitPerStep = 2;

    /// <summary>True when <paramref name="toolName"/> has already failed the per-step
    /// limit on this step and must not be dispatched again. Observation tools and
    /// uia_set_value are EXEMPT — re-observing and the verified text-entry path must
    /// always stay available so the model can actually switch approach.</summary>
    internal static bool IsCapabilityExhaustedForStep(
        string toolName,
        IReadOnlyList<TaskAction> stepActions,
        int limit = CapabilityFailureLimitPerStep)
    {
        if (CapabilityRouter.IsObservationTool(toolName)
            || string.Equals(toolName, "uia_set_value", StringComparison.Ordinal))
            return false;
        var failures = stepActions.Count(action =>
            action.Status == TaskActionStatus.Failed
            && string.Equals(action.ToolName, toolName, StringComparison.Ordinal));
        return failures >= limit;
    }

    /// <summary>Aggregate typing-flail limit: total failures across ALL typing
    /// capabilities (uia_set_value included) on one step before typing is blocked and
    /// the step must be re-observed / paused. Higher than the per-capability limit so a
    /// legitimate type→observe→confirm retry isn't cut short.</summary>
    internal const int TypingFailureLimitPerStep = 4;

    private static bool IsTypingCapability(string toolName) =>
        toolName is "uia_set_value" or "computer_type" or "browser_type";

    /// <summary>True when typing capabilities have collectively failed the aggregate
    /// limit on this step — covers uia_set_value too, so even the preferred path can't
    /// grind forever.</summary>
    internal static bool IsTypingExhaustedForStep(
        IReadOnlyList<TaskAction> stepActions, int limit = TypingFailureLimitPerStep)
    {
        var failures = stepActions.Count(action =>
            action.Status == TaskActionStatus.Failed && IsTypingCapability(action.ToolName));
        return failures >= limit;
    }

    /// <summary>True for an action that would close/quit the target window (Alt+F4 and
    /// friends) — blocked mid-task so progress isn't discarded.</summary>
    internal static bool IsDestructiveWindowAction(string toolName, string argumentsJson)
    {
        if (toolName is not ("computer_key" or "computer_type" or "run_command"))
            return false;
        var lower = argumentsJson.ToLowerInvariant();
        return lower.Contains("alt+f4")
            || (lower.Contains("\"f4\"") && lower.Contains("alt"))
            || lower.Contains("taskkill")
            || lower.Contains("stop-process");
    }

    // Names the next approach to try when a capability is exhausted — the first
    // recommended capability for the outcome that isn't the failing one, biased to
    // re-observing first (read_screen_elements → uia_set_value for text entry).
    private static string SwitchSuggestion(TaskOutcome outcome, string failedTool)
    {
        var next = CapabilityRouter.RecommendedCapabilities(outcome)
            .FirstOrDefault(capability =>
                !string.Equals(capability, failedTool, StringComparison.Ordinal));
        return next is null
            ? "re-observe with read_screen_elements and try a different capability."
            : $"re-observe with read_screen_elements, then use {next} instead.";
    }

    private static string Id(string prefix) => $"{prefix}_{Guid.NewGuid():N}";

    private static GoalSpec SynthesizeGoal(string userIntent)
    {
        var now = DateTimeOffset.UtcNow;
        return new GoalSpec
        {
            GoalId = Id("goal"),
            OriginalUserRequest = userIntent,
            NormalizedGoal = userIntent.Trim(),
            DesiredOutcome = userIntent.Trim(),
            RiskLevel = AgentSafetyPolicy.InferRisk(userIntent),
            CreatedAtUtc = now,
            UpdatedAtUtc = now
        };
    }

    private static string Hash(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private static string ApprovalFingerprint(string toolName, string argumentsJson) =>
        Hash($"{toolName}|{argumentsJson}");
    private static string? JsonString(string json, string property)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            return document.RootElement.ValueKind == JsonValueKind.Object
                   && document.RootElement.TryGetProperty(property, out var value)
                   && value.ValueKind == JsonValueKind.String
                ? value.GetString()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static bool? ReconciliationResolution(ToolResult result)
    {
        var evidence = result.Effect?.Evidence
                       ?? result.ComputerAction?.Evidence
                       ?? Array.Empty<string>();
        foreach (var value in evidence)
        {
            if (string.Equals(value, "side_effect_occurred=true",
                    StringComparison.OrdinalIgnoreCase))
                return true;
            if (string.Equals(value, "side_effect_occurred=false",
                    StringComparison.OrdinalIgnoreCase))
                return false;
        }
        return null;
    }
    private static string Truncate(string? value, int max) =>
        string.IsNullOrEmpty(value) ? string.Empty
            : value.Length <= max ? value : value[..max] + "...";
    private static string Csv(string? value) =>
        $"\"{(value ?? string.Empty).Replace("\"", "\"\"", StringComparison.Ordinal)}\"";
}

public sealed class CapabilityRouter
{
    public CapabilityRoute Route(
        TaskStep step,
        TaskOutcome outcome,
        string requestedTool,
        ToolCategory category,
        string argumentsJson,
        WorldStateSnapshot? worldState,
        IReadOnlyList<CapabilityHealth> availableCapabilities)
    {
        var sideEffect = AgentSafetyPolicy.Classify(requestedTool, argumentsJson, category);
        var rawComputerFallback = requestedTool == "computer_click"
                                  && !argumentsJson.Contains(
                                      "\"target_description\"", StringComparison.Ordinal);
        var deterministicCapabilityAvailable = step.PreferredCapabilities.Any(
            capability => capability.Contains("file", StringComparison.OrdinalIgnoreCase)
                          || capability.Contains("process", StringComparison.OrdinalIgnoreCase)
                          || capability.Contains("package", StringComparison.OrdinalIgnoreCase));
        if (rawComputerFallback && deterministicCapabilityAvailable)
            return new(
                false,
                requestedTool,
                step.PreferredCapabilities,
                "A deterministic file/process capability must be attempted before raw Computer Use.",
                sideEffect,
                AgentSafetyPolicy.RequiresInvariantApproval(sideEffect),
                "Independent deterministic state verification")
            {
                FailureCode = "CapabilityPolicyRejected"
            };
        if (step.RiskLevel >= AgentRiskLevel.High
            && requestedTool == "computer_click"
            && !argumentsJson.Contains("\"target_description\"", StringComparison.Ordinal))
            return new(false, requestedTool, [], "High-risk raw coordinate clicks are not allowed.",
                sideEffect, true, "Semantic target state")
            {
                FailureCode = "CapabilityPolicyRejected"
            };

        var testAdapter = requestedTool.StartsWith("mock_", StringComparison.Ordinal)
                          || requestedTool.StartsWith("fake_", StringComparison.Ordinal);
        // open_application is a modality-neutral enabling precondition (see
        // GoalRelevanceGate.Evaluate): it must remain eligible regardless of the
        // current outcome kind, otherwise IsToolRelevant filters it out for a
        // TextValue/FileState outcome and the task can never open its app.
        // An MCP capability has no local substitute: no other capability can talk
        // to that connector, so there is nothing to route between and filtering it
        // against the desktop outcome taxonomy would just make it unavailable.
        var mcpOperation = requestedTool.StartsWith("mcp.", StringComparison.Ordinal);
        // The document worker has no substitute either. Nothing else in the
        // runtime can create or edit an Office file inside the sandbox, so
        // offering the router a choice between "document" and, say,
        // open_application produces the wrong winner for a scheduled document
        // job — and there was never a real alternative to route between.
        var documentOperation = requestedTool == "document";
        var candidateIds = outcome.Kind == OutcomeKind.Generic || testAdapter || mcpOperation
                           || documentOperation
                           || IsObservationTool(requestedTool)
                           || requestedTool == "open_application"
            ? new[] { requestedTool }
            : availableCapabilities.Select(capability => capability.CapabilityId)
                .Append(requestedTool)
                .Where(capability => GoalRelevanceGate.IsToolRelevant(outcome, capability))
                .Where(capability => ModalityMatches(outcome, capability))
                .Distinct(StringComparer.Ordinal)
                .ToArray();
        var scores = candidateIds.Select(capabilityId =>
        {
            var health = availableCapabilities.FirstOrDefault(capability =>
                capability.CapabilityId == capabilityId);
            var eligible = health?.Availability != CapabilityAvailability.Unavailable
                           && !(health is { Health: CapabilityHealthState.Unhealthy, Attempts: >= 3 });
            var score = eligible ? BaseScore(capabilityId) : 0;
            if (health?.Health == CapabilityHealthState.Degraded) score -= 12;
            if (health?.VerificationSupport == true) score += 8;
            if (capabilityId == requestedTool) score += 0.01;
            return new CapabilityCandidateScore(
                capabilityId,
                Math.Max(0, score),
                eligible,
                eligible
                    ? $"priority={BaseScore(capabilityId):0};health={health?.Health.ToString() ?? "runtime-resolved"};verification={health?.VerificationSupport.ToString() ?? "unknown"}"
                    : $"health={health?.Health};availability={health?.Availability}");
        }).OrderByDescending(candidate => candidate.Score).ToList();
        var selected = scores.FirstOrDefault(candidate => candidate.Eligible);
        if (selected is null)
            return new CapabilityRoute(false, requestedTool, [],
                "No goal-relevant healthy capability is available.", sideEffect,
                AgentSafetyPolicy.RequiresInvariantApproval(sideEffect),
                "Independent expected-effect evidence")
            {
                FailureCode = "CapabilityUnavailable",
                Candidates = scores
            };
        if (!string.Equals(selected.CapabilityId, requestedTool, StringComparison.Ordinal))
            return new CapabilityRoute(false, selected.CapabilityId,
                scores.Where(candidate => candidate.Eligible)
                    .Select(candidate => candidate.CapabilityId).ToList(),
                $"Runtime selected '{selected.CapabilityId}' ahead of model proposal '{requestedTool}'.",
                sideEffect, AgentSafetyPolicy.RequiresInvariantApproval(sideEffect),
                "Independent expected-effect evidence")
            {
                FailureCode = "CapabilitySelectionRequired",
                Candidates = scores
            };
        return new(true, selected.CapabilityId,
            scores.Where(candidate => candidate.Eligible)
                .Skip(1).Select(candidate => candidate.CapabilityId).ToList(),
            $"Runtime selected '{selected.CapabilityId}' from {scores.Count} goal-relevant candidates.",
            sideEffect,
            AgentSafetyPolicy.RequiresInvariantApproval(sideEffect),
            sideEffect == SideEffectClass.ReadOnly
                ? "Structured observation"
                : "Independent expected-effect evidence")
        {
            Candidates = scores
        };
    }

    // A capability's interaction modality must match the outcome's application
    // surface. Without this, browser DOM tools (globally the highest-priority
    // capabilities) would hijack a desktop Calculator/Notepad step — and desktop
    // input tools would hijack a browser form. Modality-neutral capabilities
    // (open_application, files, shell, observation) are eligible for either.
    private static bool ModalityMatches(TaskOutcome outcome, string capabilityId)
    {
        var browserOutcome = IsBrowserOutcome(outcome);
        if (capabilityId.StartsWith("browser_", StringComparison.Ordinal))
            return browserOutcome;
        if (IsDesktopSurfaceCapability(capabilityId))
            return !browserOutcome;
        return true;
    }

    private static bool IsBrowserOutcome(TaskOutcome outcome) =>
        outcome.Kind == OutcomeKind.BrowserState
        || (outcome.ExpectedApplication is { Length: > 0 } app
            && new[] { "browser", "chrome", "edge", "firefox", "safari", "web", "http" }
                .Any(token => app.Contains(token, StringComparison.OrdinalIgnoreCase)));

    private static bool IsDesktopSurfaceCapability(string capabilityId) =>
        capabilityId is "uia_click" or "uia_set_value"
            or "computer_click" or "computer_type" or "computer_key"
            or "computer_scroll" or "computer_drag";

    private static double BaseScore(string capability) => capability switch
    {
        "browser_upload" or "browser_download" => 110,
        "browser_type" or "browser_click" or "browser_read_page" => 100,
        "uia_set_value" or "uia_click" or "read_screen_elements" => 90,
        "open_application" or "run_command" or "read_file" or "write_file" => 88,
        "computer_key" => 70,
        "computer_type" => 65,
        "computer_click" => 55,
        "computer_screenshot" or "browser_screenshot" => 40,
        _ when capability.StartsWith("mock_", StringComparison.Ordinal)
               || capability.StartsWith("fake_", StringComparison.Ordinal) => 100,
        _ => 60
    };

    public static bool IsObservationTool(string toolName) =>
        toolName is "computer_screenshot" or "get_computer_state" or "read_screen_elements"
            or "browser_read_page" or "browser_list_tabs" or "browser_screenshot"
            or "read_file" or "list_directory" or "search_files" or "web_fetch" or "web_search";

    // Preferred capability order per outcome, most-preferred first. For a FileState
    // outcome whose content is already known (ExpectedValue present — e.g. "save the
    // text X to a.txt"), write_file/edit_file lead: the agent should write the file
    // directly and deterministically rather than fight a GUI Save As dialog (weak
    // models burn many steps on Ctrl+S / Alt+F / blind clicks and often mis-target the
    // path). GUI Save As is NOT removed — it stays the route when content was produced
    // inside an app and cannot be reproduced (ExpectedValue absent).
    public static IReadOnlyList<string> RecommendedCapabilities(TaskOutcome outcome) =>
        outcome.Kind switch
        {
            OutcomeKind.ApplicationReady => ["open_application", "windows_uia"],
            OutcomeKind.OperationProcessed => ["application_adapter", "windows_uia", "computer_use"],
            OutcomeKind.StateObserved => ["application_adapter", "windows_uia", "computer_vision"],
            // Text entry: on the desktop, uia_set_value leads — it binds to the real
            // control and verifies by read-back (see UiaSetValueTool). computer_type is
            // an unverifiable last resort (canvas/game/no-UIA), never the primary. A
            // browser text field goes through the DOM first.
            OutcomeKind.TextValue => IsBrowserOutcome(outcome)
                ? ["browser_dom", "uia_set_value"]
                : ["uia_set_value", "computer_type"],
            OutcomeKind.BrowserState => ["browser_dom", "windows_uia", "computer_vision"],
            OutcomeKind.ProcessState => ["shell_process"],
            OutcomeKind.FileState => outcome.ExpectedValue is { Length: > 0 }
                ? ["write_file", "edit_file", "file_system"]
                : ["file_system"],
            _ => ["application_capability", "browser_dom", "windows_uia", "computer_use"]
        };

    public static List<string> SuggestCapabilities(string description)
    {
        var lower = description.ToLowerInvariant();
        if (lower.Contains("browser") || lower.Contains("website") || lower.Contains("เว็บ"))
            return ["browser_dom", "uia", "computer_vision"];
        if (lower.Contains("file") || lower.Contains("project") || lower.Contains("ไฟล์")
            || lower.Contains("โปรเจกต์"))
            return ["file_tool", "process_tool", "browser_dom", "uia"];
        if (lower.Contains("install") || lower.Contains("build") || lower.Contains("run")
            || lower.Contains("ติดตั้ง") || lower.Contains("รัน"))
            return ["package_or_process_tool", "file_tool", "uia", "computer_vision"];
        return ["application_capability", "browser_dom", "uia", "computer_vision"];
    }
}

public static class AgentSafetyPolicy
{
    public static SideEffectClass Classify(
        string toolName,
        string argumentsJson,
        ToolCategory category)
    {
        var lower = argumentsJson.ToLowerInvariant();
        // An MCP capability's blast radius comes from the connector's risk policy,
        // not from ToolCategory.Network (which would land every connector call on
        // ReversibleLowRisk and skip approval entirely). Unknown stays consequential
        // on purpose: a tool nobody has classified must not be cheaper to run than
        // one that has been.
        if (toolName.StartsWith("mcp.", StringComparison.Ordinal))
            return McpRiskPolicy.ClassifyCapabilityName(toolName).SideEffectClass;
        if (toolName is "read_file" or "list_directory" or "search_files"
            or "computer_screenshot" or "get_computer_state" or "read_screen_elements"
            or "browser_read_page" or "browser_list_tabs" or "browser_screenshot"
            or "web_fetch" or "web_search" or "update_plan")
            return SideEffectClass.ReadOnly;
        if (toolName == "run_command")
        {
            if (ContainsAny(lower, "install", "uninstall", "setx ", "reg add", "production",
                    "start-process -verb runas", "runas "))
                return SideEffectClass.Consequential;
            return SideEffectClass.ReversibleMediumRisk;
        }
        if (toolName is "write_file" or "edit_file" or "move_file")
            return lower.Contains("\"overwrite\":true", StringComparison.Ordinal)
                ? SideEffectClass.Consequential
                : SideEffectClass.ReversibleMediumRisk;
        if (toolName == "browser_upload"
            || (toolName == "browser_type"
                && lower.Contains("\"submit\":true", StringComparison.Ordinal)))
            return SideEffectClass.Consequential;
        if (toolName is "write_clipboard" or "computer_type" or "browser_type" or "uia_set_value")
            return SensitiveDataRedactor.Classify(argumentsJson) == SensitiveDataClassification.Secret
                ? SideEffectClass.CredentialSensitive
                : SideEffectClass.ReversibleMediumRisk;
        if (toolName is "generate_image" or "generate_video")
            return SideEffectClass.Consequential;
        if (category is ToolCategory.Screen or ToolCategory.Browser)
            return SideEffectClass.ReversibleMediumRisk;
        if (category == ToolCategory.Shell)
            return SideEffectClass.Consequential;
        return SideEffectClass.ReversibleLowRisk;
    }

    public static SideEffectClass ClassifyText(string description)
    {
        var lower = description.ToLowerInvariant();
        if (ContainsAny(lower, "install", "uninstall", "admin", "production",
                "upload", "submit", "purchase", "send message", "post message",
                "delete", "remove",
                "ติดตั้ง", "ถอนการติดตั้ง", "ผู้ดูแล", "โปรดักชัน",
                "อัปโหลด", "ส่งข้อความ", "ลบ"))
            return SideEffectClass.Consequential;
        if (ContainsAny(lower, "write", "edit", "move", "click", "type", "download",
                "เขียน", "แก้", "ย้าย", "กด", "พิมพ์", "เปิด", "ดาวน์โหลด"))
            return SideEffectClass.ReversibleMediumRisk;
        return SideEffectClass.ReadOnly;
    }

    public static AgentRiskLevel InferRisk(string description) =>
        ClassifyText(description) switch
        {
            >= SideEffectClass.SecuritySensitive => AgentRiskLevel.Critical,
            SideEffectClass.Consequential or SideEffectClass.Irreversible => AgentRiskLevel.High,
            SideEffectClass.ReversibleMediumRisk => AgentRiskLevel.Medium,
            _ => AgentRiskLevel.Low
        };

    public static bool RequiresInvariantApproval(SideEffectClass sideEffect) =>
        sideEffect >= SideEffectClass.Consequential;

    private static bool ContainsAny(string value, params string[] terms) =>
        terms.Any(value.Contains);
}

public static class CheckpointIntegrity
{
    public static string Compute(TaskCheckpoint checkpoint)
    {
        var canonical = string.Join("|",
            checkpoint.TaskId,
            checkpoint.PlanVersion,
            checkpoint.CurrentMilestoneId,
            checkpoint.CurrentStepId,
            checkpoint.TaskStatus,
            checkpoint.WorldStateVersion,
            checkpoint.LastCompletedActionId,
            checkpoint.PendingActionId,
            checkpoint.ApprovalState,
            checkpoint.ContextInvariantSnapshot,
            checkpoint.CreatedAtUtc.ToUniversalTime().ToString("O"),
            checkpoint.ResumeSafetyStatus,
            checkpoint.Version);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
    }

    public static bool IsValid(TaskCheckpoint checkpoint)
    {
        try
        {
            return CryptographicOperations.FixedTimeEquals(
                Convert.FromHexString(checkpoint.Checksum),
                Convert.FromHexString(Compute(checkpoint)));
        }
        catch (FormatException)
        {
            return false;
        }
    }
}
