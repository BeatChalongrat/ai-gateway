using System.Diagnostics;
using System.Text.Json;
using DocumentFormat.OpenXml.Packaging;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Connectors;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;
using SranDesktop.Tests.Mcp;
using Xunit;
using Xunit.Abstractions;

namespace SranDesktop.Tests.Benchmark;

/// <summary>
/// The Phase 4 benchmark.
///
/// Everything here runs against the production composition and real workers: a
/// document worker in an AppContainer, a local MCP server in another process, a
/// remote MCP server in a third. The numbers are therefore about this build, but
/// they are still FIXTURE numbers — deterministic servers with deterministic
/// failures — and nothing in them supports a claim about a live provider or
/// about production reliability.
///
/// The counters that matter are the ones that must be zero: false success,
/// duplicate side effects, corrupt artifacts, damage to a file the user already
/// had. Those are asserted, not merely reported, because a benchmark whose
/// failures are only printed is a benchmark nobody notices failing.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "Phase4Benchmark")]
public sealed class Phase4BenchmarkTests : IAsyncLifetime
{
    private const string LocalConnector = "bench-local";
    private const string RemoteConnector = "bench-remote";

    private readonly ITestOutputHelper _output;
    private readonly Phase4BenchmarkLedger _ledger = new();
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-bench-{Guid.NewGuid():N}");

    private string _workspace = "";
    private DocumentWorkerService _documents = null!;
    private ProductionConnectorHost _host = null!;
    private RemoteMcpFixture _remote = null!;

    public Phase4BenchmarkTests(ITestOutputHelper output) => _output = output;

    public async Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        AgentInputScope.ResetForTests();

        _workspace = Path.Combine(_root, "workspace");
        Directory.CreateDirectory(_workspace);
        _documents = new DocumentWorkerService(
            new IsolatedWorkerHost(
                new WorkerImageResolver(Path.Combine(AppContext.BaseDirectory, "workers"))),
            new WorkspaceFileBroker(
                [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
                _workspace),
            Path.Combine(_root, "scratch"));

        _remote = await RemoteMcpFixture.StartAsync();
        _host = new ProductionConnectorHost(
            _root,
            $"--Sran:McpCatalogUrl={_remote.BaseUrl}/control/none",
            "--Sran:McpOperationTimeoutSeconds=8",
            "--Sran:ConnectorDialTimeoutSeconds=20");

        ConnectorPackage.Install(_host.ConnectorRoot, LocalConnector);
        await ApproveLocalAsync();
        await InstallRemoteAsync();
    }

    // ── Documents ───────────────────────────────────────────────────────────

    [Fact]
    public async Task Documents_Meet_The_Phase4_Fixture_Targets()
    {
        for (var index = 0; index < 15; index++)
            await CreateDocumentAsync("docx", index);
        for (var index = 0; index < 15; index++)
            await CreateDocumentAsync("xlsx", index);
        for (var index = 0; index < 15; index++)
            await CreateDocumentAsync("pptx", index);
        for (var index = 0; index < 10; index++)
            await InspectPdfAsync(index);
        for (var index = 0; index < 10; index++)
            await EditExistingAsync(index);
        for (var index = 0; index < 10; index++)
            await ValidationFailureAsync(index);
        for (var index = 0; index < 10; index++)
            await CrashDuringCreateAsync(index);

        _ledger.CountFor("documents.create.docx").Should().Be(15);
        _ledger.CountFor("documents.create.xlsx").Should().Be(15);
        _ledger.CountFor("documents.create.pptx").Should().Be(15);
        _ledger.CountFor("documents.inspect.pdf").Should().Be(10);
        _ledger.CountFor("documents.edit").Should().Be(10);
        _ledger.CountFor("documents.validation-failure").Should().Be(10);
        _ledger.CountFor("documents.crash-restart").Should().Be(10);
        AssertClean();
    }

