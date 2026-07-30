using System.Text;
using System.Text.Json;

namespace SranDesktop.Tests.Benchmark;

/// <summary>Where a benchmark number came from. Kept as a first-class field
/// because a fixture result and a live-provider result are not the same claim,
/// and a table that mixes them silently is how "it works" gets overstated.</summary>
public enum BenchmarkSource
{
    DeterministicFixture,
    ScriptedModel,
    LiveProvider,
    RealExternalConnector
}

/// <summary>One benchmark task and everything worth counting about it.</summary>
public sealed record BenchmarkOutcome
{
    public required string Area { get; init; }
    public required string Scenario { get; init; }
    public BenchmarkSource Source { get; init; } = BenchmarkSource.DeterministicFixture;

    /// <summary>The task did what it claimed AND the claim was checked
    /// independently — a document reopened, a server's own state read back.</summary>
    public bool VerifiedSuccess { get; init; }

    /// <summary>Reported success that turns out not to be true. The single most
    /// important number in the table, and the one that must be zero.</summary>
    public bool FalseSuccess { get; init; }

    public int DuplicateSideEffects { get; init; }
    public bool CorruptArtifact { get; init; }
    public bool OriginalFileDamaged { get; init; }
    public bool ReconciliationSucceeded { get; init; }
    public bool PolicyViolation { get; init; }
    public bool TokenLeak { get; init; }
    public bool UserIntervention { get; init; }
    public long DurationMs { get; init; }

    /// <summary>An expected refusal is not a failure. A benchmark that counted
    /// "the connector was correctly blocked" as a loss would reward a build that
    /// enforced nothing.</summary>
    public bool ExpectedRefusal { get; init; }

    public string? Note { get; init; }
}

/// <summary>
/// Collects benchmark outcomes and renders the table the report quotes.
///
/// It deliberately does not aggregate across sources. A fixture run and a live
/// provider run answer different questions, and merging them produces a number
/// that answers neither.
/// </summary>
public sealed class Phase4BenchmarkLedger
{
    private readonly List<BenchmarkOutcome> _outcomes = [];
    private readonly object _gate = new();

    public void Record(BenchmarkOutcome outcome)
    {
        lock (_gate)
            _outcomes.Add(outcome);
    }

    public IReadOnlyList<BenchmarkOutcome> Outcomes
    {
        get { lock (_gate) return [.. _outcomes]; }
    }

    public int CountFor(string area) => Outcomes.Count(item => item.Area == area);

    public int TotalFalseSuccess => Outcomes.Count(item => item.FalseSuccess);

    public int TotalDuplicates => Outcomes.Sum(item => item.DuplicateSideEffects);

    public int TotalCorrupt => Outcomes.Count(item => item.CorruptArtifact);

    public int TotalOriginalDamage => Outcomes.Count(item => item.OriginalFileDamaged);

    public int TotalPolicyViolations => Outcomes.Count(item => item.PolicyViolation);

    public int TotalTokenLeaks => Outcomes.Count(item => item.TokenLeak);

    /// <summary>Markdown, for pasting into the report unchanged. Writing it here
    /// rather than by hand means the report cannot drift from the run.</summary>
    public string ToMarkdown()
    {
        var builder = new StringBuilder();
        builder.AppendLine(
            "| Source | Area | Tasks | Verified | False success | Duplicate | Corrupt | "
            + "Original damage | Reconciled | Policy violation | Token leak | User intervention | Median ms |");
        builder.AppendLine(
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|");

        foreach (var group in Outcomes
                     .GroupBy(item => (item.Source, item.Area))
                     .OrderBy(group => group.Key.Source)
                     .ThenBy(group => group.Key.Area, StringComparer.Ordinal))
        {
            var items = group.ToList();
            var durations = items.Select(item => item.DurationMs).OrderBy(value => value).ToList();
            builder.AppendLine(
                $"| {group.Key.Source} | {group.Key.Area} | {items.Count} "
                + $"| {items.Count(item => item.VerifiedSuccess || item.ExpectedRefusal)} "
                + $"| {items.Count(item => item.FalseSuccess)} "
                + $"| {items.Sum(item => item.DuplicateSideEffects)} "
                + $"| {items.Count(item => item.CorruptArtifact)} "
                + $"| {items.Count(item => item.OriginalFileDamaged)} "
                + $"| {items.Count(item => item.ReconciliationSucceeded)} "
                + $"| {items.Count(item => item.PolicyViolation)} "
                + $"| {items.Count(item => item.TokenLeak)} "
                + $"| {items.Count(item => item.UserIntervention)} "
                + $"| {(durations.Count > 0 ? durations[durations.Count / 2] : 0)} |");
        }
        return builder.ToString();
    }

    /// <summary>Writes the run to disk so the report quotes a file rather than a
    /// number somebody remembered.</summary>
    public void Save(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(new
        {
            generatedUtc = DateTimeOffset.UtcNow,
            total = Outcomes.Count,
            falseSuccess = TotalFalseSuccess,
            duplicateSideEffects = TotalDuplicates,
            corruptArtifacts = TotalCorrupt,
            originalFileDamage = TotalOriginalDamage,
            policyViolations = TotalPolicyViolations,
            tokenLeaks = TotalTokenLeaks,
            outcomes = Outcomes
        }, new JsonSerializerOptions { WriteIndented = true }));
        File.WriteAllText(Path.ChangeExtension(path, ".md"), ToMarkdown());
    }
}
