using System.Diagnostics;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Workers;

/// <summary>
/// Dispatches one <see cref="IsolatedWorkerRequest"/> to a sandboxed worker
/// process and classifies what actually happened.
///
/// The load-bearing rule lives here: the worker's exit code and its own
/// "succeeded" claim are inputs, not verdicts. A worker that ran but left no
/// parseable answer is <see cref="WorkerOutcomeClass.PostDispatchUnknownOutcome"/>
/// — never a clean failure — because it may already have acted.
/// </summary>
public sealed class IsolatedWorkerHost : IIsolatedWorkerHost
{
    internal const string RequestFileName = "request.json";
    internal const string ResponseFileName = "response.json";
    private const int ResponseProtocolVersion = 1;
    private const long MaxResponseBytes = 8 * 1024 * 1024;

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = false
    };

    private readonly IWorkerImageResolver _images;
    private readonly Func<string, WindowsAppContainerProcessRunner> _runnerFactory;
    private readonly ILogger<IsolatedWorkerHost>? _logger;

    /// <param name="runnerFactory">Builds the sandbox runner for one dispatch. The
    /// default scopes it to the request's working directory ALONE, so the worker
    /// cannot see the user's workspace even though the host can.</param>
    public IsolatedWorkerHost(
        IWorkerImageResolver images,
        Func<string, WindowsAppContainerProcessRunner>? runnerFactory = null,
        ILogger<IsolatedWorkerHost>? logger = null)
    {
        _images = images;
        _runnerFactory = runnerFactory ?? DefaultRunner;
        _logger = logger;
    }

    private static WindowsAppContainerProcessRunner DefaultRunner(string workingDirectory) =>
        new(new WorkspaceFileBroker(
            [new WorkspaceMount(workingDirectory, WorkspaceMountAccess.ReadWrite)],
            workingDirectory));

    public async Task<IsolatedWorkerOutcome> DispatchAsync(
        IsolatedWorkerRequest request,
        CancellationToken ct)
    {
        var started = Stopwatch.StartNew();
        var evidence = new List<string>
        {
            $"capability={request.Capability}",
            $"operation={request.Operation}",
            $"idempotency_key={request.IdempotencyKey}"
        };

        IsolatedWorkerLaunch launch;
        try
        {
            launch = _images.Resolve(request.Capability);
        }
        catch (Exception ex) when (ex is SandboxUnavailableException or KeyNotFoundException)
        {
            // Nothing was started, so this is provably effect-free.
            return new IsolatedWorkerOutcome(
                WorkerOutcomeClass.PreDispatchFailure,
                null,
                "CapabilityUnavailable",
                ex.Message,
                [.. evidence, "dispatched=false"],
                started.ElapsedMilliseconds,
                TemporaryArtifactsRemoved: true);
        }

        if (request.Network.Allowed)
            return new IsolatedWorkerOutcome(
                WorkerOutcomeClass.PreDispatchFailure,
                null,
                "NetworkPolicyUnsupported",
                "The isolated sandbox has no network capability; a worker that needs "
                + "the network must fail closed rather than run unisolated.",
                [.. evidence, "dispatched=false"],
                started.ElapsedMilliseconds,
                TemporaryArtifactsRemoved: true);

        var requestPath = Path.Combine(request.WorkingDirectory, RequestFileName);
        var responsePath = Path.Combine(request.WorkingDirectory, ResponseFileName);
        try
        {
            Directory.CreateDirectory(request.WorkingDirectory);
            if (File.Exists(responsePath))
                File.Delete(responsePath); // a stale answer must never be read as this attempt's
            await File.WriteAllTextAsync(
                requestPath,
                JsonSerializer.Serialize(WireRequest.From(request), Json),
                ct);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return new IsolatedWorkerOutcome(
                WorkerOutcomeClass.PreDispatchFailure,
                null,
                "WorkerWorkspaceUnavailable",
                ex.Message,
                [.. evidence, "dispatched=false"],
                started.ElapsedMilliseconds,
                TemporaryArtifactsRemoved: true);
        }

        var output = new BoundedOutputBuffer();
        var dispatched = false;
        int exitCode;
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(request.Quotas.Deadline);
        try
        {
            dispatched = true;
            exitCode = await _runnerFactory(request.WorkingDirectory).RunWorkerAsync(
                launch,
                request.WorkingDirectory,
                output.Append,
                deadline.Token);
        }
        catch (SandboxUnavailableException ex)
        {
            // Failed to start: the worker image never executed.
            return Finish(
                WorkerOutcomeClass.PreDispatchFailure,
                null,
                "WorkerUnavailable",
                ex.Message,
                evidence,
                started,
                request,
                dispatchedFlag: false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            // Deadline hit AFTER the worker was launched — it may have finished the
            // mutation and died before writing its answer.
            return Finish(
                await TryReadResponseAsync(responsePath, request, ct) is { } late
                    ? ClassifyReported(late, request, evidence)
                    : WorkerOutcomeClass.PostDispatchUnknownOutcome,
                await TryReadResponseAsync(responsePath, request, ct),
                "Timeout",
                "The worker exceeded its deadline after dispatch; the real state must be "
                + "inspected before any retry.",
                evidence,
                started,
                request,
                dispatched);
        }
        catch (OperationCanceledException)
        {
            return Finish(
                WorkerOutcomeClass.PostDispatchUnknownOutcome,
                await TryReadResponseAsync(responsePath, request, ct),
                "Cancelled",
                "The worker was cancelled after dispatch; the real state must be inspected.",
                evidence,
                started,
                request,
                dispatched);
        }
        catch (SandboxOutputLimitException ex)
        {
            return Finish(
                WorkerOutcomeClass.PostDispatchUnknownOutcome,
                null,
                "OutputQuotaExceeded",
                ex.Message,
                evidence,
                started,
                request,
                dispatched);
        }

        evidence.Add($"worker_exit_code={exitCode}");
        var response = await TryReadResponseAsync(responsePath, request, ct);
        if (response is null)
            // Ran, but left nothing readable. Exit code alone proves nothing about
            // whether the document was written or the remote call landed.
            return Finish(
                WorkerOutcomeClass.PostDispatchUnknownOutcome,
                null,
                "WorkerResponseMissing",
                exitCode == 0
                    ? "The worker exited cleanly but wrote no result; a zero exit code is "
                      + "not evidence that the work happened."
                    : $"The worker exited with code {exitCode} and wrote no result.",
                evidence,
                started,
                request,
                dispatched);

        return Finish(
            ClassifyReported(response, request, evidence),
            response,
            response.Succeeded ? null : response.FailureCode ?? "WorkerReportedFailure",
            response.Succeeded ? null : response.FailureReason,
            evidence,
            started,
            request,
            dispatched);
    }

    /// <summary>Turns the worker's own claim into a host classification. Success is
    /// only ever "unverified" here — confirming it is the caller's job, because only
    /// the caller knows what the artifact or remote object should look like.</summary>
    private static WorkerOutcomeClass ClassifyReported(
        IsolatedWorkerResponse response,
        IsolatedWorkerRequest request,
        List<string> evidence)
    {
        if (!string.Equals(response.IdempotencyKey, request.IdempotencyKey, StringComparison.Ordinal)
            || !string.Equals(response.AttemptId, request.AttemptId, StringComparison.Ordinal))
        {
            evidence.Add("stale_response=true");
            return WorkerOutcomeClass.PostDispatchUnknownOutcome;
        }
        if (response.ProtocolVersion != ResponseProtocolVersion)
        {
            evidence.Add($"protocol_mismatch={response.ProtocolVersion}");
            return WorkerOutcomeClass.PostDispatchUnknownOutcome;
        }
        if (response.Succeeded)
            return WorkerOutcomeClass.SucceededUnverified;
        return response.SideEffectPerformed
            ? WorkerOutcomeClass.FailedPartialEffect
            : WorkerOutcomeClass.FailedNoEffect;
    }

    private static async Task<IsolatedWorkerResponse?> TryReadResponseAsync(
        string responsePath,
        IsolatedWorkerRequest request,
        CancellationToken ct)
    {
        try
        {
            if (!File.Exists(responsePath))
                return null;
            var info = new FileInfo(responsePath);
            if (info.Length is 0 or > MaxResponseBytes)
                return null;
            var text = await File.ReadAllTextAsync(responsePath, CancellationToken.None);
            _ = ct;
            _ = request;
            return JsonSerializer.Deserialize<IsolatedWorkerResponse>(text, Json);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or JsonException)
        {
            // Malformed or unreadable: treated exactly like "no answer".
            return null;
        }
    }

    private IsolatedWorkerOutcome Finish(
        WorkerOutcomeClass outcomeClass,
        IsolatedWorkerResponse? response,
        string? failureCode,
        string? failureReason,
        List<string> evidence,
        Stopwatch started,
        IsolatedWorkerRequest request,
        bool dispatchedFlag)
    {
        evidence.Add($"dispatched={dispatchedFlag.ToString().ToLowerInvariant()}");
        evidence.Add($"outcome_class={outcomeClass}");
        var cleaned = CleanupScratch(request, response);
        evidence.Add($"temporary_artifacts_removed={cleaned.ToString().ToLowerInvariant()}");
        if (outcomeClass == WorkerOutcomeClass.PostDispatchUnknownOutcome)
            _logger?.LogWarning(
                "Isolated worker {Capability}/{Operation} ended with an unknown outcome; "
                + "reconciliation is required before any retry",
                request.Capability,
                request.Operation);
        return new IsolatedWorkerOutcome(
            outcomeClass,
            response,
            failureCode,
            failureReason,
            evidence,
            started.ElapsedMilliseconds,
            cleaned);
    }

    /// <summary>Removes the worker's control files and anything it declared
    /// temporary. Runs on every path — success, failure, timeout, cancel.</summary>
    private static bool CleanupScratch(
        IsolatedWorkerRequest request,
        IsolatedWorkerResponse? response)
    {
        var removed = true;
        foreach (var relative in (response?.TemporaryArtifacts ?? [])
                 .Concat([RequestFileName, ResponseFileName]))
        {
            try
            {
                var full = Path.GetFullPath(Path.Combine(request.WorkingDirectory, relative));
                // Never follow a path the worker invented out of its own directory.
                if (!full.StartsWith(
                        Path.TrimEndingDirectorySeparator(
                            Path.GetFullPath(request.WorkingDirectory))
                        + Path.DirectorySeparatorChar,
                        StringComparison.OrdinalIgnoreCase))
                    continue;
                if (File.Exists(full))
                    File.Delete(full);
                else if (Directory.Exists(full))
                    Directory.Delete(full, recursive: true);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                removed = false;
            }
        }
        return removed;
    }

    /// <summary>Only the fields a worker is allowed to see. Host paths outside the
    /// working directory, credentials, and trust decisions are deliberately absent.</summary>
    private sealed record WireRequest(
        int ProtocolVersion,
        string RunId,
        string StepId,
        string AttemptId,
        string IdempotencyKey,
        string Operation,
        string PayloadJson,
        string Sensitivity,
        long DeadlineUnixMs)
    {
        public static WireRequest From(IsolatedWorkerRequest request) =>
            new(
                ResponseProtocolVersion,
                request.RunId,
                request.StepId,
                request.AttemptId,
                request.IdempotencyKey,
                request.Operation,
                request.PayloadJson,
                request.Sensitivity,
                DateTimeOffset.UtcNow.Add(request.Quotas.Deadline).ToUnixTimeMilliseconds());
    }
}
