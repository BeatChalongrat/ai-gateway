namespace SranDesktop.Application.Interfaces;

/// <summary>Opt-in crash telemetry (R11 A3). No-ops unless the user enabled
/// "Send crash reports" in settings.</summary>
public interface ICrashReportService
{
    /// <summary>True when the opt-in toggle is on AND an endpoint is configured.</summary>
    bool IsEnabled { get; }

    /// <summary>Send a crash report (best effort — never throws). Returns true
    /// when the report was accepted by the server.</summary>
    Task<bool> ReportAsync(Exception exception, bool forceSend = false, CancellationToken ct = default);
}
