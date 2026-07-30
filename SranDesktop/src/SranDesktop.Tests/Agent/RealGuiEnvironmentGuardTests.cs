using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Safety gate for real-GUI automation (Phase 13 b). Real automation destroys
/// live GUI state, so the guard must DENY BY DEFAULT and permit only when an
/// isolated VM is declared three independent, agreeing ways. These tests pin the
/// deny-by-default behaviour and every partial-signal deny path — the "RealGui"
/// trait keeps them in the default suite precisely because proving the block
/// works everywhere is the point.
/// </summary>
[Trait("Category", "RealGui")]
public class RealGuiEnvironmentGuardTests
{
    private const string Token = RealGuiEnvironmentGuard.RequiredToken;
    private const string Machine = "SRAN-GUI-SANDBOX-01";

    private static RealGuiEnvironmentGuard Guard(
        IDictionary<string, string?> env,
        string machine = Machine,
        string? markerContent = null) =>
        new(
            key => env.TryGetValue(key, out var value) ? value : null,
            () => machine,
            path => markerContent is not null && path == env[RealGuiEnvironmentGuard.MarkerPathVariable],
            _ => markerContent ?? string.Empty);

    private static Dictionary<string, string?> FullyProvisioned(string machine = Machine) => new()
    {
        [RealGuiEnvironmentGuard.OptInVariable] = Token,
        [RealGuiEnvironmentGuard.SandboxHostVariable] = machine,
        [RealGuiEnvironmentGuard.MarkerPathVariable] = @"C:\sran-sandbox\marker.txt",
    };

    [Fact]
    public void Denies_By_Default_With_No_Environment()
    {
        var decision = Guard(new Dictionary<string, string?>()).Evaluate();

        decision.Permitted.Should().BeFalse();
        decision.Reason.Should().Contain("opt-in only");
    }

    [Fact]
    public void Denies_When_Opt_In_Is_A_Truthy_But_Wrong_Value()
    {
        var env = FullyProvisioned();
        env[RealGuiEnvironmentGuard.OptInVariable] = "1";

        Guard(env, markerContent: $"{Token} {Machine}").Evaluate()
            .Permitted.Should().BeFalse();
    }

    [Fact]
    public void Denies_When_Declared_Host_Does_Not_Match_This_Machine()
    {
        var env = FullyProvisioned();
        env[RealGuiEnvironmentGuard.SandboxHostVariable] = "SOME-OTHER-BOX";

        var decision = Guard(env, markerContent: $"{Token} {Machine}").Evaluate();

        decision.Permitted.Should().BeFalse();
        decision.Reason.Should().Contain("does not match this machine");
    }

    [Fact]
    public void Denies_When_Marker_File_Is_Absent()
    {
        var decision = Guard(FullyProvisioned(), markerContent: null).Evaluate();

        decision.Permitted.Should().BeFalse();
        decision.Reason.Should().Contain("marker file is missing");
    }

    [Fact]
    public void Denies_When_Marker_Lacks_The_Token()
    {
        var decision = Guard(FullyProvisioned(), markerContent: $"authorized for {Machine}").Evaluate();

        decision.Permitted.Should().BeFalse();
        decision.Reason.Should().Contain("confirmation token");
    }

    [Fact]
    public void Denies_When_Marker_Was_Authored_For_Another_Machine()
    {
        var decision = Guard(FullyProvisioned(), markerContent: $"{Token} for SOME-OTHER-BOX").Evaluate();

        decision.Permitted.Should().BeFalse();
        decision.Reason.Should().Contain("not authored for this machine");
    }

    [Fact]
    public void Permits_Only_When_All_Three_Signals_Agree()
    {
        var decision = Guard(FullyProvisioned(), markerContent: $"{Token}\nhost={Machine}\n").Evaluate();

        decision.Permitted.Should().BeTrue();
        decision.Reason.Should().Contain("Isolated GUI sandbox confirmed");
    }

    [Fact]
    public void EnsurePermitted_Throws_When_Blocked()
    {
        var guard = Guard(new Dictionary<string, string?>());

        var act = guard.EnsurePermitted;

        act.Should().Throw<InvalidOperationException>()
            .WithMessage("*Real-GUI automation is blocked*");
    }

    [Fact]
    public void EnsurePermitted_Does_Not_Throw_On_A_Declared_Sandbox()
    {
        var guard = Guard(FullyProvisioned(), markerContent: $"{Token} {Machine}");

        guard.EnsurePermitted();
    }
}