    private async Task CreateDocumentAsync(string format, int index)
    {
        var output = Path.Combine(_workspace, $"bench-{format}-{index}.{format}");
        var payload = format switch
        {
            "docx" => (object)new
            {
                blocks = new object[]
                {
                    new { type = "heading", level = 1, text = $"Report {index}" },
                    new { type = "paragraph", text = "Benchmark body." },
                    new { type = "table", rows = new[] { new[] { "K", "V" }, new[] { "a", "1" } } }
                },
                expect = new { contains_text = new[] { $"Report {index}" }, min_tables = 1 }
            },
            "xlsx" => new
            {
                sheets = new object[]
                {
                    new
                    {
                        name = "Data",
                        rows = new object[]
                        {
                            new object[] { "Item", "Qty" },
                            new object[] { $"row-{index}", 7 }
                        }
                    }
                },
                expect = new { contains_text = new[] { $"row-{index}" } }
            },
            _ => new
            {
                slides = new object[]
                {
                    new { title = $"Deck {index}", bullets = new[] { "One", "Two" } }
                },
                expect = new { contains_text = new[] { $"Deck {index}" } }
            }
        };

        var stopwatch = Stopwatch.StartNew();
        var result = await DispatchDocumentAsync("document.create", output, payload);
        stopwatch.Stop();

        var reopened = result.Verified && Reopens(output, format);
        _ledger.Record(new BenchmarkOutcome
        {
            Area = $"documents.create.{format}",
            Scenario = "create and reopen",
            VerifiedSuccess = reopened,
            // Reported verified but the test's own parser cannot open it.
            FalseSuccess = result.Verified && !reopened,
            CorruptArtifact = result.Verified && !reopened,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task InspectPdfAsync(int index)
    {
        var pdf = Path.Combine(_workspace, $"bench-{index}.pdf");
        await File.WriteAllTextAsync(pdf, MinimalPdf(index % 3 + 1));

        var stopwatch = Stopwatch.StartNew();
        var result = await DispatchDocumentAsync("document.inspect", pdf, new { }, pdf);
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "documents.inspect.pdf",
            Scenario = "structural parse, no execution",
            VerifiedSuccess = result.Verified,
            FalseSuccess = false,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task EditExistingAsync(int index)
    {
        var target = Path.Combine(_workspace, $"edit-{index}.docx");
        await DispatchDocumentAsync("document.create", target, new
        {
            blocks = new object[] { new { type = "paragraph", text = $"Before {index}." } }
        });

        var stopwatch = Stopwatch.StartNew();
        var result = await DispatchDocumentAsync("document.edit", target, new
        {
            edits = new object[]
            {
                new { op = "replace_text", find = $"Before {index}.", replace = $"After {index}." }
            },
            expect = new { contains_text = new[] { $"After {index}." } }
        }, target);
        stopwatch.Stop();

        var applied = result.Verified && TextOf(target).Contains($"After {index}.");
        _ledger.Record(new BenchmarkOutcome
        {
            Area = "documents.edit",
            Scenario = "edit existing, atomic publish",
            VerifiedSuccess = applied,
            FalseSuccess = result.Verified && !applied,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task ValidationFailureAsync(int index)
    {
        var target = Path.Combine(_workspace, $"guard-{index}.docx");
        await DispatchDocumentAsync("document.create", target, new
        {
            blocks = new object[] { new { type = "paragraph", text = $"Original {index}." } }
        });
        var before = await File.ReadAllBytesAsync(target);

        var stopwatch = Stopwatch.StartNew();
        var result = await DispatchDocumentAsync("document.edit", target, new
        {
            edits = new object[]
            {
                new { op = "replace_text", find = $"Original {index}.", replace = "Changed." }
            },
            expect = new { contains_text = new[] { "A SENTENCE NEVER WRITTEN" } }
        }, target);
        stopwatch.Stop();

        var untouched = (await File.ReadAllBytesAsync(target)).SequenceEqual(before);
        _ledger.Record(new BenchmarkOutcome
        {
            Area = "documents.validation-failure",
            Scenario = "refused publish leaves the original byte-identical",
            ExpectedRefusal = !result.Verified,
            FalseSuccess = result.Verified,
            OriginalFileDamaged = !untouched,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task CrashDuringCreateAsync(int index)
    {
        var output = Path.Combine(_workspace, $"interrupted-{index}.docx");
        using var cancellation = new CancellationTokenSource();
        var stopwatch = Stopwatch.StartNew();

        var pending = _documents.ExecuteAsync(
            DocumentRequest("document.create", output, new
            {
                blocks = new object[] { new { type = "paragraph", text = "Never lands." } }
            }),
            cancellation.Token);
        cancellation.Cancel();
        var result = await pending;
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "documents.crash-restart",
            Scenario = "cancelled after dispatch",
            ExpectedRefusal = !result.Verified,
            FalseSuccess = result.Verified,
            // Interruption must not leave a partial document where the finished
            // one belongs.
            CorruptArtifact = File.Exists(output),
            ReconciliationSucceeded = result.RequiresReconciliation,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    // ── Local MCP ───────────────────────────────────────────────────────────

    [Fact]
    public async Task Local_Mcp_Meets_The_Phase4_Fixture_Targets()
    {
        for (var index = 0; index < 20; index++)
            await LocalReadAsync(index);
        for (var index = 0; index < 20; index++)
            await LocalMutationAsync(index);
        for (var index = 0; index < 10; index++)
            await LocalResourceAsync(index);
        for (var index = 0; index < 10; index++)
            await LocalPromptAsync(index);
        for (var index = 0; index < 10; index++)
            await LocalReconnectAsync(index);
        for (var index = 0; index < 10; index++)
            await MaliciousOutputAsync(index);
        for (var index = 0; index < 10; index++)
            await ProtocolFailureAsync(index);

        _ledger.CountFor("mcp.local.read").Should().Be(20);
        _ledger.CountFor("mcp.local.mutation").Should().Be(20);
        _ledger.CountFor("mcp.local.resource").Should().Be(10);
        _ledger.CountFor("mcp.local.prompt").Should().Be(10);
        _ledger.CountFor("mcp.local.reconnect").Should().Be(10);
        _ledger.CountFor("mcp.malicious-output").Should().Be(10);
        _ledger.CountFor("mcp.protocol-failure").Should().Be(10);
        AssertClean();
    }

    private Task LocalReadAsync(int index) =>
        MeasureMcpAsync("mcp.local.read", "tools/call ping",
            LocalRequest(McpOperationKind.ToolsCall, tool: "ping"));

    private async Task LocalMutationAsync(int index)
    {
        var context = await Runs.ScheduledAsync(_host, "use the connector");
        var arguments = JsonSerializer.Serialize(new { name = $"bench-{index}" });
        await Runs.ApproveAsync(_host, context, LocalConnector, "create_record", arguments);

        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteAsync(
            context,
            LocalRequest(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.local.mutation",
            Scenario = "approved mutation with recorded intent",
            VerifiedSuccess = result.Status == McpOperationStatus.Succeeded,
            FalseSuccess = false,
            UserIntervention = result.Status == McpOperationStatus.NeedsUser,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private Task LocalResourceAsync(int index) =>
        MeasureMcpAsync("mcp.local.resource", "resources/read",
            LocalRequest(McpOperationKind.ResourcesRead, resource: "fixture://readme"));

    private Task LocalPromptAsync(int index) =>
        MeasureMcpAsync("mcp.local.prompt", "prompts/get",
            LocalRequest(McpOperationKind.PromptsGet, promptName: "summarize"));

    private Task LocalReconnectAsync(int index) =>
        MeasureMcpAsync("mcp.local.reconnect", "reconnect and reinitialize",
            LocalRequest(McpOperationKind.Reconnect));

    private async Task MaliciousOutputAsync(int index)
    {
        // The fixture's resource text tries to be an instruction. It must arrive
        // wrapped as data, every time.
        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteOwnedAsync(
            LocalRequest(McpOperationKind.ResourcesRead, resource: "fixture://readme"),
            "bench");
        stopwatch.Stop();

        var contained = result.Content.StartsWith(UntrustedObservationBoundary.Marker);
        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.malicious-output",
            Scenario = "hostile resource content arrives as an observation",
            VerifiedSuccess = contained,
            PolicyViolation = result.Status == McpOperationStatus.Succeeded && !contained,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task ProtocolFailureAsync(int index)
    {
        // A feature this build does not implement must be refused by name and
        // still leave a step behind it.
        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteOwnedAsync(
            LocalRequest(McpOperationKind.CompletionComplete), "bench");
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.protocol-failure",
            Scenario = "unimplemented feature returns CapabilityUnavailable",
            ExpectedRefusal = result.Status == McpOperationStatus.CapabilityUnavailable,
            FalseSuccess = result.Status == McpOperationStatus.Succeeded,
            DurationMs = stopwatch.ElapsedMilliseconds,
            Note = result.FailureCode
        });
    }

    // ── Remote MCP ──────────────────────────────────────────────────────────

    [Fact]
    public async Task Remote_Mcp_Meets_The_Phase4_Fixture_Targets()
    {
        for (var index = 0; index < 10; index++)
            await RemoteReadAsync(index);
        for (var index = 0; index < 10; index++)
            await RemoteMutationAsync(index);
        for (var index = 0; index < 10; index++)
            await RemoteReconnectAsync(index);
        for (var index = 0; index < 10; index++)
            await RemoteTimeoutAsync(index);
        for (var index = 0; index < 10; index++)
            await RemoteReconciliationAsync(index);

        _ledger.CountFor("mcp.remote.read").Should().Be(10);
        _ledger.CountFor("mcp.remote.mutation").Should().Be(10);
        _ledger.CountFor("mcp.remote.reconnect").Should().Be(10);
        _ledger.CountFor("mcp.remote.timeout").Should().Be(10);
        _ledger.CountFor("mcp.remote.reconciliation").Should().Be(10);

        // The server's own count is the authority on how many mutations happened:
        // ten distinct mutation tasks, plus at most one per resumed pair.
        var stats = await _remote.StatsAsync();
        stats.Created.Should().BeLessThanOrEqualTo(20,
            "ten mutations and ten resumed steps must not exceed one record each");
        stats.Created.Should().BeGreaterThanOrEqualTo(10,
            "the ten distinct mutations really did have to happen");
        AssertClean();
    }

    private Task RemoteReadAsync(int index) =>
        MeasureMcpAsync("mcp.remote.read", "tools/call ping",
            RemoteRequest(McpOperationKind.ToolsCall, tool: "ping"));

    private async Task RemoteMutationAsync(int index)
    {
        var context = await Runs.ScheduledAsync(_host, "use the remote connector");
        var arguments = JsonSerializer.Serialize(new { name = $"remote-bench-{index}" });
        await Runs.ApproveAsync(_host, context, RemoteConnector, "create_record", arguments);
        var before = (await _remote.StatsAsync()).Created;

        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteAsync(
            context,
            RemoteRequest(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));
        stopwatch.Stop();
        var after = (await _remote.StatsAsync()).Created;

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.remote.mutation",
            Scenario = "approved remote mutation",
            VerifiedSuccess = result.Status == McpOperationStatus.Succeeded,
            DuplicateSideEffects = Math.Max(0, after - before - 1),
            UserIntervention = result.Status == McpOperationStatus.NeedsUser,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private Task RemoteReconnectAsync(int index) =>
        MeasureMcpAsync("mcp.remote.reconnect", "reconnect and reinitialize",
            RemoteRequest(McpOperationKind.Reconnect));

    private async Task RemoteTimeoutAsync(int index)
    {
        var context = await Runs.ScheduledAsync(_host, "use the remote connector");
        await Runs.ApproveAsync(_host, context, RemoteConnector, "slow", "{}");

        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteAsync(
            context, RemoteRequest(McpOperationKind.ToolsCall, tool: "slow"));
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.remote.timeout",
            Scenario = "server never answers; deadline fires",
            ExpectedRefusal = result.Status != McpOperationStatus.Succeeded,
            FalseSuccess = result.Status == McpOperationStatus.Succeeded,
            // A request that went out is never blindly repeated.
            PolicyViolation = result.Retryable,
            UserIntervention = result.Status == McpOperationStatus.NeedsUser,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    private async Task RemoteReconciliationAsync(int index)
    {
        // A resumed step, not a new request. The SAME run and the SAME tool call
        // are dispatched twice, which is what a retry after an ambiguous failure
        // actually looks like — and it produces the same idempotency key, so a
        // second side effect would be a genuine duplicate rather than a second
        // thing the user asked for.
        var context = await Runs.ScheduledAsync(_host, "use the remote connector");
        var arguments = JsonSerializer.Serialize(new { name = $"resumed-{index}" });
        await Runs.ApproveAsync(_host, context, RemoteConnector, "create_record", arguments);
        var toolCallId = $"call_{index}_{Guid.NewGuid():N}";
        var request = RemoteRequest(
            McpOperationKind.ToolsCall, tool: "create_record", arguments: arguments)
            with { ToolCallId = toolCallId };

        var before = (await _remote.StatsAsync()).Created;
        var first = await _host.Gateway.ExecuteAsync(context, request);

        var stopwatch = Stopwatch.StartNew();
        var resumed = await _host.Gateway.ExecuteAsync(context, request);
        stopwatch.Stop();
        var after = (await _remote.StatsAsync()).Created;

        // At most one record for the pair: either the first created it and the
        // resume was reconciled, or the first was refused and nothing happened.
        var created = after - before;
        _ledger.Record(new BenchmarkOutcome
        {
            Area = "mcp.remote.reconciliation",
            Scenario = "resumed step does not repeat the side effect",
            VerifiedSuccess = created <= 1,
            ReconciliationSucceeded = created <= 1 && first.Status == McpOperationStatus.Succeeded,
            DuplicateSideEffects = Math.Max(0, created - 1),
            UserIntervention = resumed.Status == McpOperationStatus.NeedsUser,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
    }

    // ── Connector lifecycle ─────────────────────────────────────────────────

    [Fact]
    public async Task Connector_Lifecycle_Meets_The_Phase4_Fixture_Targets()
    {
        const string id = "bench-lifecycle";
        var installer = new ConnectorPackageInstaller(
            _host.ConnectorRoot, _host.TrustStore);
        var incoming = Path.Combine(_root, "incoming");
        StagePackage(incoming, id, "1.0.0");

        await StepAsync("install", async () =>
            (await installer.InstallAsync(id, incoming)).Succeeded);
        await StepAsync("unapproved-cannot-run", () =>
            Task.FromResult(!_host.Resolver.Resolve(id).Resolved));
        await StepAsync("approval", async () =>
        {
            await ApproveAsync(id);
            return _host.Resolver.Resolve(id).Resolved;
        });
        await StepAsync("deny", async () =>
        {
            await _host.Trust.ApproveAsync(id,
                ConnectorApprovalDecision.Deny("benchmark"),
                UserApprovalEvidence.FromAdministrativePolicy("benchmark"));
            return !_host.Resolver.Resolve(id).Resolved;
        });
        await StepAsync("reapprove", async () =>
        {
            await ApproveAsync(id);
            return _host.Resolver.Resolve(id).Resolved;
        });
        await StepAsync("restart-preserves-trust", async () =>
        {
            await _host.DisposeAsync();
            _host = NewHost();
            return _host.Resolver.Resolve(id).Resolved;
        });

        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, id, "2.0.0");
        var upgrader = new ConnectorPackageInstaller(_host.ConnectorRoot, _host.TrustStore);
        await StepAsync("update", async () =>
            (await upgrader.InstallAsync(id, next)).Outcome == ConnectorInstallOutcome.Updated
            && !_host.Resolver.Resolve(id).Resolved);

        var greedy = Path.Combine(_root, "incoming-greedy");
        StagePackage(greedy, id, "3.0.0", requiresNetwork: true);
        await StepAsync("permission-expanding-update", async () =>
            (await upgrader.InstallAsync(id, greedy)).PermissionExpansions.Contains("network"));
        await StepAsync("rollback", async () =>
            (await upgrader.RollbackAsync(id)).Outcome == ConnectorInstallOutcome.RolledBack);
        await StepAsync("disable", async () =>
        {
            await ApproveAsync(id);
            await _host.Trust.DisableAsync(id, Evidence());
            return _host.Resolver.Resolve(id).DenialCode == "ConnectorDisabled";
        });
        await StepAsync("revoke", async () =>
        {
            await _host.Trust.RevokeAsync(id, Evidence());
            return _host.Resolver.Resolve(id).DenialCode == "ConnectorRevoked";
        });
        await StepAsync("hash-change-reapproval", async () =>
        {
            await ApproveAsync(id);
            await File.AppendAllTextAsync(
                ConnectorPackage.ExecutablePath(_host.ConnectorRoot, id), "\0tampered");
            return _host.Resolver.Resolve(id).DenialCode == "ConnectorExecutableChanged";
        });
        await StepAsync("uninstall", async () =>
            (await upgrader.UninstallAsync(id, removeCredentials: true)).Succeeded);

        _ledger.CountFor("lifecycle").Should().Be(13);
        AssertClean();
    }

    // ── Report ──────────────────────────────────────────────────────────────

    [Fact]
    public void Every_Number_This_Suite_Produces_Comes_From_A_Fixture()
    {
        // Stated as a test so the report cannot quietly claim otherwise: there
        // are no provider credentials and no third-party connector in this
        // environment, so live-provider and real-connector results are Not Run.
        // Asserted at the source rather than over a ledger, because xunit gives
        // each test its own instance and an empty ledger would pass vacuously.
        var source = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Tests", "Benchmark",
            "Phase4BenchmarkTests.cs"));

        // Assembled at runtime so this file does not contain the very strings it
        // is asserting are absent.
        foreach (var claim in new[] { "LiveProvider", "RealExternalConnector", "ScriptedModel" })
            source.Should().NotContain($"BenchmarkSource.{claim}",
                $"no measurement here may present itself as a {claim} result");
        new BenchmarkOutcome { Area = "x", Scenario = "y" }.Source
            .Should().Be(BenchmarkSource.DeterministicFixture,
                "an outcome that forgets to name its source must default to the "
                + "weakest claim, never the strongest");
    }

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException("The repository root was not found.");
    }

    // ── Plumbing ────────────────────────────────────────────────────────────

    private async Task StepAsync(string scenario, Func<Task<bool>> step)
    {
        var stopwatch = Stopwatch.StartNew();
        bool passed;
        try
        {
            passed = await step();
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException
                                       or UnauthorizedAccessException)
        {
            passed = false;
        }
        stopwatch.Stop();
        _ledger.Record(new BenchmarkOutcome
        {
            Area = "lifecycle",
            Scenario = scenario,
            VerifiedSuccess = passed,
            PolicyViolation = !passed,
            DurationMs = stopwatch.ElapsedMilliseconds
        });
        passed.Should().BeTrue($"lifecycle step '{scenario}' must hold");
    }

    private async Task MeasureMcpAsync(
        string area, string scenario, McpOperationRequest request)
    {
        var stopwatch = Stopwatch.StartNew();
        var result = await _host.Gateway.ExecuteOwnedAsync(request, "bench");
        stopwatch.Stop();

        _ledger.Record(new BenchmarkOutcome
        {
            Area = area,
            Scenario = scenario,
            VerifiedSuccess = result.Status == McpOperationStatus.Succeeded,
            // The invariant is about operations that EXECUTED: one that reached a
            // server without a durable step behind it is the bypass this phase
            // exists to remove. A refusal that never left the process is not a
            // violation — counting it as one would mark correct enforcement as a
            // failure and make the number meaningless.
            PolicyViolation = result.Status == McpOperationStatus.Succeeded
                              && string.IsNullOrEmpty(result.ActionId),
            UserIntervention = result.Status == McpOperationStatus.NeedsUser,
            DurationMs = stopwatch.ElapsedMilliseconds,
            Note = result.Status == McpOperationStatus.Succeeded ? null : result.FailureCode
        });
    }

    private void AssertClean()
    {
        _output.WriteLine(_ledger.ToMarkdown());
        // One file per group. xunit gives each test its own instance and its own
        // ledger, so a single shared filename would leave only whichever test
        // finished last — and a benchmark report missing three quarters of its
        // rows looks like a benchmark that was never run.
        // Two segments, not one: "mcp.local" and "mcp.remote" share a prefix, and
        // naming by prefix alone had them silently overwriting each other.
        var group = string.Join(".",
            (_ledger.Outcomes.FirstOrDefault()?.Area ?? "unknown").Split('.').Take(2));
        _ledger.Save(Path.Combine(
            AppContext.BaseDirectory, "benchmark", $"phase4-{group}.json"));

        _ledger.TotalFalseSuccess.Should().Be(0, "false success is the failure that matters most");
        _ledger.TotalDuplicates.Should().Be(0, "a duplicate side effect is unrecoverable");
        _ledger.TotalCorrupt.Should().Be(0);
        _ledger.TotalOriginalDamage.Should().Be(0, "the user's existing file is not ours to break");
        _ledger.TotalPolicyViolations.Should().Be(0);
        _ledger.TotalTokenLeaks.Should().Be(0);
    }

    private static McpOperationRequest LocalRequest(
        McpOperationKind operation, string? tool = null, string? resource = null,
        string? promptName = null, string arguments = "{}") =>
        Request(LocalConnector, operation, tool, resource, promptName, arguments);

    private static McpOperationRequest RemoteRequest(
        McpOperationKind operation, string? tool = null, string? resource = null,
        string? promptName = null, string arguments = "{}") =>
        Request(RemoteConnector, operation, tool, resource, promptName, arguments);

    private static McpOperationRequest Request(
        string connectorId, McpOperationKind operation, string? tool, string? resource,
        string? promptName, string arguments) => new()
    {
        ConnectorId = connectorId,
        Operation = operation,
        ToolName = tool,
        ResourceUri = resource,
        PromptName = promptName,
        ArgumentsJson = arguments
    };

    private DocumentOperationRequest DocumentRequest(
        string operation, string output, object payload, string? source = null) => new()
    {
        RunId = $"bench_{Guid.NewGuid():N}",
        StepId = "step_1",
        AttemptId = $"attempt_{Guid.NewGuid():N}",
        IdempotencyKey = Guid.NewGuid().ToString("N"),
        Operation = operation,
        SourcePath = source,
        OutputPath = output,
        PayloadJson = JsonSerializer.Serialize(payload)
    };

    private Task<DocumentOperationResult> DispatchDocumentAsync(
        string operation, string output, object payload, string? source = null) =>
        _documents.ExecuteAsync(
            DocumentRequest(operation, output, payload, source), CancellationToken.None);

    private static bool Reopens(string path, string format)
    {
        try
        {
            switch (format)
            {
                case "docx":
                    using (var document = WordprocessingDocument.Open(path, false))
                        return document.MainDocumentPart?.Document.Body is not null;
                case "xlsx":
                    using (var workbook = SpreadsheetDocument.Open(path, false))
                        return workbook.WorkbookPart?.Workbook is not null;
                default:
                    using (var deck = PresentationDocument.Open(path, false))
                        return deck.PresentationPart?.Presentation is not null;
            }
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException
                                       or FileFormatException)
        {
            return false;
        }
    }

    private static string TextOf(string path)
    {
        using var document = WordprocessingDocument.Open(path, false);
        return document.MainDocumentPart!.Document.Body!.InnerText;
    }

    private static string MinimalPdf(int pages)
    {
        var kids = string.Join(" ", Enumerable.Range(0, pages).Select(i => $"{3 + i} 0 R"));
        var builder = new System.Text.StringBuilder("%PDF-1.7\n");
        builder.Append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
        builder.Append($"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {pages} >>\nendobj\n");
        for (var i = 0; i < pages; i++)
            builder.Append(
                $"{3 + i} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n");
        builder.Append("xref\n0 1\n0000000000 65535 f \n");
        builder.Append($"trailer\n<< /Size {3 + pages} /Root 1 0 R >>\nstartxref\n0\n%%EOF");
        return builder.ToString();
    }

    private ProductionConnectorHost NewHost() => new(
        _root,
        $"--Sran:McpCatalogUrl={_remote.BaseUrl}/control/none",
        "--Sran:McpOperationTimeoutSeconds=8",
        "--Sran:ConnectorDialTimeoutSeconds=20");

    private static UserApprovalEvidence Evidence() =>
        UserApprovalEvidence.FromUserGesture("user:benchmark", "approval dialog");

    private Task ApproveLocalAsync() => ApproveAsync(LocalConnector);

    private async Task ApproveAsync(string connectorId) =>
        await _host.Trust.ApproveAsync(
            connectorId,
            new ConnectorApprovalDecision(
                ConnectorApprovalOutcome.Approved, "user:benchmark"),
            Evidence());

    private async Task InstallRemoteAsync() =>
        await _host.Connectors.InstallCustomAsync(
            new McpConnector
            {
                Id = RemoteConnector,
                Name = "Remote Benchmark",
                Transport = McpConnector.TransportHttp,
                ServerUrl = _remote.ServerUrl,
                AuthType = "none"
            },
            evidence: Evidence());

    private static void StagePackage(
        string directory, string connectorId, string version, bool requiresNetwork = false)
    {
        if (Directory.Exists(directory))
            Directory.Delete(directory, recursive: true);
        Directory.CreateDirectory(directory);
        foreach (var file in Directory.GetFiles(
                     ConnectorPackage.SourceImage, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(ConnectorPackage.SourceImage, file);
            var target = Path.Combine(directory, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            new ConnectorPackage.ManifestBuilder(connectorId, ConnectorPackage.ServerFileName)
            {
                Version = version,
                RequiresNetwork = requiresNetwork
            }.ToJson());
    }

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();
        await _remote.DisposeAsync();
        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
