namespace SranDesktop.Application.Models;

public enum ChildRunState
{
    Pending,
    Running,
    Succeeded,
    Failed,
    TimedOut,
    Cancelled,
    NeedsUser,
    BudgetExhausted
}

/// <summary>
/// What a child agent is allowed to touch.
///
/// Every field is a ceiling, never a grant: a child receives a scope derived
/// from its parent's, and <see cref="IsWithin"/> is what makes "a child cannot
/// broaden permissions" checkable rather than aspirational. A child that asks
/// for more than the parent holds is refused at construction, not audited after
/// the fact.
/// </summary>
public sealed record ChildScope
{
    public IReadOnlyList<string> AllowedTools { get; init; } = [];
    public IReadOnlyList<string> WorkspaceRoots { get; init; } = [];
    public IReadOnlyList<string> ConnectorIds { get; init; } = [];
    public IReadOnlyList<string> AccountKeys { get; init; } = [];

    /// <summary>Read-only children cannot mutate anything, whatever tools they hold.</summary>
    public bool ReadOnly { get; init; } = true;

    /// <summary>True when this scope grants nothing the outer scope lacks.</summary>
    public bool IsWithin(ChildScope outer)
    {
        ArgumentNullException.ThrowIfNull(outer);

        // A read-only parent cannot produce a mutating child. The reverse is fine.
        if (!ReadOnly && outer.ReadOnly)
            return false;

        return IsSubset(AllowedTools, outer.AllowedTools)
               && IsSubset(WorkspaceRoots, outer.WorkspaceRoots)
               && IsSubset(ConnectorIds, outer.ConnectorIds)
               && IsSubset(AccountKeys, outer.AccountKeys);
    }

    private static bool IsSubset(IReadOnlyList<string> inner, IReadOnlyList<string> outer) =>
        inner.All(item => outer.Contains(item, StringComparer.OrdinalIgnoreCase));
}

/// <summary>Everything a child is allowed to spend.</summary>
public sealed record ChildBudget(
    int MaxToolCalls = 20,
    int MaxTokens = 60_000,
    TimeSpan? WallClock = null,
    int MaxRetries = 1)
{
    public TimeSpan Deadline => WallClock ?? TimeSpan.FromMinutes(4);
}

/// <summary>What a child actually spent.</summary>
public sealed record ChildSpend(int ToolCalls = 0, int Tokens = 0, int Retries = 0)
{
    public bool Exceeds(ChildBudget budget) =>
        ToolCalls > budget.MaxToolCalls
        || Tokens > budget.MaxTokens
        || Retries > budget.MaxRetries;
}

/// <summary>A supervised child run.</summary>
public sealed record ChildRun
{
    public required string ChildRunId { get; init; }
    public required string ParentRunId { get; init; }
    public required string Role { get; init; }
    public required string Goal { get; init; }

    public string? Model { get; init; }
    public ChildScope Scope { get; init; } = new();
    public ChildBudget Budget { get; init; } = new();
    public ChildSpend Spend { get; init; } = new();

    /// <summary>How many delegation hops deep this run is. 0 is the top run.</summary>
    public int Depth { get; init; }

    public ChildRunState State { get; init; } = ChildRunState.Pending;
    public string? StateReason { get; init; }

    /// <summary>The child's answer, when it produced one.</summary>
    public string? Result { get; init; }

    /// <summary>Facts the child asserts, each traceable to where it got them.</summary>
    public IReadOnlyList<ChildFinding> Findings { get; init; } = [];

    public DateTimeOffset? StartedUtc { get; init; }
    public DateTimeOffset? CompletedUtc { get; init; }

    public bool IsTerminal => State is ChildRunState.Succeeded or ChildRunState.Failed
        or ChildRunState.TimedOut or ChildRunState.Cancelled
        or ChildRunState.NeedsUser or ChildRunState.BudgetExhausted;

    /// <summary>Work worth keeping even if the child did not finish cleanly.</summary>
    public bool HasUsableWork => Findings.Count > 0 || !string.IsNullOrWhiteSpace(Result);
}

/// <summary>
/// One fact a child reports, with where it came from.
///
/// Provenance is not decoration here: when two children disagree, the parent has
/// nothing else to reason with, and a finding whose origin is "the model said
/// so" must be distinguishable from one backed by a fetched source.
/// </summary>
public sealed record ChildFinding(
    string ChildRunId,
    string Claim,
    string NormalizedClaim,
    string Provenance,
    double Confidence,
    string? SourceId = null,
    DateTimeOffset? ObservedUtc = null);

public enum ConflictResolution
{
    /// <summary>One side's evidence is clearly better; the parent may use it.</summary>
    ResolvedByEvidence,

    /// <summary>Same fact, different times — the newer observation wins.</summary>
    ResolvedByRecency,

    /// <summary>Genuine disagreement the parent must report rather than pick.</summary>
    Unresolved
}

/// <summary>Two children asserting incompatible things about the same subject.</summary>
public sealed record ChildConflict(
    string Subject,
    ChildFinding Left,
    ChildFinding Right,
    ConflictResolution Resolution,
    ChildFinding? Preferred,
    string Explanation);

/// <summary>What the supervisor hands back to the parent run.</summary>
public sealed record SupervisionResult(
    IReadOnlyList<ChildRun> Children,
    IReadOnlyList<ChildFinding> MergedFindings,
    IReadOnlyList<ChildConflict> Conflicts,
    int Succeeded,
    int Failed,
    bool RequiresUser)
{
    public bool PartialSuccess => Succeeded > 0 && Failed > 0;

    public IReadOnlyList<ChildConflict> UnresolvedConflicts =>
        Conflicts.Where(c => c.Resolution == ConflictResolution.Unresolved).ToList();
}
