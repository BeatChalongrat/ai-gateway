namespace SranDesktop.Application.Services;

/// <summary>
/// Async-flow-only correlation for tools that create child runs. It carries an
/// opaque run id, never permissions or credentials, and restores the prior
/// value when a tool dispatch completes.
/// </summary>
public static class AgentRunExecutionContext
{
    private static readonly AsyncLocal<Correlation?> Current = new();

    public static string? CurrentRunId => Current.Value?.RunId;
    public static string? CurrentStepId => Current.Value?.StepId;

    public static IDisposable Push(string? runId, string? stepId = null)
    {
        var previous = Current.Value;
        Current.Value = new Correlation(runId, stepId);
        return new RestoreScope(previous);
    }

    private sealed record Correlation(string? RunId, string? StepId);

    private sealed class RestoreScope(Correlation? previous) : IDisposable
    {
        private Correlation? _previous = previous;
        private int _disposed;

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0)
                return;
            Current.Value = _previous;
            _previous = null;
        }
    }
}
