namespace SranDesktop.Application.Services;

/// <summary>
/// R4 (round 2a): the single source of truth for which tool FailureCodes are TRANSIENT —
/// safe for the runtime to auto-retry after taking a fresh observation. Everything else
/// (logic / policy errors — UnboundInput, credential-like refusals, capture/target
/// validation, value mismatch, and the runtime's own budget / blind-retry guards) is
/// terminal and must surface red immediately, keeping R5's human-error intact.
///
/// Deliberately NOT retryable and why:
/// - <c>UnboundInput</c> / missing args — a caller mistake; a fresh screenshot won't fix it.
/// - credential-like refusals — a safety decision, never retried.
/// - capture/window validation (<c>WrongWindow</c>, <c>HostWindowTargeted</c>, …) — targeting
///   a logically wrong window; retrying the same target repeats the error.
/// - <c>EffectMismatch</c> / <c>ReadBackUnavailable</c> — these are UNVERIFIED (amber, R3′),
///   never red, so they are outside the auto-retry (red) path entirely.
/// - <c>RetryBudgetExceeded</c> / <c>BlindRetryBlocked</c> — the runtime's own stop signals.
/// </summary>
public static class TransientFailurePolicy
{
    /// <summary>True when <paramref name="failureCode"/> is a transient computer-use failure
    /// that a fresh observation + re-dispatch can plausibly clear.</summary>
    public static bool IsRetryable(string? failureCode) =>
        failureCode is
            "FocusLost"           // foreground stolen mid-dispatch
            or "TargetNotFound"   // the control was not present in the observation used
            or "StaleObservation" // the capture went stale before dispatch
            or "StaleWindow"      // readiness: the expected window vanished / not ready
            or "CaptureStale"     // click: the capture went stale — re-observe and retry
            or "ApplicationNotReady"; // readiness: app was not input-ready within the wait

    /// <summary>Pull the leading FailureCode token out of a tool error/result string. Tools
    /// prefix hard failures as <c>"Error: &lt;Code&gt;: reason"</c> or <c>"&lt;Code&gt;: reason"</c>.</summary>
    public static string? ExtractCode(string? errorText)
    {
        if (string.IsNullOrEmpty(errorText)) return null;
        var text = errorText;
        const string prefix = "Error: ";
        if (text.StartsWith(prefix, System.StringComparison.Ordinal))
            text = text[prefix.Length..];
        var colon = text.IndexOf(':');
        return (colon > 0 ? text[..colon] : text).Trim();
    }

    /// <summary>Convenience: is this tool error/result string a retryable transient?</summary>
    public static bool IsRetryableError(string? errorText) =>
        IsRetryable(ExtractCode(errorText));
}
