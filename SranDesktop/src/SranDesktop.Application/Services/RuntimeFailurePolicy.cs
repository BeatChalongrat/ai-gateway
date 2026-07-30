using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed record RuntimeFailureDecision(
    RuntimeFailureClassification Classification,
    DurableToolOutcome Outcome,
    bool RetryAllowed,
    bool ReconciliationRequired,
    TimeSpan Backoff,
    string Reason);

/// <summary>
/// Central failure and retry policy. Unknown or possibly-effectful outcomes
/// never become blind retries, and a policy denial never becomes retryable
/// without a new authorization decision.
/// </summary>
public static class RuntimeFailurePolicy
{
    public static RuntimeFailureDecision Classify(
        ToolResult result,
        SideEffectClass sideEffect,
        bool dispatched = true)
    {
        if (result.Success && result.Effect?.Verified == true)
            return Decision(
                RuntimeFailureClassification.None,
                DurableToolOutcome.Succeeded,
                false,
                false,
                "The effect is verified.");
        if (result.ComputerAction?.FailureCode is "StaleObservation" or "StaleWindow"
            or "CaptureStale" or "TargetNotFound" or "FocusLost"
            || result.Effect?.FailureCode is "StaleObservation" or "StaleWindow"
                or "CaptureStale" or "TargetNotFound" or "FocusLost")
        {
            return Decision(
                RuntimeFailureClassification.StaleState,
                DurableToolOutcome.FailedTransient,
                sideEffect == SideEffectClass.ReadOnly,
                sideEffect != SideEffectClass.ReadOnly,
                "State must be re-observed before a retry.");
        }

        var code = result.Effect?.FailureCode
                   ?? result.ComputerAction?.FailureCode
                   ?? TransientFailurePolicy.ExtractCode(result.Error);
        return code switch
        {
            "PermissionDenied" or "ApprovalRequired" =>
                Decision(RuntimeFailureClassification.PermissionDenied,
                    DurableToolOutcome.FailedPermanent, false, false,
                    "A new user authorization is required."),
            "PolicyDenied" or "CapabilityPolicyRejected" or "DlpBlocked" =>
                Decision(RuntimeFailureClassification.PolicyDenied,
                    DurableToolOutcome.FailedPermanent, false, false,
                    "Security policy denials are never automatically retried."),
            "InvalidArgument" or "InvalidJson" or "UnboundInput" =>
                Decision(RuntimeFailureClassification.InvalidArgument,
                    DurableToolOutcome.FailedNoEffect, false, false,
                    "The request must be corrected before another attempt."),
            "SandboxUnavailable" or "CapabilityUnavailable" =>
                Decision(RuntimeFailureClassification.ResourceUnavailable,
                    DurableToolOutcome.CapabilityUnavailable, false, false,
                    "The capability is unavailable and host fallback is prohibited."),
            "Timeout" when !dispatched =>
                Decision(RuntimeFailureClassification.TimeoutBeforeEffect,
                    DurableToolOutcome.FailedTransient, true, false,
                    "The timeout occurred before dispatch."),
            "Timeout" =>
                Decision(RuntimeFailureClassification.TimeoutAfterPossibleEffect,
                    DurableToolOutcome.UnknownOutcome, false, true,
                    "The timeout followed dispatch and requires reconciliation."),
            "WorkerCrash" or "DurableStateCommitFailed" =>
                Decision(RuntimeFailureClassification.WorkerCrash,
                    DurableToolOutcome.UnknownOutcome, false, true,
                    "Worker failure left the side effect uncertain."),
            "UnknownOutcome" =>
                Decision(RuntimeFailureClassification.UnknownOutcome,
                    DurableToolOutcome.UnknownOutcome, false, true,
                    "Unknown outcomes require reconciliation before retry."),
            _ when result.Success =>
                Decision(RuntimeFailureClassification.None,
                    DurableToolOutcome.FailedPartialEffect, false, true,
                    "Execution succeeded but verification is inconclusive."),
            _ when result.Effect?.Retryable == true && sideEffect == SideEffectClass.ReadOnly =>
                Decision(RuntimeFailureClassification.TransientNetwork,
                    DurableToolOutcome.FailedTransient, true, false,
                    "A bounded retry is allowed for an idempotent read-only operation."),
            _ =>
                Decision(RuntimeFailureClassification.Permanent,
                    DurableToolOutcome.FailedPermanent, false, false,
                    "The failure is not safely retryable.")
        };
    }

    private static RuntimeFailureDecision Decision(
        RuntimeFailureClassification classification,
        DurableToolOutcome outcome,
        bool retry,
        bool reconcile,
        string reason) =>
        new(
            classification,
            outcome,
            retry,
            reconcile,
            retry ? TimeSpan.FromMilliseconds(250) : TimeSpan.Zero,
            reason);
}
