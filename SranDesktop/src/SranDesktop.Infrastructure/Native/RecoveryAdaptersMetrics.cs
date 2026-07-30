namespace SranDesktop.Infrastructure.Native;

public enum RecoveryStrategy
{
    RefreshSemanticTree,
    AlternateSemanticCandidate,
    VerifiedShortcut,
    FreshWindowVisionCapture,
    SafeStop
}

public sealed record RecoveryStep(
    string RetryId,
    string ParentActionId,
    int Attempt,
    RecoveryStrategy Strategy,
    bool RequiresFreshObservation,
    bool MayUseRawPixel);

public static class RecoveryCoordinator
{
    public static RecoveryStep Next(
        string parentActionId, int priorAttempts, int maximumAttempts = 4)
    {
        var attempt = priorAttempts + 1;
        if (attempt > maximumAttempts)
            return new RecoveryStep(
                Guid.NewGuid().ToString("N"), parentActionId, attempt,
                RecoveryStrategy.SafeStop, true, false);
        var strategy = attempt switch
        {
            1 => RecoveryStrategy.RefreshSemanticTree,
            2 => RecoveryStrategy.AlternateSemanticCandidate,
            3 => RecoveryStrategy.VerifiedShortcut,
            _ => RecoveryStrategy.FreshWindowVisionCapture
        };
        return new RecoveryStep(
            Guid.NewGuid().ToString("N"), parentActionId, attempt,
            strategy, true, false);
    }
}

public interface IApplicationAdapter
{
    string Name { get; }
    bool CanHandle(string processName, string windowTitle);
    IReadOnlyList<string> GetSupportedActions();
}

public sealed record DiscordObservation(
    bool WindowIdentityVerified,
    long WindowHandle,
    int ProcessId,
    bool? IsMuted,
    bool MicrophoneControlFound,
    bool TogglePatternSupported);

public sealed record AdapterDecision(
    bool Execute,
    TargetMethod? Method,
    bool TaskCompleted,
    string Reason);

public sealed class DiscordApplicationAdapter : IApplicationAdapter
{
    public string Name => "Discord";

    public bool CanHandle(string processName, string windowTitle) =>
        processName.Contains("discord", StringComparison.OrdinalIgnoreCase)
        && windowTitle.Contains("discord", StringComparison.OrdinalIgnoreCase);

    public IReadOnlyList<string> GetSupportedActions() =>
        new[] { "mute_microphone", "unmute_microphone" };

    public AdapterDecision ResolveMicrophoneState(
        DiscordObservation observation, bool desiredMuted)
    {
        if (!observation.WindowIdentityVerified)
            return new(false, null, false, "Discord HWND/PID identity is not verified.");
        if (observation.IsMuted == desiredMuted)
            return new(false, TargetMethod.ApplicationAdapter, true,
                desiredMuted ? "Microphone is already muted." : "Microphone is already unmuted.");
        if (observation.MicrophoneControlFound && observation.TogglePatternSupported)
            return new(true, TargetMethod.WindowsUia, false,
                "Use TogglePattern, then re-read ToggleState.");
        return new(false, null, false,
            "A verified semantic microphone control was not found; a fresh Discord-window vision capture is required.");
    }

    public bool VerifyMicrophoneEffect(
        bool desiredMuted, DiscordObservation after) =>
        after.WindowIdentityVerified && after.IsMuted == desiredMuted;
}

public enum ComputerUseMetric
{
    TargetResolved,
    SemanticTarget,
    VisionFallback,
    FirstAttemptSuccess,
    EffectVerified,
    TaskCompleted,
    Misclick,
    WrongWindowPrevented,
    StaleCaptureRejected,
    Retry,
    RecoverySucceeded,
    FalseSuccess
}

public sealed class ComputerUseMetrics
{
    private readonly Dictionary<ComputerUseMetric, long> _counts = new();

    public void Increment(ComputerUseMetric metric)
    {
        lock (_counts)
            _counts[metric] = Get(metric) + 1;
    }

    public long Get(ComputerUseMetric metric)
    {
        lock (_counts)
            return _counts.GetValueOrDefault(metric);
    }
}

public sealed record ProductionGateInput(
    long WrongWindowActions,
    long FalseSuccesses,
    double LargeTargetSuccess,
    double MediumTargetSuccess,
    double SmallTargetSuccess,
    double FirstAttemptSuccess,
    double VerifiedWorkflowCompletion,
    bool ConsequentialActionsConfirmedAndVerified);

public static class ProductionGate
{
    public static bool Passes(ProductionGateInput value) =>
        value.WrongWindowActions == 0
        && value.FalseSuccesses == 0
        && value.LargeTargetSuccess >= .99
        && value.MediumTargetSuccess >= .98
        && value.SmallTargetSuccess >= .95
        && value.FirstAttemptSuccess >= .95
        && value.VerifiedWorkflowCompletion >= .95
        && value.ConsequentialActionsConfirmedAndVerified;
}
