using System.Reflection;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Crash telemetry (R11 A3): POSTs a compact JSON report to the configured
/// endpoint. Respects the opt-in toggle unless the user explicitly clicks
/// "Send crash report" in the crash dialog (forceSend).
/// </summary>
public sealed class CrashReportService : ICrashReportService
{
    private readonly ISettingsService _settings;
    private readonly ILogger<CrashReportService> _logger;
    private readonly string? _endpoint;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly string _appVersion;

    public CrashReportService(ISettingsService settings, string? endpoint,
        ILogger<CrashReportService> logger)
    {
        _settings = settings;
        _endpoint = endpoint;
        _logger = logger;
        _appVersion = Assembly.GetEntryAssembly()?.GetName().Version?.ToString(3) ?? "0.0.0";
    }

    public bool IsEnabled =>
        _settings.Current.CrashReportingEnabled && !string.IsNullOrEmpty(_endpoint);

    public async Task<bool> ReportAsync(Exception exception, bool forceSend = false, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(_endpoint))
            return false;
        if (!forceSend && !_settings.Current.CrashReportingEnabled)
            return false;

        var report = new
        {
            version = _appVersion,
            os = Environment.OSVersion.ToString(),
            dotnet = Environment.Version.ToString(),
            exception = exception.GetType().FullName,
            message = exception.Message,
            stackTrace = Truncate(exception.StackTrace, 8000),
            innerException = exception.InnerException?.Message,
            timestamp = DateTimeOffset.UtcNow
        };

        try
        {
            var json = JsonSerializer.Serialize(report);
            using var response = await _http.PostAsync(_endpoint,
                new StringContent(json, Encoding.UTF8, "application/json"), ct);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            // Never crash the crash reporter.
            _logger.LogDebug(ex, "Crash report delivery failed");
            return false;
        }
    }

    private static string? Truncate(string? value, int max) =>
        value is null || value.Length <= max ? value : value[..max];
}
