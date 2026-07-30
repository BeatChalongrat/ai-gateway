using System.Collections.Concurrent;
using System.Diagnostics;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Workers;

/// <summary>
/// A long-lived, bidirectional conversation with one sandboxed worker process.
///
/// The design centres on one question: for every request, can we prove the
/// worker never saw it? Requests are tracked by <see cref="DispatchStage"/>, and
/// the moment bytes start leaving for the worker's stdin the answer becomes "no"
/// — so a disconnect from that point on is <see cref="InteractiveRequestOutcome.UnknownOutcome"/>,
/// never a clean failure. Anything still queued is provably effect-free and may
/// be retried.
/// </summary>
public sealed class InteractiveWorkerSession : IAsyncDisposable
{
    private static long _generationCounter;

    private readonly WindowsAppContainerProcessRunner.InteractiveIsolatedProcess _process;
    private readonly ILogger? _logger;
    private readonly Action<string, object?>? _durableEvent;
    private readonly ConcurrentDictionary<string, PendingRequest> _pending = new();
    private readonly ConcurrentDictionary<string, byte> _seenResponses = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly CancellationTokenSource _lifetime = new();
    private readonly List<InteractiveFrame> _notifications = [];
    private readonly object _stateLock = new();
    private readonly long _stderrQuota;

    private Task? _readLoop;
    private Task? _stderrLoop;
    private long _stderrBytes;
    private long _nextRequestNumber;
    private InteractiveSessionState _state = InteractiveSessionState.Created;
    private int _disposed;

    public InteractiveWorkerSession(
        WindowsAppContainerProcessRunner.InteractiveIsolatedProcess process,
        string workerId,
        string? runId = null,
        string? stepId = null,
        ILogger? logger = null,
        Action<string, object?>? durableEvent = null,
        long stderrQuota = 256 * 1024)
    {
        _process = process;
        _logger = logger;
        _durableEvent = durableEvent;
        _stderrQuota = stderrQuota;
        SessionId = $"session_{Guid.NewGuid():N}";
        WorkerId = workerId;
        RunId = runId;
        StepId = stepId;
        Generation = Interlocked.Increment(ref _generationCounter);
        CreatedUtc = DateTimeOffset.UtcNow;
        LastHeartbeatUtc = CreatedUtc;
    }

    public string SessionId { get; }
    public string WorkerId { get; }
    public string? RunId { get; }
    public string? StepId { get; }
    public long Generation { get; }
    public DateTimeOffset CreatedUtc { get; }
    public DateTimeOffset LastHeartbeatUtc { get; private set; }
    public string WorkerVersion { get; private set; } = "unknown";
    public int ProcessId => _process.ProcessId;
    public int PendingCount => _pending.Count;
    public IReadOnlyList<InteractiveFrame> Notifications
    {
        get { lock (_stateLock) return _notifications.ToList(); }
    }

    public InteractiveSessionState State
    {
        get { lock (_stateLock) return _state; }
    }

    public InteractiveWorkerSessionInfo Info => new(
        SessionId,
        WorkerId,
        ProcessId,
        RunId,
        StepId,
        WorkerVersion,
        _process.GetType().Name,
        "appcontainer:SRAN.Desktop.CommandSandbox.v1",
        Generation,
        CreatedUtc,
        LastHeartbeatUtc,
        State);

    /// <summary>Starts the reader loops and completes the handshake. A worker that
    /// never says hello leaves the session in StartFailed, not Ready.</summary>
    public async Task StartAsync(TimeSpan handshakeTimeout, CancellationToken ct = default)
    {
        Transition(InteractiveSessionState.Starting);
        Emit(InteractiveWorkerEvents.SessionStarting, new { SessionId, ProcessId });

        _readLoop = Task.Run(ReadLoopAsync);
        _stderrLoop = Task.Run(DrainStandardErrorAsync);
        Transition(InteractiveSessionState.Handshaking);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
        timeout.CancelAfter(handshakeTimeout);
        try
        {
            var hello = await SendAsync("$/handshake", payload: null, timeout.Token);
            if (!hello.Succeeded)
                throw new InteractiveProtocolException(
                    "HandshakeFailed",
                    hello.ErrorMessage ?? "The worker did not complete the handshake.");
            WorkerVersion = hello.Payload ?? "unknown";
        }
        catch (Exception ex)
        {
            Transition(InteractiveSessionState.StartFailed);
            _logger?.LogWarning(
                "Interactive worker {Worker} failed to start: {Message}", WorkerId, ex.Message);
            throw;
        }

        // Safe only now: applying the CPU cap before the runtime has initialized
        // fails CLR startup inside the lowbox.
        _process.ApplyCpuQuota();
        Transition(InteractiveSessionState.Ready);
        Emit(InteractiveWorkerEvents.SessionReady, new { SessionId, WorkerVersion });
    }

