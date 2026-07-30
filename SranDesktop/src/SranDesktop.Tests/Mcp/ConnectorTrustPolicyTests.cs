using FluentAssertions;
using SranDesktop.Application.Models;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Local connector launch gate. The cases that matter are the ones where an old
/// approval would otherwise be inherited by something new: a swapped binary, a
/// widened permission set, a rewritten manifest, or a connector installed before
/// identity was ever recorded.
/// </summary>
public sealed class ConnectorTrustPolicyTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-connector-trust-{Guid.NewGuid():N}");
    private readonly string _installRoot;
    private readonly string _executable;
    private readonly ConnectorInstallationPolicy _installation;

    public ConnectorTrustPolicyTests()
    {
        _installRoot = Path.Combine(_root, "connectors");
        Directory.CreateDirectory(_installRoot);
        _executable = Path.Combine(_installRoot, "server.exe");
        File.WriteAllText(_executable, "original binary");
        _installation = new ConnectorInstallationPolicy([_installRoot]);
    }

    private ConnectorManifest Manifest(
        ConnectorTrustState state = ConnectorTrustState.Trusted,
        bool enabled = true,
        string? hash = null,
        string? executablePath = null,
        IReadOnlyList<string>? requested = null,
        IReadOnlyList<string>? approved = null)
    {
        var path = executablePath ?? _executable;
        var manifest = new ConnectorManifest
        {
            ConnectorId = "local-test",
            Name = "Local Test Server",
            Version = "1.0.0",
            ExecutablePath = path,
            ExecutableSha256 = hash ?? ConnectorTrustPolicy.HashExecutable(path),
            RequestedCapabilities = requested ?? [],
            ApprovedCapabilities = approved ?? requested ?? [],
            TrustState = state,
            Enabled = enabled,
            TrustedAtUtc = DateTimeOffset.UtcNow
        };
        return manifest with
        {
            ApprovedManifestFingerprint = manifest.ComputeManifestFingerprint()
        };
    }

    [Fact]
    public void An_Approved_Unchanged_Executable_May_Launch()
    {
        var verdict = ConnectorTrustPolicy.Evaluate(Manifest(), _installation);

        verdict.MayLaunch.Should().BeTrue(verdict.Reason);
        verdict.State.Should().Be(ConnectorTrustState.Trusted);
    }

    [Fact]
    public void Replacing_The_Executable_After_Trust_Invalidates_It()
    {
        var manifest = Manifest();
        File.WriteAllText(_executable, "swapped binary");

        var verdict = ConnectorTrustPolicy.Evaluate(manifest, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.State.Should().Be(ConnectorTrustState.Invalidated);
        verdict.RejectionCode.Should().Be("ConnectorExecutableChanged");
    }

    [Fact]
    public void A_Hash_That_Was_Never_The_Approved_One_Refuses_To_Launch()
    {
        var verdict = ConnectorTrustPolicy.Evaluate(
            Manifest(hash: new string('A', 64)), _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorExecutableChanged");
    }

    [Fact]
    public void A_Missing_Executable_Fails_Closed()
    {
        var manifest = Manifest();
        File.Delete(_executable);

        var verdict = ConnectorTrustPolicy.Evaluate(manifest, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorExecutableMissing");
    }

    [Fact]
    public void A_Connector_With_No_Recorded_Identity_Is_Never_Auto_Trusted()
    {
        var legacy = Manifest(hash: "") with { ApprovedManifestFingerprint = null };

        var verdict = ConnectorTrustPolicy.Evaluate(legacy, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorIdentityUnknown");
        verdict.State.Should().Be(
            ConnectorTrustState.AwaitingTrust,
            "a legacy install must be re-approved, not silently blessed with today's hash");
    }

    [Fact]
    public void Migrating_A_Legacy_Connector_Moves_It_To_AwaitingTrust_Without_Hashing()
    {
        var legacy = Manifest(hash: "") with { ApprovedManifestFingerprint = null };

        var migrated = ConnectorTrustPolicy.MigrateLegacy(legacy);

        migrated.TrustState.Should().Be(ConnectorTrustState.AwaitingTrust);
        migrated.ExecutableSha256.Should().BeEmpty(
            "computing the hash here would approve whatever binary is on disk now");
        migrated.TrustedAtUtc.Should().BeNull();
        ConnectorTrustPolicy.Evaluate(migrated, _installation).MayLaunch.Should().BeFalse();
    }

    [Fact]
    public void An_Explicit_Approval_Records_The_Identity_And_Then_Allows_Launch()
    {
        var legacy = ConnectorTrustPolicy.MigrateLegacy(
            Manifest(hash: "") with { ApprovedManifestFingerprint = null });

        var approved = ConnectorTrustPolicy.Approve(legacy, _installation);

        approved.ExecutableSha256.Should().Be(ConnectorTrustPolicy.HashExecutable(_executable));
        approved.TrustState.Should().Be(ConnectorTrustState.Trusted);
        approved.ApprovedManifestFingerprint.Should().NotBeNullOrEmpty();
        ConnectorTrustPolicy.Evaluate(approved, _installation).MayLaunch.Should().BeTrue();
    }

    [Fact]
    public void An_Executable_Outside_The_Controlled_Location_Cannot_Launch()
    {
        var loose = Path.Combine(_root, "downloads-server.exe");
        File.WriteAllText(loose, "downloaded binary");

        var verdict = ConnectorTrustPolicy.Evaluate(
            Manifest(executablePath: loose), _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorPathNotControlled");
    }

    [Fact]
    public void Repointing_The_Manifest_At_A_Different_Binary_Is_Caught()
    {
        var manifest = Manifest();
        var replacement = Path.Combine(_installRoot, "other.exe");
        File.WriteAllText(replacement, "original binary");

        // Same bytes, different file: the approved manifest no longer describes
        // what is about to run, so the fingerprint check must catch it even
        // though the hash would match.
        var verdict = ConnectorTrustPolicy.Evaluate(
            manifest with { ExecutablePath = replacement }, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorManifestChanged");
    }

    [Fact]
    public void A_Connector_Asking_For_More_Than_Was_Approved_Cannot_Launch()
    {
        var manifest = Manifest(
            requested: ["workspace.read"],
            approved: ["workspace.read"]);
        var expanded = manifest with
        {
            RequestedCapabilities = ["workspace.read", "network.outbound"]
        };

        var verdict = ConnectorTrustPolicy.Evaluate(
            expanded with { ApprovedManifestFingerprint = null }, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorPermissionExpanded");
        verdict.Reason.Should().Contain("network.outbound");
    }

    [Fact]
    public void Changing_The_Declaration_After_Approval_Invalidates_It()
    {
        var manifest = Manifest();

        var verdict = ConnectorTrustPolicy.Evaluate(
            manifest with { RequiresNetwork = true }, _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be("ConnectorManifestChanged");
    }

    [Theory]
    [InlineData(ConnectorTrustState.Untrusted, true, "ConnectorUntrusted")]
    [InlineData(ConnectorTrustState.Revoked, true, "ConnectorRevoked")]
    [InlineData(ConnectorTrustState.Invalidated, true, "ConnectorUntrusted")]
    [InlineData(ConnectorTrustState.AwaitingTrust, true, "ConnectorAwaitingTrust")]
    [InlineData(ConnectorTrustState.Trusted, false, "ConnectorDisabled")]
    public void Every_Non_Approved_State_Refuses_To_Launch(
        ConnectorTrustState state,
        bool enabled,
        string expectedCode)
    {
        var verdict = ConnectorTrustPolicy.Evaluate(Manifest(state, enabled), _installation);

        verdict.MayLaunch.Should().BeFalse();
        verdict.RejectionCode.Should().Be(expectedCode);
    }

    [Fact]
    public void Approving_Something_Outside_The_Controlled_Location_Is_Refused()
    {
        var loose = Path.Combine(_root, "elsewhere.exe");
        File.WriteAllText(loose, "binary");

        var act = () => ConnectorTrustPolicy.Approve(
            Manifest(executablePath: loose), _installation);

        act.Should().Throw<InvalidOperationException>();
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}
