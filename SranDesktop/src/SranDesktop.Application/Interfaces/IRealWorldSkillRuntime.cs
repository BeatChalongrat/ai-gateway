using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Interfaces;

public interface ISkillRegistry
{
    IReadOnlyList<SkillDefinition> GetSkills();
    SkillDefinition? GetSkill(string skillId);
}

public interface ISkill
{
    SkillDefinition Definition { get; }
    SkillActionDecision ValidateAction(
        SkillExecutionContext context,
        string toolName,
        string argumentsJson);
}

public interface ISkillExecutor
{
    SkillActionDecision PrepareAction(
        SkillDefinition skill,
        SkillExecutionContext context,
        string toolName,
        string argumentsJson);
}

public interface ISkillResolver
{
    SkillResolution Resolve(GoalSpec goal, string taskId, WorldStateSnapshot? worldState);
}

public interface ICapabilityInventory
{
    IReadOnlyList<CapabilityHealth> GetAll();
    CapabilityHealth? Get(string capabilityId);
    void Synchronize(IEnumerable<(string Name, bool VerificationSupport)> capabilities);
    CapabilityHealth RecordResult(
        string capabilityId, bool succeeded, bool verified, long latencyMs, string? failure);
}

public interface IEnvironmentObserver
{
    Task<IReadOnlyList<EnvironmentFact>> ObserveAsync(
        GoalSpec goal,
        SkillDefinition skill,
        CancellationToken ct = default);
}

public interface ISkillVerifier
{
    SkillExecutionResult Verify(
        SkillDefinition skill,
        AgentTask task,
        TaskPlan plan,
        IReadOnlyList<TaskEvidence> evidence);
}

public interface ICompensationCoordinator
{
    CompensationLedgerEntry Plan(
        TaskAction action,
        string target,
        string beforeState,
        string afterState,
        string? compensationAction);
    CompensationLedgerEntry ValidateForExecution(
        CompensationLedgerEntry entry,
        string currentState);
}
