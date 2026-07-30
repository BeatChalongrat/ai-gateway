namespace SranDesktop.Application.Services;

/// <summary>
/// Marks the window during which the agent — not the user — is driving the
/// desktop.
///
/// The agent can click, type, and invoke UI Automation patterns. An approval
/// dialog is a window with buttons on it. Without a marker that says "the input
/// arriving right now came from the tool I am running", a connector approval is
/// something the agent can grant itself, and every other control in this phase
/// rests on that approval being a person's decision.
///
/// The cooldown exists because a synthetic click is delivered asynchronously:
/// SendInput returns before the target window has processed the message, so a
/// scope that closed the instant the P/Invoke returned would already be shut by
/// the time the click landed.
/// </summary>
public static class AgentInputScope
{
    /// <summary>How long after synthetic input a decision is still treated as
    /// possibly caused by it. Generous on purpose: the cost of waiting is a
    /// person clicking twice, and the cost of being wrong is an unapproved
    /// connector running arbitrary code.</summary>
    public static TimeSpan Cooldown { get; } = TimeSpan.FromMilliseconds(1500);

    private static int _depth;
    private static long _lastInputTicks;
    private static string _lastDescription = "";

    /// <summary>Opens the scope for the duration of one synthetic action.</summary>
    public static IDisposable Enter(string description)
    {
        Interlocked.Increment(ref _depth);
        Touch(description);
        return new Scope(description);
    }

    /// <summary>Records that synthetic input was just delivered, for callers that
    /// cannot wrap a block — a fire-and-forget SendInput, for instance.</summary>
    public static void Touch(string description)
    {
        Interlocked.Exchange(ref _lastInputTicks, DateTimeOffset.UtcNow.UtcTicks);
        Volatile.Write(ref _lastDescription, description);
    }

    public static bool IsAgentInputInFlight(out string reason)
    {
        if (Volatile.Read(ref _depth) > 0)
        {
            reason = $"agent input in progress: {Volatile.Read(ref _lastDescription)}";
            return true;
        }
        var last = Interlocked.Read(ref _lastInputTicks);
        if (last != 0
            && DateTimeOffset.UtcNow - new DateTimeOffset(last, TimeSpan.Zero) < Cooldown)
        {
            reason = $"agent input within the last {Cooldown.TotalMilliseconds:0} ms: "
                     + Volatile.Read(ref _lastDescription);
            return true;
        }
        reason = "";
        return false;
    }

    /// <summary>Test seam: forget any recorded synthetic input. Never called from
    /// production code — clearing the marker is precisely the move an attacker
    /// would want, so it has no production call site to borrow.</summary>
    internal static void ResetForTests()
    {
        Interlocked.Exchange(ref _depth, 0);
        Interlocked.Exchange(ref _lastInputTicks, 0);
        Volatile.Write(ref _lastDescription, "");
    }

    private sealed class Scope(string description) : IDisposable
    {
        private bool _disposed;

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            // Touch on the way out too: the click is still travelling.
            Touch(description);
            Interlocked.Decrement(ref _depth);
        }
    }
}
