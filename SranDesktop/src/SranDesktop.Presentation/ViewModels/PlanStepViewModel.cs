using SranDesktop.Application.Services;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>One row in the model-maintained plan checklist (Phase A PART 5).</summary>
public sealed class PlanStepViewModel
{
    public string Text { get; }
    public string Status { get; }

    public bool IsPending => Status == PlanStep.Pending;
    public bool IsInProgress => Status == PlanStep.InProgress;
    public bool IsDone => Status == PlanStep.Done;

    public PlanStepViewModel(string text, string status)
    {
        Text = text;
        Status = status;
    }
}