    /// <summary>
    /// Sends one request and awaits its correlated response. Multiple calls may be
    /// in flight at once; responses are matched by request id and may arrive in
    /// any order, interleaved with notifications.
    /// </summary>
    public async Task<InteractiveRequestResult> SendAsync(
        string method,
        string? payload,
        CancellationToken ct = default,
        TimeSpan? timeout = null,
        string? attemptId = null)
    {
        if (!InteractiveSessionStateMachine.CanAcceptRequests(State)
            && State != InteractiveSessionState.Handshaking)
            return new InteractiveRequestResult(
                InteractiveRequestOutcome.FailedNoEffect,
                DispatchStage.Queued,
                null,
                "SessionNotReady",
                $"The session is {State} and cannot accept requests.",
                0);

        var requestId = $"req_{Interlocked.Increment(ref _nextRequestNumber)}_{Generation}";
        var pending = new PendingRequest(requestId, attemptId);
        if (!_pending.TryAdd(requestId, pending))
            return new InteractiveRequestResult(
                InteractiveRequestOutcome.FailedNoEffect,
                DispatchStage.Queued,
                null,
                "DuplicateRequestId",
                "A request id collided with one already in flight.",
                0);

        var watch = Stopwatch.StartNew();
        Emit(InteractiveWorkerEvents.RequestQueued, new { SessionId, requestId, method });

        var frame = new InteractiveFrame
        {
            MessageType = "request",
            SessionId = SessionId,
            RequestId = requestId,
            RunId = RunId,
            StepId = StepId,
            AttemptId = attemptId,
            Generation = Generation,
            Method = method,
            Payload = payload
        };

        try
        {
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
            if (timeout is { } window)
                linked.CancelAfter(window);

            try
            {
                await _writeLock.WaitAsync(linked.Token);
            }
            catch (OperationCanceledException)
            {
                // Never reached the pipe.
                return Finish(pending, watch, new InteractiveRequestResult(
                    ct.IsCancellationRequested
                        ? InteractiveRequestOutcome.CancelledBeforeDispatch
                        : InteractiveRequestOutcome.TimedOut,
                    DispatchStage.Queued,
                    null,
                    "NotDispatched",
                    "The request was never written to the worker.",
                    watch.ElapsedMilliseconds));
            }

            try
            {
                pending.Stage = DispatchStage.Writing;
                Emit(InteractiveWorkerEvents.DispatchStarted, new { SessionId, requestId });
                var bytes = InteractiveFrameCodec.Encode(frame);
                // Deliberately NOT cancellable: aborting a half-written frame
                // would desynchronize the stream for every other request.
                await _process.StandardInput.WriteAsync(bytes, CancellationToken.None);
                await _process.StandardInput.FlushAsync(CancellationToken.None);
                pending.Stage = DispatchStage.Dispatched;
            }
            catch (Exception ex) when (ex is IOException or ObjectDisposedException)
            {
                // The worker died mid-write: it may have parsed a prefix.
                Emit(InteractiveWorkerEvents.UnknownOutcomeDetected,
                    new { SessionId, requestId, stage = pending.Stage.ToString() });
                return Finish(pending, watch, new InteractiveRequestResult(
                    InteractiveRequestOutcome.UnknownOutcome,
                    pending.Stage,
                    null,
                    "WorkerDisconnectedDuringWrite",
                    "The worker disconnected while the request was being written; "
                    + "whether it acted is unknown.",
                    watch.ElapsedMilliseconds));
            }
            finally
            {
                _writeLock.Release();
            }

            await using var registration = linked.Token.Register(() =>
            {
                // Cancellation AFTER dispatch cannot undo what the worker may
                // already have done.
                var cancelled = ct.IsCancellationRequested;
                pending.Completion.TrySetResult(new InteractiveRequestResult(
                    cancelled
                        ? InteractiveRequestOutcome.CancelledAfterDispatchUnknown
                        : InteractiveRequestOutcome.UnknownOutcome,
                    pending.Stage,
                    null,
                    cancelled ? "CancelledAfterDispatch" : "RequestTimedOut",
                    cancelled
                        ? "The request was cancelled after it reached the worker."
                        : "The worker did not answer in time; the effect is unknown.",
                    watch.ElapsedMilliseconds));
                Emit(
                    cancelled
                        ? InteractiveWorkerEvents.CancellationRequested
                        : InteractiveWorkerEvents.RequestTimedOut,
                    new { SessionId, requestId });
                _ = SendCancelAsync(requestId);
            });

            var result = await pending.Completion.Task;
            return Finish(pending, watch, result);
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    /// <summary>Best-effort protocol cancellation. A worker that ignores it still
    /// leaves the request classified as unknown, never as cancelled-confirmed.</summary>
    private async Task SendCancelAsync(string requestId)
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            await _writeLock.WaitAsync(timeout.Token);
            try
            {
                var bytes = InteractiveFrameCodec.Encode(new InteractiveFrame
                {
                    MessageType = "cancel",
                    SessionId = SessionId,
                    RequestId = requestId,
                    Generation = Generation
                });
                await _process.StandardInput.WriteAsync(bytes, timeout.Token);
                await _process.StandardInput.FlushAsync(timeout.Token);
            }
            finally
            {
                _writeLock.Release();
            }
        }
        catch (Exception ex) when (
            ex is IOException or ObjectDisposedException or OperationCanceledException)
        {
        }
    }

