using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed record CompiledOutcomeGraph(
    IReadOnlyList<TaskOutcome> Outcomes,
    IReadOnlyList<TaskStep> Steps,
    IReadOnlyList<DefinitionOfDoneItem> DefinitionOfDone);

public sealed class OutcomeCompiler
{
    private static readonly Regex ClauseSeparator = new(
        @"(?ix)\s*(?:\band\s+then\b|\bthen\b|\bafter\s+that\b|\band\b(?=\s*(?:open|launch|calculate|compute|write|type|fill|run|build|verify|read)\b)|\u0E08\u0E32\u0E01\u0E19\u0E31\u0E49\u0E19|\u0E41\u0E25\u0E49\u0E27|\u0E41\u0E25\u0E30(?=\s*(?:\u0E40\u0E1B\u0E34\u0E14|\u0E04\u0E33\u0E19\u0E27\u0E13|\u0E40\u0E02\u0E35\u0E22\u0E19|\u0E1E\u0E34\u0E21\u0E1E\u0E4C|\u0E01\u0E23\u0E2D\u0E01)))\s*|[;\r\n]+",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex Expression = new(
        @"(?<left>-?\d+(?:\.\d+)?)\s*(?<operator>[xX\u00D7*+\-/])\s*(?<right>-?\d+(?:\.\d+)?)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    // The app name is captured as the FIRST run of Latin/number tokens after the
    // open/launch/start/\u0E40\u0E1B\u0E34\u0E14 verb \u2014 deliberately NOT anchored to the end of the
    // clause. Anchoring on `$` only matched "\u0E40\u0E1B\u0E34\u0E14 Notepad" when the app was the last
    // token, so "\u0E40\u0E1B\u0E34\u0E14 Notepad \u0E1E\u0E34\u0E21\u0E1E\u0E4C\u0E02\u0E49\u0E2D\u0E04\u0E27\u0E32\u0E21" produced no ApplicationReady precondition
    // (the deadlock this fix removes). Latin-only tokens stop the capture at the next
    // Thai character (e.g. the verb \u0E1E\u0E34\u0E21\u0E1E\u0E4C), and the verb look-ahead stops it before an
    // English continuation verb (e.g. "open Notepad type ..."), so multi-word app
    // names ("Google Chrome") are still captured whole without swallowing the verb.
    private const string OpenVerbLookahead =
        @"(?:then|and|type|write|fill|enter|save|run|build|verify|read|calculate|compute)\b";
    private static readonly Regex OpenApplication = new(
        @"(?i)(?:\bopen\b|\blaunch\b|\bstart\b|\u0E40\u0E1B\u0E34\u0E14)\s+"
        + @"(?<app>[A-Za-z0-9._-]+(?:\s+(?!" + OpenVerbLookahead + @")[A-Za-z0-9._-]+)*)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex DestinationApplication = new(
        @"(?ix)(?:\binto\b|\bin\b|\bto\b|\u0E25\u0E07|\u0E43\u0E19)\s+(?<app>[\p{L}\p{N}._ -]+)$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex QuotedValue = new(
        "[\\\"'\u201C\u201D](?<value>[^\\\"'\u201C\u201D]+)[\\\"'\u201C\u201D]",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public CompiledOutcomeGraph Compile(
        GoalSpec goal,
        string taskId,
        string milestoneId,
        SkillDefinition skill)
    {
        var outcomes = new List<TaskOutcome>();
        var knownApplications = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? previousOutcomeId = null;
        string? lastObservedValueOutcomeId = null;

        foreach (var rawClause in SplitClauses(goal.OriginalUserRequest))
        {
            var clause = rawClause.Trim().Trim(',', '.');
            if (clause.Length == 0) continue;
            var lower = clause.ToLowerInvariant();
            var openMatch = OpenApplication.Match(clause);
            var openedApplication = NormalizeApplication(
                openMatch.Success ? openMatch.Groups["app"].Value : null);
            if (openedApplication is not null)
            {
                var readiness = AddOutcome(
                    outcomes, taskId, goal.Version, OutcomeKind.ApplicationReady,
                    $"{openedApplication} application is available and input-ready",
                    $"A visible {openedApplication} window has a verified input receiver",
                    previousOutcomeId,
                    expectedApplication: openedApplication,
                    verification: ["Window identity and requested readiness are independently observed"],
                    evidence: ["PID", "HWND", "visibility", "foreground or semantic receiver", "readiness level"]);
                previousOutcomeId = readiness.OutcomeId;
                knownApplications.Add(openedApplication);

                // The regex no longer requires the app to end the clause, so a single
                // breath like "เปิด Notepad พิมพ์ข้อความ X" carries both an open and a
                // text-entry intent. Strip the matched "open <app>" span and keep
                // processing the remainder (instead of dropping it via continue) when it
                // still describes an actionable calculation / text-entry / file step.
                var remainder = clause.Remove(openMatch.Index, openMatch.Length)
                    .Trim().Trim(',', '.');
                var remainderLower = remainder.ToLowerInvariant();
                if (remainder.Length == 0
                    || !(IsCalculation(remainderLower) || IsTextEntry(remainderLower)
                         || InferKind(remainderLower) != OutcomeKind.Generic))
                    continue;
                clause = remainder;
                lower = remainderLower;
            }

            if (IsCalculation(lower))
            {
                var match = Expression.Match(clause);
                var expression = match.Success ? match.Value : clause;
                var expected = match.Success ? EvaluateExpression(match) : null;
                var namedApplication = ExtractApplication(DestinationApplication, clause);
                previousOutcomeId = EnsureApplicationReady(
                    outcomes, taskId, goal.Version, knownApplications,
                    namedApplication, previousOutcomeId);
                var application = namedApplication ?? knownApplications.LastOrDefault();
                var processed = AddOutcome(
                    outcomes, taskId, goal.Version, OutcomeKind.OperationProcessed,
                    $"The requested expression is processed: {expression}",
                    $"The target application processed the exact expression {expression}",
                    previousOutcomeId,
                    expectedApplication: application,
                    expectedValue: expected ?? expression,
                    verification: ["The full expression effect is observed on the verified application receiver"],
                    evidence: ["receiver identity", "exact resulting application state"]);
                var observed = AddOutcome(
                    outcomes, taskId, goal.Version, OutcomeKind.StateObserved,
                    expected is null
                        ? "The application result is independently read"
                        : $"The application display is independently read and equals {expected}",
                    expected is null
                        ? "The displayed result is read from application state"
                        : $"The displayed result equals {expected}",
                    processed.OutcomeId,
                    expectedApplication: application,
                    expectedValue: expected,
                    verification: ["Read result from UIA, DOM, application API, or scoped visual observation"],
                    evidence: ["independent display value", "receiver identity"]);
                previousOutcomeId = observed.OutcomeId;
                lastObservedValueOutcomeId = observed.OutcomeId;
                continue;
            }

            if (IsTextEntry(lower))
            {
                var application = ExtractApplication(DestinationApplication, clause);
                if (application is not null && !knownApplications.Contains(application))
                {
                    var readiness = AddOutcome(
                        outcomes, taskId, goal.Version, OutcomeKind.ApplicationReady,
                        $"{application} editor is available and input-ready",
                        $"A visible {application} editor has a verified input receiver",
                        previousOutcomeId,
                        expectedApplication: application,
                        verification: ["Editor window and writable target control are independently observed"],
                        evidence: ["PID", "HWND", "target control identity", "InputReady"]);
                    previousOutcomeId = readiness.OutcomeId;
                    knownApplications.Add(application);
                }

                var quoted = QuotedValue.Match(clause);
                var explicitValue = quoted.Success ? quoted.Groups["value"].Value : null;
                var expectedValue = explicitValue
                    ?? (lastObservedValueOutcomeId is null ? null : $"outcome:{lastObservedValueOutcomeId}");
                var target = application ?? knownApplications.LastOrDefault();
                var textOutcome = AddOutcome(
                    outcomes, taskId, goal.Version, OutcomeKind.TextValue,
                    target is null
                        ? "The target field contains the exact intended text"
                        : $"{target} contains the exact intended result text",
                    expectedValue is null
                        ? "The intended text is present in the verified target control"
                        : $"The verified target control value exactly matches {expectedValue}",
                    previousOutcomeId,
                    expectedApplication: target,
                    expectedValue: expectedValue,
                    verification: ["Read back the exact value from the same bound control"],
                    evidence: ["HWND/PID", "control identity", "exact value read-back"]);
                previousOutcomeId = textOutcome.OutcomeId;
                continue;
            }

            // "verify both/all/them" is a requirement over outcomes already in the
            // authoritative plan, not a new side effect. Creating a generic action for
            // it leaves a run permanently pending even after every concrete outcome has
            // independent evidence.
            if (IsReferentialVerification(lower) && outcomes.Count > 0)
            {
                foreach (var prior in outcomes.Where(item => item.Required))
                {
                    if (!prior.VerificationRequirements.Contains(
                            "Retain independent evidence through final run completion.",
                            StringComparer.Ordinal))
                        prior.VerificationRequirements.Add(
                            "Retain independent evidence through final run completion.");
                }
                continue;
            }

            var generic = AddOutcome(
                outcomes, taskId, goal.Version, InferKind(lower),
                clause,
                $"Verified completion of: {clause}",
                previousOutcomeId,
                verification: ["Independent observation of the intended state"],
                evidence: ["structured state evidence linked to this outcome"]);
            previousOutcomeId = generic.OutcomeId;
        }

        if (outcomes.Count == 0)
            outcomes.Add(AddOutcome(
                [], taskId, goal.Version, OutcomeKind.Generic,
                goal.NormalizedGoal, goal.DesiredOutcome, null,
                verification: ["Independent observation of the intended state"],
                evidence: ["structured state evidence linked to this outcome"]));

        var steps = BuildSteps(outcomes, taskId, milestoneId, goal, skill);
        var dod = outcomes.Where(outcome => outcome.Required && !outcome.Prohibited)
            .Select(outcome => new DefinitionOfDoneItem
            {
                Id = $"{goal.GoalId}_dod_{outcome.OutcomeId}",
                OutcomeId = outcome.OutcomeId,
                Description = outcome.IntendedState,
                VerificationType = "OutcomeEvidence",
                RequiredEvidence = string.Join("; ", outcome.EvidenceRequirements),
                Required = true
            }).ToList();
        foreach (var existing in goal.DefinitionOfDone.Where(item =>
                     item.VerificationType is not "IndependentStateObservation"
                         and not "IndependentVerification" and not "WorldState"))
            dod.Add(existing);
        return new CompiledOutcomeGraph(outcomes, steps, dod);
    }

    private static IReadOnlyList<string> SplitClauses(string request) =>
        ClauseSeparator.Split(request)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .ToList();

    private static TaskOutcome AddOutcome(
        ICollection<TaskOutcome> target,
        string taskId,
        int goalVersion,
        OutcomeKind kind,
        string description,
        string intendedState,
        string? dependency,
        string? expectedApplication = null,
        string? expectedValue = null,
        IReadOnlyList<string>? verification = null,
        IReadOnlyList<string>? evidence = null)
    {
        var outcome = new TaskOutcome
        {
            OutcomeId = $"outcome_{Guid.NewGuid():N}",
            TaskId = taskId,
            Description = description,
            IntendedState = intendedState,
            Dependencies = dependency is null ? [] : [dependency],
            Kind = kind,
            Required = true,
            VerificationRequirements = verification?.ToList() ?? [],
            EvidenceRequirements = evidence?.ToList() ?? [],
            SourceGoalVersion = goalVersion,
            ExpectedApplication = expectedApplication,
            ExpectedValue = expectedValue,
            Status = dependency is null ? OutcomeStatus.Ready : OutcomeStatus.Pending
        };
        target.Add(outcome);
        return outcome;
    }

    private static List<TaskStep> BuildSteps(
        IReadOnlyList<TaskOutcome> outcomes,
        string taskId,
        string milestoneId,
        GoalSpec goal,
        SkillDefinition skill)
    {
        var stepIds = outcomes.ToDictionary(
            outcome => outcome.OutcomeId,
            _ => $"step_{Guid.NewGuid():N}",
            StringComparer.Ordinal);
        return outcomes.Select(outcome => new TaskStep
        {
            StepId = stepIds[outcome.OutcomeId],
            OutcomeId = outcome.OutcomeId,
            MilestoneId = milestoneId,
            TaskId = taskId,
            Description = outcome.Description,
            IntendedOutcome = outcome.IntendedState,
            Dependencies = outcome.Dependencies.Select(id => stepIds[id]).ToList(),
            Preconditions = skill.Preconditions.ToList(),
            PreferredCapabilities = CapabilitiesFor(outcome),
            ExpectedEffects = [outcome.IntendedState],
            VerificationPlan = string.Join("; ", outcome.VerificationRequirements),
            RiskLevel = goal.RiskLevel,
            SideEffectClass = outcome.Kind == OutcomeKind.StateObserved
                ? SideEffectClass.ReadOnly
                : AgentSafetyPolicy.ClassifyText(outcome.Description),
            IdempotencyPolicy = "Reconcile the prior outcome before repeating any side effect.",
            RetryPolicy = $"Bounded to {skill.RetryLimit} attempts; every retry requires a new observation or strategy.",
            RollbackOrCompensation = skill.CompensationPolicy,
            Status = outcome.Status == OutcomeStatus.Ready
                ? TaskStepStatus.Ready
                : TaskStepStatus.Pending
        }).ToList();
    }

    // Single source of the per-outcome preferred-capability order (incl. the
    // write_file-first bias for known-content FileState). See CapabilityRouter.
    private static List<string> CapabilitiesFor(TaskOutcome outcome) =>
        CapabilityRouter.RecommendedCapabilities(outcome).ToList();

    private static bool IsCalculation(string lower) =>
        Regex.IsMatch(
            lower,
            @"\b(calculate|compute)\b",
            RegexOptions.CultureInvariant)
        || lower.Contains("\u0E04\u0E33\u0E19\u0E27\u0E13", StringComparison.Ordinal);

    private static bool IsTextEntry(string lower) =>
        Regex.IsMatch(lower, @"\b(write|type|fill|enter)\b", RegexOptions.CultureInvariant)
        || lower.Contains("\u0E40\u0E02\u0E35\u0E22\u0E19", StringComparison.Ordinal)
        || lower.Contains("\u0E1E\u0E34\u0E21\u0E1E\u0E4C", StringComparison.Ordinal)
        || lower.Contains("\u0E01\u0E23\u0E2D\u0E01", StringComparison.Ordinal);

    private static OutcomeKind InferKind(string lower)
    {
        if (Regex.IsMatch(lower, @"\b(browser|website|form|navigate|download)\b"))
            return OutcomeKind.BrowserState;
        if (Regex.IsMatch(lower, @"\b(run|build|command|terminal|shell)\b"))
            return OutcomeKind.ProcessState;
        if (Regex.IsMatch(lower, @"\b(file|folder|directory|save|archive|extract)\b"))
            return OutcomeKind.FileState;
        return OutcomeKind.Generic;
    }

    private static bool IsReferentialVerification(string lower) =>
        Regex.IsMatch(
            lower.Trim(),
            @"^(?:verify|confirm|check)\s+(?:both|all|them|those|the\s+above)$",
            RegexOptions.CultureInvariant)
        || Regex.IsMatch(
            lower.Trim(),
            @"^(?:ตรวจสอบ|ยืนยัน)(?:ทั้งคู่|ทั้งหมด|สิ่งเหล่านั้น)$",
            RegexOptions.CultureInvariant);

    private static string? ExtractApplication(Regex pattern, string clause)
    {
        var match = pattern.Match(clause.Trim());
        return match.Success ? NormalizeApplication(match.Groups["app"].Value) : null;
    }

    // Guarantees an ApplicationReady precondition exists for a named desktop app before
    // an operation/text/file outcome that targets it — so the plan is deterministic no
    // matter how the sentence is phrased. Returns the dependency id the following outcome
    // should chain from (the new readiness outcome, or the unchanged previous one).
    private static string? EnsureApplicationReady(
        ICollection<TaskOutcome> outcomes,
        string taskId,
        int goalVersion,
        ISet<string> knownApplications,
        string? application,
        string? previousOutcomeId)
    {
        if (application is null || knownApplications.Contains(application))
            return previousOutcomeId;
        var readiness = AddOutcome(
            outcomes, taskId, goalVersion, OutcomeKind.ApplicationReady,
            $"{application} application is available and input-ready",
            $"A visible {application} window has a verified input receiver",
            previousOutcomeId,
            expectedApplication: application,
            verification: ["Window identity and requested readiness are independently observed"],
            evidence: ["PID", "HWND", "visibility", "foreground or semantic receiver", "readiness level"]);
        knownApplications.Add(application);
        return readiness.OutcomeId;
    }

    private static string? NormalizeApplication(string? rawApp)
    {
        if (string.IsNullOrWhiteSpace(rawApp)) return null;
        var value = rawApp.Trim().TrimEnd('.', ',');
        value = Regex.Replace(value,
            @"(?i)\s+(?:application|app|window|editor)$", string.Empty).Trim();
        return value.Length == 0 ? null : value;
    }

    private static string? EvaluateExpression(Match match)
    {
        if (!decimal.TryParse(match.Groups["left"].Value, NumberStyles.Number,
                CultureInfo.InvariantCulture, out var left)
            || !decimal.TryParse(match.Groups["right"].Value, NumberStyles.Number,
                CultureInfo.InvariantCulture, out var right))
            return null;
        decimal result = match.Groups["operator"].Value switch
        {
            "x" or "X" or "\u00D7" or "*" => left * right,
            "+" => left + right,
            "-" => left - right,
            "/" when right != 0 => left / right,
            _ => decimal.MinValue
        };
        return result == decimal.MinValue
            ? null
            : result.ToString("0.############################", CultureInfo.InvariantCulture);
    }
}

public sealed record PlanCoverageResult(
    bool Accepted,
    string? FailureCode,
    string Reason,
    IReadOnlyList<string> MissingOutcomeIds);

public sealed class PlanCoverageValidator
{
    public PlanCoverageResult Validate(
        GoalSpec goal,
        TaskPlan? previous,
        IReadOnlyList<TaskOutcome> outcomes,
        IReadOnlyList<TaskStep> steps,
        IReadOnlyList<string> constraints)
    {
        if (!goal.Constraints.All(constraints.Contains))
            return Reject("PlanDrift", "The proposal does not preserve every goal constraint.");

        var outcomeIds = outcomes.Select(outcome => outcome.OutcomeId)
            .ToHashSet(StringComparer.Ordinal);
        if (previous is not null)
        {
            var verified = previous.Outcomes
                .Where(outcome => outcome.Status == OutcomeStatus.Verified)
                .Select(outcome => outcome.OutcomeId)
                .ToHashSet(StringComparer.Ordinal);
            if (!verified.IsSubsetOf(outcomeIds))
                return Reject("PlanDrift", "A verified outcome cannot be removed by plan revision.");
        }

        var mapped = steps.Where(step => !string.IsNullOrWhiteSpace(step.OutcomeId))
            .Select(step => step.OutcomeId!)
            .ToHashSet(StringComparer.Ordinal);
        var missing = outcomes.Where(outcome => outcome.Required && !outcome.Prohibited)
            .Where(outcome => !mapped.Contains(outcome.OutcomeId))
            .Select(outcome => outcome.OutcomeId)
            .ToList();
        if (missing.Count > 0)
            return Reject("GoalCoverageFailure",
                "The proposal omits one or more required outcomes.", missing);

        if (outcomes.Any(outcome => outcome.Dependencies.Any(id => !outcomeIds.Contains(id))))
            return Reject("GoalCoverageFailure", "An outcome dependency is missing from the plan.");

        if (steps.Any(step => string.IsNullOrWhiteSpace(step.OutcomeId)))
            return Reject("GoalCoverageFailure", "Every executable step must link one OutcomeId.");

        return new PlanCoverageResult(true, null,
            "All required outcomes, dependencies, Definition of Done items, and constraints are covered.", []);
    }

    public static bool SemanticallyMatches(TaskOutcome outcome, string proposedStep)
    {
        var value = proposedStep.ToLowerInvariant();
        if (outcome.ExpectedApplication is { Length: > 0 } application
            && !value.Contains(application, StringComparison.OrdinalIgnoreCase))
            return false;
        var tokens = Tokenize(outcome.Description + " " + outcome.IntendedState);
        var proposed = Tokenize(proposedStep);
        if (outcome.Kind == OutcomeKind.ApplicationReady)
            return proposed.Overlaps(["open", "launch", "ready", "focus", "window", "editor", "application"])
                   || proposedStep.Contains("\u0E40\u0E1B\u0E34\u0E14", StringComparison.Ordinal);
        if (outcome.Kind == OutcomeKind.OperationProcessed)
            return proposed.Overlaps(["calculate", "compute", "expression", "process"])
                   || proposedStep.Contains("\u0E04\u0E33\u0E19\u0E27\u0E13", StringComparison.Ordinal);
        if (outcome.Kind == OutcomeKind.StateObserved)
            return proposed.Overlaps(["read", "verify", "result", "display", "check"])
                   || proposedStep.Contains("\u0E15\u0E23\u0E27\u0E08", StringComparison.Ordinal);
        if (outcome.Kind == OutcomeKind.TextValue)
            return proposed.Overlaps(["write", "type", "fill", "enter", "contains", "text", "result"])
                   || proposedStep.Contains("\u0E40\u0E02\u0E35\u0E22\u0E19", StringComparison.Ordinal)
                   || proposedStep.Contains("\u0E1E\u0E34\u0E21\u0E1E\u0E4C", StringComparison.Ordinal);
        return tokens.Intersect(proposed).Count() >= Math.Min(2, tokens.Count);
    }

    private static HashSet<string> Tokenize(string value) =>
        Regex.Matches(value.ToLowerInvariant(), @"[\p{L}\p{N}]+")
            .Select(match => match.Value)
            .Where(token => token.Length > 2)
            .Where(token => token is not "the" and not "and" and not "with" and not "from" and not "into")
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

    private static PlanCoverageResult Reject(
        string code,
        string reason,
        IReadOnlyList<string>? missing = null) =>
        new(false, code, reason, missing ?? []);
}

public sealed class SemanticOutcomeMatcher
{
    public OutcomeMatchResult Match(
        TaskOutcome outcome,
        TaskStep step,
        TaskAction action,
        ToolResult raw,
        bool toolReportedVerified)
    {
        if (!string.Equals(step.OutcomeId, outcome.OutcomeId, StringComparison.Ordinal)
            || !string.Equals(action.OutcomeId, outcome.OutcomeId, StringComparison.Ordinal))
            return Failed("OutcomeCorrelationMismatch",
                "The action, step, and evidence are not correlated to the same outcome.");
        if (!toolReportedVerified)
            return Failed("EffectNotObserved",
                "Native execution did not include independent effect verification.");
        if (string.Equals(action.ToolName, "update_plan", StringComparison.Ordinal))
            return Failed("PlanProposalIsNotEvidence", "A plan proposal cannot satisfy an outcome.");

        var evidence = raw.Effect?.Evidence
            ?? raw.ComputerAction?.Evidence
            ?? Array.Empty<string>();
        if (action.ToolName.StartsWith("mock_", StringComparison.Ordinal)
            || action.ToolName.StartsWith("fake_", StringComparison.Ordinal))
            return new OutcomeMatchResult(true, null,
                "The injected deterministic test adapter verified the intended outcome.", evidence);
        // searchable (evidence + free-text content) may be used ONLY for hints that are
        // safe to echo (window identity, receiver flags) — NEVER for verifying a text
        // value, because content echoes the intended text (args/intent) and would make a
        // value appear "matched" without a real read. Actual value verification uses
        // evidenceOnly + structured read-back tokens (T4).
        var searchable = string.Join("\n", evidence.Append(raw.Content));
        var evidenceOnly = string.Join("\n", evidence);
        var allowed = outcome.Kind switch
        {
            OutcomeKind.ApplicationReady =>
                action.ToolName is "open_application" or "mock_state_transition"
                && HasIdentityEvidence(searchable)
                && (!outcome.IntendedState.Contains("input receiver", StringComparison.OrdinalIgnoreCase)
                    || searchable.Contains("input_receiver=true", StringComparison.OrdinalIgnoreCase)),
            OutcomeKind.OperationProcessed =>
                (action.ToolName is "uia_click" or "computer_click" or "computer_key"
                    or "browser_click" or "mock_state_transition"
                 || CapabilityRouter.IsObservationTool(action.ToolName))
                && (outcome.ExpectedValue is not { Length: > 0 } operationValue
                    || ContainsExactValue(searchable, operationValue)),
            OutcomeKind.StateObserved =>
                CapabilityRouter.IsObservationTool(action.ToolName)
                || action.ToolName.Contains("read", StringComparison.OrdinalIgnoreCase)
                || action.ToolName is "mock_state_transition",
            OutcomeKind.TextValue =>
                (action.ToolName is "uia_set_value" or "browser_type" or "computer_type"
                    or "write_file" or "mock_state_transition"
                 || CapabilityRouter.IsObservationTool(action.ToolName))
                && HasReadBackEvidence(evidenceOnly, action.ToolName),
            OutcomeKind.ProcessState => action.ToolName == "run_command"
                                        && raw.Effect?.ExitCode == 0,
            OutcomeKind.FileState =>
                action.ToolName is "write_file" or "edit_file" or "move_file" or "read_file",
            OutcomeKind.BrowserState => action.ToolName.StartsWith("browser_", StringComparison.Ordinal),
            // Generic is the fallback kind for a goal the interpreter could not
            // classify — which, for a Thai or otherwise unrecognised phrasing, is
            // most informational requests. It used to accept ANY capability, so
            // the first tool that happened to succeed closed the goal: a failed
            // `docker ps` followed by a successful "check open windows" marked
            // "show me what docker is running" satisfied. A capability that
            // answered a different question is not evidence for this one, so the
            // evidence has to at least be about the thing that was asked.
            // Generic is what a request compiles to when InferKind's keyword table
            // does not recognise it — Thai, Japanese, and plenty of ordinary
            // English ("list running docker containers" matches none of them).
            // It cannot be tightened by capability here: the unknown bucket holds
            // observation intents AND action intents, so any capability rule
            // strict enough to exclude an unrelated observation also excludes the
            // uia_click / run_command that legitimately satisfied the goal
            // (measured: doing so failed 5 runtime tests). Strictness for this
            // kind has to come from classifying the goal properly, not from
            // guessing harder at verify time.
            //
            // What IS enforceable without knowing the kind is relevance: whatever
            // the capability was, its structured evidence has to be about the
            // thing that was asked. That admits any tool which actually did the
            // work and rejects one that answered a different question, which is
            // the distinction capability filtering could not draw.
            OutcomeKind.Generic => EvidenceMentionsOutcomeSubject(outcome, evidenceOnly),
            _ => true
        };
        if (!allowed)
            return Failed("EffectOutcomeMismatch",
                $"Capability '{action.ToolName}' cannot verify outcome kind {outcome.Kind}.", evidence);

        // Concrete text/observed value verification (T4 — closes the false-success hole):
        // match ExpectedValue ONLY against values ACTUALLY READ from the control
        // (read_back_value= / observed_value= / display_value= tokens the tools emit only
        // after a real post-state read), never the content blob that echoes intent.
        if (outcome.ExpectedValue is { Length: > 0 } expected
            && !expected.StartsWith("outcome:", StringComparison.Ordinal)
            && outcome.Kind is OutcomeKind.StateObserved or OutcomeKind.TextValue)
        {
            var readValues = ExtractActualReadValues(evidence).ToList();
            if (readValues.Count == 0)
                return Failed("EffectNotObserved",
                    $"No value was read back from the control, so the expected value " +
                    $"'{expected}' cannot be confirmed. Observe the control's real state " +
                    "with read_screen_elements before completion is claimed.", evidence);
            if (!readValues.Any(readValue => ContainsExactValue(readValue, expected)))
                return Failed("EffectValueMismatch",
                    $"The value read from the control did not match the expected value '{expected}'.",
                    evidence);
        }

        // WrongWindow, HWND/PID-primary (verify time): when a coordinate-based
        // computer-use action produced a ComputerActionResult and the outcome
        // names an expected application, the effect is verified only if target
        // acquisition CONFIRMED the window identity — a real HWND and PID on a
        // validated target. The application NAME appearing in the arguments or
        // content is a hint only and is never accepted as proof of the correct
        // window. (UIA/browser tools carry control-identity evidence instead and
        // are not ComputerActionResults, so they are unaffected.)
        if (outcome.ExpectedApplication is { Length: > 0 }
            && raw.ComputerAction is { } computerAction
            && !HasConfirmedWindowIdentity(computerAction))
            return Failed("WrongWindow",
                "The action's target window identity (HWND/PID) was not confirmed by " +
                "resolution; a matching application name is not proof of the correct window.",
                evidence);

        return new OutcomeMatchResult(true, null,
            "The independently observed effect matches the intended outcome.", evidence);
    }

    /// <summary>True only when target acquisition confirmed a concrete window
    /// identity for the action: a validated target with a real (non-zero) HWND
    /// and PID. This is the authoritative signal for WrongWindow, replacing any
    /// reliance on the application name appearing in the arguments.</summary>
    private static bool HasConfirmedWindowIdentity(ComputerActionResult action) =>
        action.TargetValidated
        && action.WindowHandle is { } hwnd && hwnd != 0
        && action.ProcessId is { } pid && pid != 0;

    private static bool HasIdentityEvidence(string value) =>
        value.Contains("hwnd=", StringComparison.OrdinalIgnoreCase)
        && value.Contains("pid=", StringComparison.OrdinalIgnoreCase)
        || value.Contains("window", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// Whether the evidence is about the thing the outcome names. Used only for
    /// <see cref="OutcomeKind.Generic"/>, the fallback kind for a goal the
    /// interpreter could not classify — which, for Thai and other unrecognised
    /// phrasings, is most informational requests.
    ///
    /// The check applies only once the step has already failed, and that
    /// restriction is the whole design. On a clean first attempt the question is
    /// "can this capability satisfy the goal?", and for an unclassified goal the
    /// honest answer is that the runtime does not know, so it stays permissive:
    /// requiring the request's nouns to reappear in the output rejects plenty of
    /// real work ("inspect the computer state" is answered by a window list that
    /// never says "computer"). After a failure the question is different and much
    /// sharper — "did THIS capability recover the work that failed?" — and there
    /// a capability whose output never mentions the subject is answering some
    /// other question. That is what let a successful "check open windows" close
    /// "show me what docker is running" after `docker ps` had failed.
    ///
    /// Still deliberately weak: it asks whether the subject appears at all, not
    /// whether the answer is correct. A matcher that rejects real work is worse
    /// than one that occasionally accepts thin work, because the run must clear
    /// every other gate anyway.
    /// </summary>
    /// <summary>
    /// For an unclassified goal, the only thing that makes a capability's result
    /// evidence FOR THIS goal is that it is about what was asked.
    ///
    /// <para>Two earlier versions of this let a false success through, and both
    /// mistakes are worth naming because they look reasonable:</para>
    ///
    /// <para><b>It only ran on retries.</b> The rule was gated on
    /// <c>step.AttemptCount &gt; 1 &amp;&amp; step.FailureCode is not null</c>, which
    /// scopes it to "a capability that did not do the failed work cannot be
    /// credited with having recovered it". But the live defect was not a
    /// recovery: `docker ps` failed on its own step, then a *separate*
    /// first-attempt step ("check open windows") closed the goal. On attempt 1
    /// with no failure code the rule returned true and never looked.</para>
    ///
    /// <para><b>It searched the content blob.</b> It was passed
    /// <c>searchable</c>, which appends <c>raw.Content</c> — the tool's own
    /// output. The comment forty lines above says content must never be used to
    /// verify, because it echoes intent; this did it anyway. The result was
    /// environment-dependent in the worst way: "check open windows" listed the
    /// user's open windows, one of which was <i>Docker Desktop</i>, so the token
    /// "docker" appeared and the goal "show me what docker is running" was
    /// marked satisfied — verified by the fact that the user happened to have
    /// the GUI open. Closing the app would have changed the verdict.</para>
    ///
    /// <para>So: run on every action, and read structured evidence only.</para>
    /// </summary>
    private static bool EvidenceMentionsOutcomeSubject(
        TaskOutcome outcome,
        string evidenceOnly)
    {
        var subject = SubjectTokens($"{outcome.Description} {outcome.IntendedState}");
        if (subject.Count == 0)
            return true;
        var observed = SubjectTokens(evidenceOnly);
        return subject.Any(token => observed.Any(seen => SameSubject(token, seen)));
    }

    /// <summary>Content words of a phrase: the verbs that every request shares
    /// ("check", "show", "ดู") say nothing about what was asked, so they cannot
    /// be what makes evidence relevant.</summary>
    private static HashSet<string> SubjectTokens(string value) =>
        Regex.Matches(value.ToLowerInvariant(), @"[\p{L}\p{N}]+")
            .Select(match => match.Value)
            .Where(token => token.Length > 2)
            .Where(token => !RequestVerbs.Contains(token))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

    private static readonly HashSet<string> RequestVerbs = new(StringComparer.OrdinalIgnoreCase)
    {
        "the", "and", "with", "from", "into", "that", "this", "for", "any", "all",
        "check", "show", "list", "see", "get", "read", "find", "display", "tell",
        "look", "view", "print", "report", "give", "know", "what", "which", "please",
        "state", "status", "current", "currently", "running", "there", "here",
        "ดู", "เช็ค", "ตรวจ", "บอก", "แสดง", "หน่อย", "อะไร", "บ้าง", "ที่", "ว่า", "มี", "อยู่"
    };

    /// <summary>Singular/plural tolerance, so "containers" in the request matches
    /// "CONTAINER" in the output. Full stemming would be more accurate and much
    /// more surprising when it fired.</summary>
    private static bool SameSubject(string left, string right)
    {
        var a = left.TrimEnd('s');
        var b = right.TrimEnd('s');
        return a.Length >= 3 && b.Length >= 3
            && string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Structured evidence tokens that carry a value ACTUALLY READ back from a
    /// control/display (post-state), as opposed to intent/args echoed into free text.
    /// Only these may satisfy a concrete text/observed ExpectedValue (T4).</summary>
    private static readonly string[] ActualReadTokens =
        { "read_back_value=", "observed_value=", "display_value=" };

    /// <summary>Pulls the ACTUAL read-back value(s) out of the structured evidence list
    /// (never the free-text content). Each evidence item like "read_back_value=6000"
    /// yields "6000". This is the single source the ExpectedValue check trusts. Exposed
    /// so the durable runtime's final completion invariant (T5) reuses the same rule.</summary>
    internal static IEnumerable<string> ExtractActualReadValues(IReadOnlyList<string> evidence)
    {
        foreach (var item in evidence)
        {
            foreach (var token in ActualReadTokens)
            {
                var idx = item.IndexOf(token, StringComparison.OrdinalIgnoreCase);
                if (idx < 0) continue;
                var value = item[(idx + token.Length)..].Trim();
                if (value.Length > 0)
                    yield return value;
            }
        }
    }

    // TextValue "allowed" gate: the verifying evidence must carry a REAL read-back token
    // (a value actually read from the control) or a file hash. Deliberately NOT satisfied
    // by the word "exact", a bare hwnd, or an intent echo — those are the false-success
    // hole (T4). The separate ExpectedValue gate then confirms the read value matches.
    private static bool HasReadBackEvidence(string evidenceText, string toolName) =>
        toolName == "mock_state_transition"
        || ActualReadTokens.Any(token =>
            evidenceText.Contains(token, StringComparison.OrdinalIgnoreCase))
        || evidenceText.Contains("hash=", StringComparison.OrdinalIgnoreCase);

    /// <summary>True when the structured <paramref name="evidence"/> carries at least one
    /// ACTUAL read-back value that matches <paramref name="expected"/> — the exact rule
    /// the matcher's ExpectedValue gate uses, exposed for the durable runtime's final
    /// completion invariant (T5) so both paths agree and neither trusts an intent echo.</summary>
    internal static bool EvidenceConfirmsValue(IReadOnlyList<string> evidence, string expected) =>
        ExtractActualReadValues(evidence).Any(readValue => ContainsExactValue(readValue, expected));

    private static bool ContainsExactValue(string value, string expected)
    {
        if (Regex.IsMatch(value,
                $@"(?<![\p{{L}}\p{{N}}]){Regex.Escape(expected)}(?![\p{{L}}\p{{N}}])",
                RegexOptions.CultureInvariant))
            return true;
        if (!decimal.TryParse(expected, NumberStyles.Number,
                CultureInfo.InvariantCulture, out var expectedNumber))
            return false;
        return Regex.Matches(value, @"-?\d[\d,]*(?:\.\d+)?", RegexOptions.CultureInvariant)
            .Select(match => match.Value)
            .Any(candidate => decimal.TryParse(candidate, NumberStyles.Number,
                                  CultureInfo.InvariantCulture, out var actual)
                              && actual == expectedNumber);
    }

    private static OutcomeMatchResult Failed(
        string code,
        string reason,
        IReadOnlyList<string>? evidence = null) =>
        new(false, code, reason, evidence ?? []);
}
