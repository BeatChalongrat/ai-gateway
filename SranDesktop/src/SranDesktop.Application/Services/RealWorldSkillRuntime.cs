using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed class RealWorldSkillRuntime :
    ISkillRegistry, ISkillResolver, ICapabilityInventory, IEnvironmentObserver,
    ISkillVerifier, ICompensationCoordinator, ISkillExecutor
{
    private readonly IReadOnlyList<SkillDefinition> _skills = BuiltInSkills();
    private readonly ConcurrentDictionary<string, CapabilityHealth> _capabilities =
        new(StringComparer.Ordinal);

    public RealWorldSkillRuntime()
    {
        Synchronize(new[]
        {
            ("read_file", true), ("write_file", true), ("edit_file", true),
            ("move_file", true), ("list_directory", true), ("search_files", true),
            ("run_command", true), ("open_application", true),
            ("browser_read_page", true), ("browser_navigate", true),
            ("browser_click", true), ("browser_type", true),
            ("browser_upload", true), ("browser_download", true),
            ("read_screen_elements", true), ("uia_click", true), ("uia_set_value", true),
            ("computer_screenshot", true), ("computer_click", true),
            ("computer_read_text", true),
            ("computer_type", true), ("computer_key", true),
            ("computer_scroll", true), ("computer_drag", true),
            ("generate_image", true), ("generate_video", true)
        });
    }

    public IReadOnlyList<SkillDefinition> GetSkills() => _skills;

    public SkillDefinition? GetSkill(string skillId) =>
        _skills.FirstOrDefault(skill =>
            string.Equals(skill.SkillId, skillId, StringComparison.Ordinal));

    public SkillResolution Resolve(
        GoalSpec goal,
        string taskId,
        WorldStateSnapshot? worldState)
    {
        var scores = _skills
            .Select(skill => (Skill: skill, Score: Score(goal, skill, worldState)))
            .OrderByDescending(item => item.Score)
            .ThenBy(item => item.Skill.SkillId, StringComparer.Ordinal)
            .ToList();
        var first = scores[0];
        var second = scores.Count > 1 ? scores[1].Score : 0;
        var gap = first.Score - second;
        var ambiguous = first.Score < 0.45 || gap < 0.08;
        return new SkillResolution
        {
            ResolutionId = $"skill_resolution_{Guid.NewGuid():N}",
            TaskId = taskId,
            Skill = first.Skill,
            Score = first.Score,
            ScoreGap = gap,
            Ambiguous = ambiguous,
            CandidateSkillIds = scores.Take(3).Select(item => item.Skill.SkillId).ToList(),
            Reason = ambiguous
                ? "The leading skills do not have a sufficient evidence-weighted score gap."
                : "Selected using intent, Definition of Done, risk, environment, and capability support.",
            ResolvedAtUtc = DateTimeOffset.UtcNow
        };
    }

    public IReadOnlyList<CapabilityHealth> GetAll() =>
        _capabilities.Values.OrderBy(value => value.CapabilityId, StringComparer.Ordinal).ToList();

    public CapabilityHealth? Get(string capabilityId) =>
        _capabilities.TryGetValue(capabilityId, out var value) ? value : null;

    public void Synchronize(IEnumerable<(string Name, bool VerificationSupport)> capabilities)
    {
        foreach (var (name, verificationSupport) in capabilities)
            _capabilities.AddOrUpdate(
                name,
                _ => NewCapability(name, verificationSupport),
                (_, existing) => existing with
                {
                    Availability = CapabilityAvailability.Available,
                    VerificationSupport = existing.VerificationSupport || verificationSupport,
                    UpdatedAtUtc = DateTimeOffset.UtcNow
                });
    }

    public CapabilityHealth RecordResult(
        string capabilityId,
        bool succeeded,
        bool verified,
        long latencyMs,
        string? failure)
    {
        return _capabilities.AddOrUpdate(
            capabilityId,
            _ =>
            {
                var created = NewCapability(capabilityId, verified);
                return UpdateHealth(created, succeeded, verified, latencyMs, failure);
            },
            (_, existing) => UpdateHealth(existing, succeeded, verified, latencyMs, failure));
    }

    public Task<IReadOnlyList<EnvironmentFact>> ObserveAsync(
        GoalSpec goal,
        SkillDefinition skill,
        CancellationToken ct = default)
    {
        ct.ThrowIfCancellationRequested();
        var now = DateTimeOffset.UtcNow;
        var facts = new List<EnvironmentFact>
        {
            Fact("os", RuntimeInformation.OSDescription, "RuntimeInformation", now, TimeSpan.FromHours(12)),
            Fact("architecture", RuntimeInformation.OSArchitecture.ToString(), "RuntimeInformation", now, TimeSpan.FromHours(12)),
            Fact("process_architecture", RuntimeInformation.ProcessArchitecture.ToString(), "RuntimeInformation", now, TimeSpan.FromHours(12)),
            Fact("locale", System.Globalization.CultureInfo.CurrentCulture.Name, "CultureInfo", now, TimeSpan.FromHours(12)),
            Fact("timezone", TimeZoneInfo.Local.Id, "TimeZoneInfo", now, TimeSpan.FromHours(12))
        };
        if (skill.SkillId is "project_engineering" or "file_organization" or "git_workflow")
        {
            var cwd = Environment.CurrentDirectory;
            facts.Add(Fact("working_directory", cwd, "Environment.CurrentDirectory", now, TimeSpan.FromMinutes(5)));
            facts.Add(Fact("working_directory_exists", Directory.Exists(cwd).ToString(),
                "Directory.Exists", now, TimeSpan.FromMinutes(1)));
        }
        if (skill.SkillId == "software_installation")
        {
            var root = Path.GetPathRoot(Environment.SystemDirectory) ?? "unknown";
            var available = root == "unknown" ? "unknown"
                : new DriveInfo(root).AvailableFreeSpace.ToString();
            facts.Add(Fact("system_drive_free_bytes", available, "DriveInfo", now, TimeSpan.FromMinutes(5)));
        }
        return Task.FromResult<IReadOnlyList<EnvironmentFact>>(facts);
    }

    public SkillExecutionResult Verify(
        SkillDefinition skill,
        AgentTask task,
        TaskPlan plan,
        IReadOnlyList<TaskEvidence> evidence)
    {
        var steps = plan.Milestones.SelectMany(milestone => milestone.Steps).ToList();
        var completed = steps.Count > 0 && steps.All(step =>
            step.Status is TaskStepStatus.Verified or TaskStepStatus.Skipped);
        var verified = completed && evidence.Any(item => item.Confidence >= 0.9);
        return new SkillExecutionResult
        {
            SkillId = skill.SkillId,
            SkillVersion = skill.Version,
            ExecutionId = task.SkillExecutionId ?? $"skill_execution_{task.TaskId}",
            Started = task.Status is not AgentTaskStatus.Draft,
            Completed = completed,
            Verified = verified,
            ProducedArtifacts = evidence
                .Where(item => item.ArtifactReference is not null)
                .Select(item => item.ArtifactReference!)
                .Distinct(StringComparer.Ordinal)
                .ToList(),
            EvidenceIds = evidence.Select(item => item.EvidenceId).ToList(),
            FailureCode = completed && !verified ? "SkillEvidenceMissing" : null,
            FailureReason = completed && !verified
                ? "All skill steps ended, but no independent high-confidence evidence was persisted."
                : null,
            Retryable = !completed,
            ResumeToken = $"task:{task.TaskId}:checkpoint:{task.CheckpointVersion}"
        };
    }

    public SkillActionDecision PrepareAction(
        SkillDefinition skill,
        SkillExecutionContext context,
        string toolName,
        string argumentsJson)
    {
        var lower = argumentsJson.ToLowerInvariant();
        if (skill.SkillId == "software_installation")
        {
            if (toolName is "computer_click" or "uia_click"
                && context.AvailableCapabilities.Any(capability =>
                    capability.CapabilityId == "run_command"
                    && capability.Health == CapabilityHealthState.Healthy))
                return Deny(
                    "DeterministicInstallerCapabilityAvailable",
                    "A healthy deterministic package/process capability must be attempted before GUI automation.",
                    ["installed version", "process exit", "package identity"]);
            if (ContainsDownload(lower) && !ReferencesTrustedSource(lower))
                return Deny(
                    "UntrustedInstallationSource",
                    "Installation content may only come from an official, registry, enterprise, or explicitly approved source.",
                    ["source URL", "publisher", "checksum or signature"]);
        }

        if (skill.SkillId == "project_engineering"
            && toolName is "write_file" or "edit_file" or "move_file"
            && ProtectsDependencyFiles(context.Constraints)
            && ReferencesDependencyManifest(lower))
            return Deny(
                "ProtectedDependencyManifest",
                "The persisted task constraint forbids changing dependency manifests or lock files.",
                ["before hash", "after hash", "constraint audit"]);

        if (skill.SkillId == "browser_workflow")
        {
            if (toolName.StartsWith("computer_", StringComparison.Ordinal)
                && context.AvailableCapabilities.Any(capability =>
                    capability.CapabilityId.StartsWith("browser_", StringComparison.Ordinal)
                    && capability.Health != CapabilityHealthState.Unhealthy))
                return Deny(
                    "DomCapabilityPreferred",
                    "A healthy DOM capability is available and must be used before Computer Use.",
                    ["DOM target identity", "DOM state after action"]);
            if (IsConsequentialBrowserAction(toolName, argumentsJson)
                && context.CurrentStep.SideEffectClass < SideEffectClass.Consequential)
                return Deny(
                    "ConsequentialBrowserActionNotPlanned",
                    "A consequential browser submission must be represented as a consequential step before dispatch.",
                    ["review state", "scoped approval", "post-submit state"]);
        }

        if (skill.SkillId == "file_organization"
            && ContainsNoDeleteConstraint(context.Constraints)
            && (ContainsAny(toolName, "delete", "remove")
                || ContainsAny(lower, "\"delete\"", "remove-item", " del ")))
            return Deny(
                "NoDeleteConstraint",
                "The durable goal contains a no-delete invariant.",
                ["inventory before", "inventory after", "constraint audit"]);

        if (skill.SkillId == "git_workflow" && toolName == "run_command")
        {
            if (ContainsAny(lower, "reset --hard", "push --force", "push -f", "clean -fd"))
                return Deny(
                    "DestructiveGitCommandBlocked",
                    "Destructive Git commands are not available through the general Git skill.",
                    ["git status", "git diff"]);
            if (ContainsAny(lower, "git commit", "git push")
                && !ContainsAny(
                    context.Goal.OriginalUserRequest.ToLowerInvariant(),
                    "commit", "push"))
                return Deny(
                    "ExplicitGitMutationRequired",
                    "Commit and push require an explicit user request persisted in the goal.",
                    ["explicit goal", "scoped approval", "git state"]);
        }

        return new SkillActionDecision(
            true,
            null,
            $"Skill '{skill.SkillId}@{skill.Version}' accepted the action.",
            string.Join("; ", skill.VerificationRequirements),
            context.CurrentStep.SideEffectClass >= SideEffectClass.ReversibleMediumRisk,
            skill.Preconditions);
    }

    private static bool IsConsequentialBrowserAction(
        string toolName,
        string argumentsJson)
    {
        if (toolName == "browser_upload")
            return true;
        try
        {
            using var document = JsonDocument.Parse(argumentsJson);
            if (toolName == "browser_type"
                && document.RootElement.TryGetProperty("submit", out var submit)
                && submit.ValueKind == JsonValueKind.True)
                return true;
            return ContainsConsequentialStringValue(document.RootElement);
        }
        catch (JsonException)
        {
            // Invalid action data must not bypass the consequential-action gate.
            return true;
        }
    }

    private static bool ContainsConsequentialStringValue(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.String)
            return ContainsAny(
                element.GetString()?.ToLowerInvariant() ?? "",
                "submit",
                "purchase",
                "send",
                "confirm");
        if (element.ValueKind == JsonValueKind.Object)
            return element.EnumerateObject()
                .Any(property => ContainsConsequentialStringValue(property.Value));
        if (element.ValueKind == JsonValueKind.Array)
            return element.EnumerateArray().Any(ContainsConsequentialStringValue);
        return false;
    }

    public CompensationLedgerEntry Plan(
        TaskAction action,
        string target,
        string beforeState,
        string afterState,
        string? compensationAction) =>
        new()
        {
            CompensationId = $"compensation_{Guid.NewGuid():N}",
            TaskId = action.TaskId,
            StepId = action.StepId,
            ActionId = action.ActionId,
            Target = target,
            BeforeState = beforeState,
            AfterState = afterState,
            Reversible = !string.IsNullOrWhiteSpace(compensationAction),
            CompensationAction = compensationAction,
            Status = string.IsNullOrWhiteSpace(compensationAction)
                ? CompensationStatus.NotReversible
                : CompensationStatus.Ready,
            UpdatedAtUtc = DateTimeOffset.UtcNow
        };

    public CompensationLedgerEntry ValidateForExecution(
        CompensationLedgerEntry entry,
        string currentState)
    {
        if (!entry.Reversible)
            return entry with { Status = CompensationStatus.NotReversible, UpdatedAtUtc = DateTimeOffset.UtcNow };
        if (!string.Equals(currentState, entry.AfterState, StringComparison.Ordinal))
            return entry with { Status = CompensationStatus.Conflict, UpdatedAtUtc = DateTimeOffset.UtcNow };
        return entry with { Status = CompensationStatus.Ready, UpdatedAtUtc = DateTimeOffset.UtcNow };
    }

    public bool PreconditionsSatisfied(
        SkillDefinition skill,
        out IReadOnlyList<string> missing)
    {
        var missingList = skill.RequiredCapabilities
            .Where(required => !_capabilities.Values.Any(capability =>
                capability.Availability != CapabilityAvailability.Unavailable
                && MatchesCapability(required, capability.CapabilityId)))
            .ToList();
        missing = missingList;
        return missingList.Count == 0;
    }

    private static double Score(
        GoalSpec goal,
        SkillDefinition skill,
        WorldStateSnapshot? worldState)
    {
        var text = string.Join(" ", goal.NormalizedGoal, goal.DesiredOutcome,
            string.Join(" ", goal.DefinitionOfDone.Select(item => item.Description)))
            .ToLowerInvariant();
        var intentHits = skill.SupportedIntents.Count(intent =>
            text.Contains(intent, StringComparison.OrdinalIgnoreCase));
        var verificationHits = skill.VerificationRequirements.Count(requirement =>
            goal.DefinitionOfDone.Any(item =>
                item.Description.Contains(requirement, StringComparison.OrdinalIgnoreCase)
                || item.VerificationType.Contains(requirement, StringComparison.OrdinalIgnoreCase)));
        var riskCompatibility = goal.RiskLevel <= skill.RiskLevel ? 1d : 0.4d;
        var environmentFit = skill.SkillId switch
        {
            "project_engineering" when worldState?.CurrentProject is not null => 1d,
            "browser_workflow" when worldState?.CurrentUrl is not null => 1d,
            _ => 0.6d
        };
        return Math.Min(1,
            intentHits * 0.22
            + verificationHits * 0.08
            + riskCompatibility * 0.16
            + environmentFit * 0.08
            + (intentHits > 0 ? 0.3 : 0));
    }

    private static bool MatchesCapability(string required, string actual) =>
        actual.Contains(required, StringComparison.OrdinalIgnoreCase)
        || required.Contains(actual, StringComparison.OrdinalIgnoreCase)
        || required switch
        {
            "shell_process" => actual is "run_command" or "open_application",
            "file_system" => actual is "read_file" or "write_file" or "edit_file"
                or "move_file" or "list_directory" or "search_files",
            "browser_dom" => actual.StartsWith("browser_", StringComparison.Ordinal),
            "windows_uia" => actual.StartsWith("uia_", StringComparison.Ordinal)
                || actual == "read_screen_elements",
            "computer_use" => actual.StartsWith("computer_", StringComparison.Ordinal),
            "media_provider" => actual is "generate_image" or "generate_video",
            "git" => actual == "run_command",
            "package_manager" => actual == "run_command",
            _ => false
        };

    private static SkillActionDecision Deny(
        string code,
        string reason,
        IReadOnlyList<string> observations) =>
        new(false, code, reason, "Independent state verification", false, observations);

    private static bool ContainsDownload(string value) =>
        ContainsAny(value, "http://", "https://", "download", "invoke-webrequest", "curl ");

    private static bool ReferencesTrustedSource(string value) =>
        ContainsAny(value, "official", "winget", "chocolatey", "nuget", "npmjs.org",
            "pypi.org", "github.com", "\"trusted_source\":true");

    private static bool ProtectsDependencyFiles(IEnumerable<string> constraints) =>
        constraints.Any(value =>
            ContainsAny(value.ToLowerInvariant(),
                "dependency", "lock file", "manifest", "package.json",
                "ห้ามเปลี่ยน dependency"));

    private static bool ReferencesDependencyManifest(string value) =>
        ContainsAny(value, "package.json", "package-lock.json", "packages.lock.json",
            "requirements.txt", "pyproject.toml", ".csproj", "global.json");

    private static bool ContainsNoDeleteConstraint(IEnumerable<string> constraints) =>
        constraints.Any(value =>
            ContainsAny(value.ToLowerInvariant(), "do not delete", "must not delete",
                "without deleting", "ห้ามลบ"));

    private static bool ContainsAny(string value, params string[] terms) =>
        terms.Any(value.Contains);

    private static CapabilityHealth NewCapability(string name, bool verificationSupport) =>
        new()
        {
            CapabilityId = name,
            Provider = "mcp-tool-host",
            Availability = CapabilityAvailability.Available,
            Health = CapabilityHealthState.Healthy,
            Risk = AgentSafetyPolicy.InferRisk(name),
            VerificationSupport = verificationSupport,
            RollbackSupport = name is "write_file" or "move_file" or "edit_file",
            RequiresForeground = name.StartsWith("computer_", StringComparison.Ordinal)
                                 || name.StartsWith("uia_", StringComparison.Ordinal),
            RequiresAdmin = false,
            RequiresNetwork = name.StartsWith("browser_", StringComparison.Ordinal)
                              || name is "web_search" or "web_fetch",
            UpdatedAtUtc = DateTimeOffset.UtcNow
        };

    private static CapabilityHealth UpdateHealth(
        CapabilityHealth existing,
        bool succeeded,
        bool verified,
        long latencyMs,
        string? failure)
    {
        var attempts = existing.Attempts + 1;
        var failures = existing.Failures + (succeeded ? 0 : 1);
        var average = existing.Attempts == 0
            ? latencyMs
            : ((existing.AverageLatencyMs * existing.Attempts) + latencyMs) / attempts;
        var failureRate = (double)failures / attempts;
        return existing with
        {
            Attempts = attempts,
            Failures = failures,
            AverageLatencyMs = average,
            LastFailure = succeeded ? null : failure,
            Health = !succeeded && failureRate >= 0.5
                ? CapabilityHealthState.Unhealthy
                : !verified || failureRate >= 0.2
                    ? CapabilityHealthState.Degraded
                    : CapabilityHealthState.Healthy,
            Availability = failureRate >= 0.8 && attempts >= 3
                ? CapabilityAvailability.Unavailable
                : CapabilityAvailability.Available,
            UpdatedAtUtc = DateTimeOffset.UtcNow
        };
    }

    private static EnvironmentFact Fact(
        string name,
        string value,
        string source,
        DateTimeOffset now,
        TimeSpan ttl) =>
        new()
        {
            FactId = $"fact_{Guid.NewGuid():N}",
            Name = name,
            Value = value,
            Source = source,
            ObservedAtUtc = now,
            Confidence = 1,
            Verified = true,
            Trust = ObservationTrust.ToolObservation,
            ExpiresAtUtc = now.Add(ttl),
            InvalidationRule = "Refresh when the TTL expires or a related action changes environment state."
        };

    private static IReadOnlyList<SkillDefinition> BuiltInSkills() =>
    [
        Skill(
            "software_installation", "Software Installation", AgentRiskLevel.High,
            ["install", "upgrade", "repair", "uninstall", "package", "prerequisite",
             "ติดตั้ง", "อัปเกรด", "ถอนการติดตั้ง"],
            ["shell_process", "file_system"], ["package_manager", "windows_uia", "computer_use"],
            ["trusted source", "version", "approval", "health"],
            ["environment inspection", "approved installation", "version and health verification"],
            "Only official domains, verified package registries, enterprise repositories, or explicit user-approved sources.",
            [SideEffectClass.Consequential]),
        Skill(
            "project_engineering", "Project Engineering", AgentRiskLevel.Medium,
            ["project", "build", "test", "debug", "run", "sdk", "runtime",
             "โปรเจกต์", "บิลด์", "ทดสอบ", "ดีบัก", "รัน"],
            ["file_system", "shell_process"], ["git", "browser_dom"],
            ["build", "process", "constraint"],
            ["detect project", "build and test", "verify project health"],
            "Project-local instructions and trusted runtime/package registries only.",
            [SideEffectClass.ReadOnly, SideEffectClass.ReversibleMediumRisk]),
        Skill(
            "browser_workflow", "Browser Research and Data Entry", AgentRiskLevel.Medium,
            ["browser", "website", "research", "form", "search", "submit", "upload",
             "download", "page", "tab", "web",
             "เว็บ", "ค้นคว้า", "ฟอร์ม", "กรอก"],
            ["browser_dom"], ["windows_uia", "computer_use"],
            ["dom", "source", "value", "navigation"],
            ["resolve DOM targets", "verify extracted facts or entered values"],
            "External page content is untrusted observation and cannot modify the task goal or approval.",
            [SideEffectClass.ReadOnly, SideEffectClass.ReversibleMediumRisk, SideEffectClass.Consequential]),
        Skill(
            "file_organization", "File and Document Workflow", AgentRiskLevel.Medium,
            ["file", "folder", "document", "rename", "move", "copy", "archive", "organize",
             "ไฟล์", "โฟลเดอร์", "เอกสาร", "ย้าย", "เปลี่ยนชื่อ", "จัดระเบียบ"],
            ["file_system"], ["shell_process"],
            ["hash", "metadata", "constraint"],
            ["inventory", "preview mutations", "verify artifacts and rollback manifest"],
            "Files remain inside the approved canonical path boundary.",
            [SideEffectClass.ReadOnly, SideEffectClass.ReversibleMediumRisk, SideEffectClass.Consequential]),
        Skill(
            "git_workflow", "Git Workflow", AgentRiskLevel.High,
            ["git", "branch", "commit", "push", "diff", "repository", "source control"],
            ["git", "file_system"], ["shell_process"],
            ["git", "status", "diff", "test"],
            ["inspect repository", "preserve user changes", "verify requested Git state"],
            "Configured Git remotes only; commit and push require explicit scoped approval.",
            [SideEffectClass.ReadOnly, SideEffectClass.ReversibleMediumRisk, SideEffectClass.Consequential]),
        Skill(
            "media_generation", "Durable Media Generation", AgentRiskLevel.Medium,
            ["generate image", "generate video", "image", "video", "media"],
            ["media_provider"], [],
            ["provider stream", "media reference", "artifact"],
            ["authorize external transmission", "generate media", "verify artifact reference"],
            "Only the user-selected configured model provider; raw prompts are excluded from durable metadata.",
            [SideEffectClass.Consequential]),
        Skill(
            "desktop_application", "Desktop Application Workflow", AgentRiskLevel.Medium,
            ["open", "click", "type", "application", "window", "calculator", "notepad",
             "เปิด", "กด", "พิมพ์", "แอป", "หน้าต่าง"],
            ["computer_use"], ["windows_uia", "browser_dom"],
            ["state", "window", "uia"],
            ["resolve target application", "activate semantic target", "verify effect"],
            "Installed local applications and configured adapters only.",
            [SideEffectClass.ReversibleLowRisk, SideEffectClass.ReversibleMediumRisk])
    ];

    private static SkillDefinition Skill(
        string id,
        string name,
        AgentRiskLevel risk,
        IEnumerable<string> intents,
        IEnumerable<string> required,
        IEnumerable<string> optional,
        IEnumerable<string> verification,
        IEnumerable<string> milestones,
        string trustedSources,
        IEnumerable<SideEffectClass> sideEffects) =>
        new()
        {
            SkillId = id,
            Name = name,
            Version = "1.0.0",
            Description = $"{name} executed through the durable goal-plan-state-verification runtime.",
            SupportedIntents = intents.ToList(),
            RequiredCapabilities = required.ToList(),
            OptionalCapabilities = optional.ToList(),
            Preconditions = ["Required capabilities are available and healthy.",
                "Task constraints and approvals are loaded from durable state."],
            RiskLevel = risk,
            SideEffectClasses = sideEffects.ToList(),
            DefaultMilestones = milestones.ToList(),
            VerificationRequirements = verification.ToList(),
            RecoveryPolicy = "Refresh environment state, choose a healthier capability, replan, then stop safely.",
            CompensationPolicy = "Use the persisted side-effect ledger and verify current state before compensation.",
            RequiredApprovals = sideEffects.Any(value => value >= SideEffectClass.Consequential)
                ? ["Scoped action approval"] : [],
            SupportedPlatforms = ["Windows"],
            ProducedArtifacts = ["task evidence", "world-state facts", "verification result"],
            TrustedSourcesPolicy = trustedSources,
            RetryLimit = 3
        };
}