    private async Task ReadLoopAsync()
    {
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var frame = await InteractiveFrameCodec.ReadAsync(
                    _process.StandardOutput, _lifetime.Token);
                if (frame is null)
                    break; // clean EOF or truncated frame — the worker is gone
                Handle(frame);
            }
            FailAllPending(
                "WorkerDisconnected",
                "The worker closed its output before answering.");
            if (!InteractiveSessionStateMachine.IsTerminal(State))
                Transition(InteractiveSessionState.Crashed);
            Emit(InteractiveWorkerEvents.WorkerDisconnected, new { SessionId, ProcessId });
        }
        catch (OperationCanceledException)
        {
            FailAllPending("SessionStopped", "The session was stopped.");
        }
        catch (InteractiveProtocolException ex)
        {
            // Integrity is gone: there is no safe way to keep reading a stream we
            // can no longer parse, so the session dies rather than guessing.
            _logger?.LogWarning(
                "Interactive worker {Worker} sent a malformed frame ({Code}); "
                + "terminating the session", WorkerId, ex.Code);
            Transition(InteractiveSessionState.ProtocolFailed);
            FailAllPending(ex.Code, "The worker violated the framing protocol.");
            _process.Terminate();
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException)
        {
            FailAllPending("WorkerDisconnected", "The worker's output stream failed.");
            if (!InteractiveSessionStateMachine.IsTerminal(State))
                Transition(InteractiveSessionState.Crashed);
        }
    }

    private void Handle(InteractiveFrame frame)
    {
        LastHeartbeatUtc = DateTimeOffset.UtcNow;

        if (frame.Generation != 0 && frame.Generation != Generation)
        {
            // A frame from a previous process instance must never satisfy a
            // request belonging to this one.
            _logger?.LogWarning(
                "Rejected a frame from generation {Frame} on session generation {Session}",
                frame.Generation,
                Generation);
            return;
        }

        switch (frame.MessageType)
        {
            case "notification":
            case "progress":
                lock (_stateLock)
                {
                    if (_notifications.Count < 1000)
                        _notifications.Add(frame);
                }
                Emit(InteractiveWorkerEvents.NotificationReceived,
                    new { SessionId, frame.Method });
                return;

            case "heartbeat":
                return;

            case "ack":
                if (frame.RequestId is { Length: > 0 } ackId
                    && _pending.TryGetValue(ackId, out var acked))
                {
                    acked.Stage = DispatchStage.Acknowledged;
                    Emit(InteractiveWorkerEvents.DispatchAcknowledged,
                        new { SessionId, requestId = ackId });
                }
                return;
        }

        if (frame.RequestId is not { Length: > 0 } requestId)
            return;

        if (!_seenResponses.TryAdd(requestId, 0))
        {
            // A second answer for one request means the worker is confused; the
            // first answer already resolved the caller.
            _logger?.LogWarning(
                "Rejected a duplicate response for {RequestId} on session {Session}",
                requestId,
                SessionId);
            return;
        }

        if (!_pending.TryGetValue(requestId, out var pending))
        {
            // Arrived after the caller gave up (timeout/cancel) — dropping it is
            // correct; the outcome was already classified as unknown.
            _logger?.LogDebug(
                "Discarded a late response for {RequestId} on session {Session}",
                requestId,
                SessionId);
            return;
        }

        pending.Stage = DispatchStage.Completed;
        Emit(InteractiveWorkerEvents.ResponseReceived, new { SessionId, requestId });
        pending.Completion.TrySetResult(frame.MessageType == "error"
            ? new InteractiveRequestResult(
                InteractiveRequestOutcome.FailedByWorker,
                DispatchStage.Completed,
                null,
                frame.ErrorCode ?? "WorkerError",
                frame.ErrorMessage,
                0)
            : new InteractiveRequestResult(
                InteractiveRequestOutcome.Succeeded,
                DispatchStage.Completed,
                frame.Payload,
                null,
                null,
                0));
    }

    /// <summary>stderr is drained continuously (an unread pipe would eventually
    /// block the worker) and capped, so a flooding worker cannot exhaust us.</summary>
    private async Task DrainStandardErrorAsync()
    {
        var buffer = new byte[8192];
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var read = await _process.StandardError.ReadAsync(buffer, _lifetime.Token);
                if (read == 0)
                    return;
                if (Interlocked.Add(ref _stderrBytes, read) > _stderrQuota)
                {
                    _logger?.LogWarning(
                        "Interactive worker {Worker} exceeded its stderr quota; terminating",
                        WorkerId);
                    Transition(InteractiveSessionState.ProtocolFailed);
                    FailAllPending(
                        "StderrQuotaExceeded", "The worker flooded its error stream.");
                    _process.Terminate();
                    return;
                }
            }
        }
        catch (Exception ex) when (
            ex is OperationCanceledException or IOException or ObjectDisposedException)
        {
        }
    }

    /// <summary>Ends the session. Requests still in flight had already reached the
    /// worker, so they resolve as unknown — shutting down does not make them
    /// safe to repeat.</summary>
    public async Task StopAsync(TimeSpan grace)
    {
        if (InteractiveSessionStateMachine.IsTerminal(State))
            return;
        Transition(InteractiveSessionState.Draining);
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            await _writeLock.WaitAsync(timeout.Token);
            try
            {
                var bytes = InteractiveFrameCodec.Encode(new InteractiveFrame
                {
                    MessageType = "shutdown",
                    SessionId = SessionId,
                    Generation = Generation
                });
                await _process.StandardInput.WriteAsync(bytes, timeout.Token);
                await _process.StandardInput.FlushAsync(timeout.Token);
            }
            finally
            {
                _writeLock.Release();
            }
            using var graceWindow = new CancellationTokenSource(grace);
            await _process.WaitForExitAsync(graceWindow.Token);
        }
        catch (Exception ex) when (
            ex is IOException or ObjectDisposedException or OperationCanceledException
                or InvalidOperationException)
        {
            _process.Terminate();
        }

        FailAllPending("SessionStopped", "The session stopped before this request answered.");
        Transition(InteractiveSessionState.Stopped);
        Emit(InteractiveWorkerEvents.SessionStopped, new { SessionId });
    }

    private void FailAllPending(string code, string reason)
    {
        foreach (var key in _pending.Keys.ToList())
        {
            if (!_pending.TryGetValue(key, out var pending))
                continue;
            // Anything that reached the worker is unknown; anything still queued
            // provably did nothing.
            var dispatched = pending.Stage >= DispatchStage.Writing;
            pending.Completion.TrySetResult(new InteractiveRequestResult(
                dispatched
                    ? InteractiveRequestOutcome.UnknownOutcome
                    : InteractiveRequestOutcome.FailedNoEffect,
                pending.Stage,
                null,
                code,
                reason,
                0));
            if (dispatched)
                Emit(InteractiveWorkerEvents.UnknownOutcomeDetected,
                    new { SessionId, requestId = key, stage = pending.Stage.ToString() });
        }
    }

    private InteractiveRequestResult Finish(
        PendingRequest pending,
        Stopwatch watch,
        InteractiveRequestResult result)
    {
        _pending.TryRemove(pending.RequestId, out _);
        return result with { DurationMs = watch.ElapsedMilliseconds };
    }

    private void Transition(InteractiveSessionState next)
    {
        lock (_stateLock)
        {
            if (!InteractiveSessionStateMachine.CanTransition(_state, next))
                return;
            _state = next;
        }
    }

    private void Emit(string eventName, object? data) => _durableEvent?.Invoke(eventName, data);

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
            return;
        await StopAsync(TimeSpan.FromSeconds(3));
        _lifetime.Cancel();
        foreach (var loop in new[] { _readLoop, _stderrLoop })
        {
            if (loop is not null)
                await Task.WhenAny(loop, Task.Delay(TimeSpan.FromSeconds(2)));
        }
        await _process.DisposeAsync();
        _lifetime.Dispose();
        _writeLock.Dispose();
        Emit(InteractiveWorkerEvents.CleanupCompleted, new { SessionId });
    }

    private sealed class PendingRequest(string requestId, string? attemptId)
    {
        public string RequestId { get; } = requestId;
        public string? AttemptId { get; } = attemptId;
        public DispatchStage Stage { get; set; } = DispatchStage.Queued;
        public TaskCompletionSource<InteractiveRequestResult> Completion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
}
