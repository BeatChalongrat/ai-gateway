using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// The same request must compile to the same kind of outcome whatever language
/// it is written in.
///
/// This is not a localisation nicety. <see cref="OutcomeKind"/> selects the
/// verification path: which capabilities may satisfy the outcome, whether a
/// read-back value is demanded, what the Definition of Done asks for. When kind
/// inference is driven by English keywords, a Thai request matches nothing and
/// falls to <see cref="OutcomeKind.Generic"/> — historically the weakest path in
/// the system. The users of this app type Thai, and the 238-task benchmark is
/// written in English, so the strict path was the only one ever measured and the
/// weak path was the only one ever shipped.
///
/// "Cannot classify" must therefore mean stricter, not looser.
/// </summary>
public sealed class OutcomeLanguageParityTests
{
    // Pairs where classification already agrees. These are the ones that must
    // never regress while the parity work is outstanding.
    [Theory]
    [InlineData("list running docker containers", "ดู docker ที่ว่ามีอะไรรันอยู่บ้าง")]
    [InlineData("run the build command", "รันคำสั่ง build ให้หน่อย")]
    [InlineData("save the notes file", "เซฟ notes ลง file ให้หน่อย")]
    public void The_Same_Request_Compiles_To_The_Same_Kind_In_Either_Language(
        string english, string other)
    {
        KindsOf(other).Should().BeEquivalentTo(KindsOf(english),
            $"'{other}' and '{english}' ask for the same thing, so they must be "
            + "verified the same way — the user's language cannot decide how "
            + "strictly the runtime checks its own work");
    }

    /// <summary>
    /// Characterisation of a gap that is still open, recorded rather than
    /// asserted away.
    ///
    /// `InferKind` is a fifteen-word English keyword table. A request written
    /// entirely in a non-Latin script matches none of it and falls to
    /// <see cref="OutcomeKind.Generic"/>, while its English twin gets a specific
    /// kind and the stricter verification that comes with it.
    ///
    /// This test asserts today's behaviour ON PURPOSE, so the gap is visible in
    /// the suite instead of living only in a report. **It is written to fail the
    /// day classification stops depending on language** — when that lands, invert
    /// it back into the parity theory above rather than deleting it.
    ///
    /// Not fixed in this change by decision: the tightening that would have made
    /// Generic safe on its own was measured and rejected (see the Generic case in
    /// SemanticOutcomeMatcher), because the unknown bucket holds action intents
    /// as well as observation intents. The real remedy is to classify properly,
    /// which is a separate change.
    /// </summary>
    [Theory]
    [InlineData("save the notes file", "เซฟไฟล์โน้ตให้หน่อย")]
    [InlineData("save the notes file", "ノートをファイルに保存する")]
    public void Kind_Inference_Still_Depends_On_The_Language_Of_The_Request(
        string english, string nonLatin)
    {
        KindsOf(english).Should().Contain(OutcomeKind.FileState,
            "the English phrasing matches the keyword table");
        KindsOf(nonLatin).Should().AllBeEquivalentTo(OutcomeKind.Generic,
            "the same request in a script the table does not cover falls to the "
            + "unclassified bucket — this is the open gap, not the intended design");
    }

    private static IReadOnlyList<OutcomeKind> KindsOf(string request) =>
        new OutcomeCompiler()
            .Compile(Goal(request), "task", "milestone", Skill())
            .Outcomes
            .Where(outcome => outcome.Required && !outcome.Prohibited)
            .Select(outcome => outcome.Kind)
            .ToList();

    private static GoalSpec Goal(string request) => new()
    {
        GoalId = "goal",
        OriginalUserRequest = request,
        NormalizedGoal = request,
        DesiredOutcome = request,
        Constraints = [],
        CreatedAtUtc = DateTimeOffset.UtcNow,
        UpdatedAtUtc = DateTimeOffset.UtcNow
    };

    private static SkillDefinition Skill() => new()
    {
        SkillId = "skill",
        Name = "Language parity test",
        Version = "1",
        Description = "Outcome language parity",
        RecoveryPolicy = "Observe then change strategy.",
        CompensationPolicy = "No compensation required.",
        TrustedSourcesPolicy = "Structured runtime evidence only."
    };
}
