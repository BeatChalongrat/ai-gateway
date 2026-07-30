using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One row in the runtime-authoritative outcome checklist.</summary>
public sealed class PlanStepViewModel
{
    public string Text { get; }
    public string Status { get; }

    public bool IsPending => Status is nameof(OutcomeStatus.Pending)
        or nameof(OutcomeStatus.Ready) or PlanStep.Pending;
    public bool IsInProgress => Status is nameof(OutcomeStatus.InProgress)
        or nameof(OutcomeStatus.EffectPending) or PlanStep.InProgress;
    public bool IsDone => Status is nameof(OutcomeStatus.Verified) or PlanStep.Done;
    public bool IsFailed => Status is nameof(OutcomeStatus.Failed)
        or nameof(OutcomeStatus.Blocked)
        or nameof(OutcomeStatus.Cancelled);

    public PlanStepViewModel(string text, string status)
    {
        Text = text;
        Status = status;
    }
}
