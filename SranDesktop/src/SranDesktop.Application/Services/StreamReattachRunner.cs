using System.Net;
using System.Net.Http;
using SranDesktop.Application.Events;

namespace SranDesktop.Application.Services;

/// <summary>How a reattach run ended (Phase 5 Task 3).</summary>
public enum ReattachOutcome
{
    /// <summary>Single-flight: a reattach for this key is already running.</summary>
    AlreadyRunning,

    /// <summary>message_complete arrived — the reply finished streaming.</summary>
    Completed,

    /// <summary>The server reported a normal turn error (already forwarded to
    /// the caller's event handler) — terminal, but not a reattach failure.</summary>
    Errored,

    /// <summary>Terminal loss: the generation died server-side
    /// (GENERATION_INTERRUPTED), the message/conversation is gone (404), or
    /// every attempt failed.</summary>
    Lost,

    Cancelled
}

/// <summary>
/// Phase 5 Task 3: drives reattaching to an IN-FLIGHT server-side generation
/// with up to <see cref="MaxAttempts"/> attempts at 0/1s/2s/4s backoff.
/// The target is reset before EVERY attempt (each attempt re-delivers the full
/// already-generated prefix as one delta, so a retry must start from an empty
/// bubble or text would duplicate), and at most one run per key is in flight
/// (single-flight guard). Delays are injectable so tests run instantly.
/// </summary>
public sealed class StreamReattachRunner
{
    public const string CodeGenerationInterrupted = "GENERATION_INTERRUPTED";
    public const string CodeResumeLagged = "RESUME_LAGGED";
    public const int MaxAttempts = 4;

    private static readonly TimeSpan[] BackoffDelays =
    {
        TimeSpan.Zero, TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(4)
    };

    private readonly Func<TimeSpan, CancellationToken, Task> _delay;
    private readonly object _gate = new();
    private readonly HashSet<string> _active = new(StringComparer.Ordinal);

    public StreamReattachRunner(Func<TimeSpan, CancellationToken, Task>? delay = null)
    {
        _delay = delay ?? Task.Delay;
    }

    /// <summary>
    /// Runs reattach attempts until the stream completes, terminally fails, or
    /// the attempt budget is spent. <paramref name="attemptFactory"/> opens a
    /// fresh reattach stream per attempt; <paramref name="resetTarget"/> empties
    /// the consumer's bubble before each attempt; <paramref name="onEvent"/>
    /// receives every stream event EXCEPT the reattach control errors
    /// (RESUME_LAGGED → silent fresh attempt, GENERATION_INTERRUPTED → Lost).
    /// Retryable failures: HTTP 409 (RESUME_UNAVAILABLE), transport drops, and
    /// a stream that ends without message_complete. Terminal: 404/410.
    /// No ConfigureAwait(false) here — callers rely on continuations staying on
    /// their SynchronizationContext (the WPF UI thread).
    /// </summary>
    public async Task<ReattachOutcome> RunAsync(
        string key,
        Func<CancellationToken, IAsyncEnumerable<ChatStreamEvent>> attemptFactory,
        Action resetTarget,
        Func<ChatStreamEvent, Task> onEvent,
        CancellationToken ct = default)
    {
        lock (_gate)
        {
            if (!_active.Add(key))
                return ReattachOutcome.AlreadyRunning;
        }

        try
        {
            for (var attempt = 0; attempt < MaxAttempts; attempt++)
            {
                try
                {
                    await _delay(BackoffDelays[attempt], ct);
                }
                catch (OperationCanceledException)
                {
                    return ReattachOutcome.Cancelled;
                }

                // Empty bubble per attempt: the prefix re-arrives in full.
                resetTarget();

                var completed = false;
                var errored = false;
                var lagged = false;
                var interrupted = false;
                try
                {
                    await foreach (var evt in attemptFactory(ct))
                    {
                        if (evt is StreamErrorEvent { Code: CodeGenerationInterrupted })
                        {
                            interrupted = true;
                            break;
                        }
                        if (evt is StreamErrorEvent { Code: CodeResumeLagged })
                        {
                            lagged = true; // this subscriber was dropped — reattach fresh
                            break;
                        }

                        if (evt is StreamCompletedEvent)
                            completed = true;
                        else if (evt is StreamErrorEvent)
                            errored = true;
                        await onEvent(evt);
                    }
                }
                catch (OperationCanceledException)
                {
                    return ReattachOutcome.Cancelled;
                }
                catch (Exception ex) when (IsGone(ex))
                {
                    return ReattachOutcome.Lost; // message/conversation gone
                }
                catch (Exception ex) when (IsRetryable(ex))
                {
                    continue; // 409 not-attachable-yet / transport drop → back off
                }

                if (interrupted)
                    return ReattachOutcome.Lost;
                if (completed)
                    return ReattachOutcome.Completed;
                if (errored)
                    return ReattachOutcome.Errored;
                if (lagged)
                    continue; // dropped subscriber — reattach fresh
                // Stream ended without message_complete → also retry.
            }

            return ReattachOutcome.Lost;
        }
        finally
        {
            lock (_gate)
                _active.Remove(key);
        }
    }

    private static bool IsGone(Exception ex) =>
        ex is HttpRequestException { StatusCode: HttpStatusCode.NotFound or HttpStatusCode.Gone };

    private static bool IsRetryable(Exception ex) =>
        ex is IOException || (ex is HttpRequestException && !IsGone(ex));
}
