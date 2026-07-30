namespace SranDesktop.Infrastructure.Native;

/// <summary>The result of a real-GUI automation permission check.</summary>
public sealed record RealGuiGuardDecision(
    bool Permitted,
    string Reason,
    IReadOnlyList<string> Checks);

/// <summary>
/// Hard gate for real-GUI desktop automation (the RS-001 / regression-matrix
/// runner). It exists because real automation types into, clicks, closes, and
/// reads live windows: run on the wrong machine and it corrupts the operator's
/// actual work — a real past incident had a smoke test overwrite a live Notepad
/// tab. The guard therefore DENIES BY DEFAULT and only permits when an operator
/// has deliberately provisioned a throwaway, isolated Windows session and
/// declared it THREE independent ways that must all agree:
///
///   1. an exact confirmation token in <see cref="OptInVariable"/>,
///   2. a declared sandbox host (<see cref="SandboxHostVariable"/>) equal to
///      this machine's name — so a stray inherited env var can't target an
///      undeclared desktop,
///   3. an on-disk sentinel marker (<see cref="MarkerPathVariable"/>) carrying
///      the same token AND this machine's name — provisioned only on the VM.
///
/// No single leaked variable is enough; the marker file must be authored on the
/// target machine on purpose. The guard performs NO GUI work itself and is fully
/// unit-testable via its injected environment accessors.
/// </summary>
public sealed class RealGuiEnvironmentGuard
{
    public const string OptInVariable = "SRAN_ALLOW_REAL_GUI_AUTOMATION";
    public const string SandboxHostVariable = "SRAN_GUI_SANDBOX_HOST";
    public const string MarkerPathVariable = "SRAN_GUI_SANDBOX_MARKER";

    /// <summary>The exact token an operator must set, in env and marker, to
    /// acknowledge that real-GUI automation destroys live GUI state.</summary>
    public const string RequiredToken = "I-UNDERSTAND-THIS-DESTROYS-GUI-STATE";

    private readonly Func<string, string?> _env;
    private readonly Func<string> _machineName;
    private readonly Func<string, bool> _fileExists;
    private readonly Func<string, string> _readFile;

    public RealGuiEnvironmentGuard(
        Func<string, string?>? env = null,
        Func<string>? machineName = null,
        Func<string, bool>? fileExists = null,
        Func<string, string>? readFile = null)
    {
        _env = env ?? Environment.GetEnvironmentVariable;
        _machineName = machineName ?? (() => Environment.MachineName);
        _fileExists = fileExists ?? File.Exists;
        _readFile = readFile ?? File.ReadAllText;
    }

    public RealGuiGuardDecision Evaluate()
    {
        var checks = new List<string>();

        // 1) exact opt-in token — a bare "1"/"true" is intentionally NOT enough.
        var optIn = _env(OptInVariable);
        checks.Add($"{OptInVariable}=" + (optIn is null
            ? "<unset>"
            : string.Equals(optIn, RequiredToken, StringComparison.Ordinal) ? "token-ok" : "wrong-value"));
        if (!string.Equals(optIn, RequiredToken, StringComparison.Ordinal))
            return Deny(
                "Real-GUI automation is opt-in only and the exact confirmation token is not set.",
                checks);

        // 2) declared sandbox host must equal this machine.
        var declaredHost = _env(SandboxHostVariable);
        var machine = _machineName();
        checks.Add($"declared_host={declaredHost ?? "<unset>"};machine={machine}");
        if (string.IsNullOrWhiteSpace(declaredHost)
            || !string.Equals(declaredHost.Trim(), machine, StringComparison.OrdinalIgnoreCase))
            return Deny(
                "The declared sandbox host does not match this machine; refusing to target an undeclared desktop.",
                checks);

        // 3) on-disk sentinel marker, authored on the VM, carrying token + host.
        var markerPath = _env(MarkerPathVariable);
        checks.Add($"marker_path={markerPath ?? "<unset>"}");
        if (string.IsNullOrWhiteSpace(markerPath) || !_fileExists(markerPath))
            return Deny(
                "The sandbox sentinel marker file is missing; provision it on the isolated VM only.",
                checks);

        string marker;
        try
        {
            marker = _readFile(markerPath);
        }
        catch (Exception ex)
        {
            return Deny($"The sandbox marker could not be read: {ex.Message}", checks);
        }
        if (!marker.Contains(RequiredToken, StringComparison.Ordinal))
            return Deny("The sandbox marker does not carry the confirmation token.", checks);
        if (!marker.Contains(machine, StringComparison.OrdinalIgnoreCase))
            return Deny("The sandbox marker was not authored for this machine.", checks);

        checks.Add("marker=token-and-host-ok");
        return new RealGuiGuardDecision(
            true,
            "Isolated GUI sandbox confirmed by opt-in token, host match, and on-disk marker.",
            checks);
    }

    /// <summary>Throws unless real-GUI automation is permitted. Every real-GUI
    /// runner MUST call this before touching a window.</summary>
    public void EnsurePermitted()
    {
        var decision = Evaluate();
        if (!decision.Permitted)
            throw new InvalidOperationException(
                "Real-GUI automation is blocked: " + decision.Reason +
                " [" + string.Join("; ", decision.Checks) + "]");
    }

    private static RealGuiGuardDecision Deny(string reason, List<string> checks) =>
        new(false, reason, checks);
}
