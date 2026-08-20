using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>Terminal outcome of an audio-generation run. UserCancelled
/// distinguishes "declined the premium-cost confirmation" from a real
/// failure — the caller resets silently instead of showing an error.</summary>
public sealed record AudioGenerationResult(
    bool Success,
    byte[]? Audio,
    string? Filename,
    string? ContentType,
    string? ErrorMessage,
    bool UserCancelled = false);

/// <summary>
/// Job-status vocabulary, in one place. The gateway is the authority on the
/// strings; the client only needs to know which of them mean "stop polling".
/// </summary>
public static class MediaJobState
{
    public static bool IsDone(string? status) =>
        string.Equals(status, "done", StringComparison.OrdinalIgnoreCase)
        || string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase)
        || string.Equals(status, "succeeded", StringComparison.OrdinalIgnoreCase);

    public static bool IsFailed(string? status) =>
        string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase)
        || string.Equals(status, "error", StringComparison.OrdinalIgnoreCase)
        || string.Equals(status, "cancelled", StringComparison.OrdinalIgnoreCase)
        || string.Equals(status, "canceled", StringComparison.OrdinalIgnoreCase);

    public static bool IsTerminal(string? status) => IsDone(status) || IsFailed(status);
}

/// <summary>
/// Drives one audio generation: submit → poll → fetch the bytes (Phase 14
/// Task 3).
///
/// Audio is DIRECT DISPATCH, not a model-callable tool — chat-edge has no
/// generate_audio in its registry, only POST /audio/generations. So unlike
/// image and video (composer toggles that bias the model toward a tool), the
/// desktop drives the job itself, exactly as the web AudioGenForm does.
///
/// The delay is injectable so tests run the whole polling loop instantly.
/// </summary>
public sealed class AudioGenerationService
{
    /// <summary>Matches the web form's cadence.</summary>
    public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(2);

    /// <summary>Ceiling on polling. Music generation is slow but not unbounded;
    /// without this a stuck job would poll until the app closed.</summary>
    public const int MaxPolls = 150; // ~5 minutes at 2s

    private readonly IChatEdgeApiClient _api;
    private readonly ILogger _logger;
    private readonly Func<TimeSpan, CancellationToken, Task> _delay;
    private readonly Func<MediaGenSubmitResponse, CancellationToken, Task<bool>>? _confirmPremium;

    public AudioGenerationService(
        IChatEdgeApiClient api,
        ILogger logger,
        Func<TimeSpan, CancellationToken, Task>? delay = null,
        Func<MediaGenSubmitResponse, CancellationToken, Task<bool>>? confirmPremium = null)
    {
        _api = api;
        _logger = logger;
        _delay = delay ?? ((d, ct) => Task.Delay(d, ct));
        _confirmPremium = confirmPremium;
    }

    /// <summary>Progress callback: percent complete when the job reports it.</summary>
    public event EventHandler<int?>? ProgressChanged;

    public async Task<AudioGenerationResult> GenerateAsync(
        AudioGenerationRequest request, CancellationToken ct = default)
    {
        MediaGenSubmitResponse submitted;
        try
        {
            submitted = await _api.SubmitAudioGenerationAsync(request, ct);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Audio generation submit failed");
            return Failure(ex.Message);
        }

        // Premium-cost gate (Batch 1 D1). The threshold is SERVER-owned:
        // requires_confirmation=true means the job is reserved but must not
        // proceed without explicit consent — the same post-submit flow as
        // the web CostConfirmModal (cancel → POST /jobs/{id}/cancel refunds
        // the reservation; confirm → start polling). With no confirm hook
        // wired (headless callers), declining is the safe default.
        if (submitted.RequiresConfirmation)
        {
            var confirmed = false;
            try
            {
                confirmed = _confirmPremium is not null
                    && await _confirmPremium(submitted, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _logger.LogWarning(ex, "Premium confirmation failed; treating as declined");
            }
            if (!confirmed)
            {
                await CancelJobSafeAsync(submitted.JobId);
                return new AudioGenerationResult(
                    false, null, null, null, null, UserCancelled: true);
            }
        }

        // A job can come back already finished (cache hit) — check before
        // sleeping, so a fast result is not delayed by a poll interval.
        var status = new MediaJobStatus(submitted.JobId, submitted.Status);
        for (var poll = 0; poll < MaxPolls; poll++)
        {
            if (MediaJobState.IsTerminal(status.Status))
                break;

            await _delay(PollInterval, ct);
            try
            {
                status = await _api.GetMediaJobAsync(submitted.JobId, ct);
                ProgressChanged?.Invoke(this, status.ProgressPct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                // A transient poll failure is not a job failure — keep going.
                _logger.LogDebug(ex, "Audio job poll failed; retrying");
            }
        }

        if (MediaJobState.IsFailed(status.Status))
            return Failure(status.ErrorMessage);
        if (!MediaJobState.IsDone(status.Status))
            return Failure(null); // ran out of polls — the caller shows "timed out"

        try
        {
            var bytes = await _api.DownloadMediaJobOutputAsync(submitted.JobId, ct);
            if (bytes.Length == 0)
                return Failure(null);
            var isSpeech = string.Equals(request.Mode, "tts", StringComparison.OrdinalIgnoreCase);
            return new AudioGenerationResult(
                true, bytes,
                Filename: $"{(isSpeech ? "speech" : "music")}-{submitted.JobId}.mp3",
                ContentType: "audio/mpeg",
                ErrorMessage: null);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Could not download the generated audio");
            return Failure(ex.Message);
        }
    }

    /// <summary>Best-effort cancel of a declined reservation. Runs on
    /// CancellationToken.None — the refund must not be lost to a racing
    /// user cancel of the surrounding run.</summary>
    private async Task CancelJobSafeAsync(string jobId)
    {
        try
        {
            await _api.CancelMediaJobAsync(jobId, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not cancel declined media job {JobId}", jobId);
        }
    }

    private static AudioGenerationResult Failure(string? message) =>
        new(false, null, null, null, message);
}
