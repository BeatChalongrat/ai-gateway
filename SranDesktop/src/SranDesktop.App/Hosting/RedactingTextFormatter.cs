using System.IO;
using Serilog.Events;
using Serilog.Formatting;
using Serilog.Formatting.Display;
using Serilog;
using SranDesktop.Application.Services;

namespace SranDesktop.App.Hosting;

/// <summary>
/// Last-line defense for the normal file log. Structured callers must avoid
/// sensitive payloads; this formatter redacts the fully rendered event,
/// including exception text, before any bytes reach disk.
/// </summary>
internal sealed class RedactingTextFormatter : ITextFormatter
{
    private readonly MessageTemplateTextFormatter _inner = new(
        "{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz} [{Level:u3}] " +
        "{Message:lj}{NewLine}{Exception}");

    public void Format(LogEvent logEvent, TextWriter output)
    {
        using var buffer = new StringWriter();
        _inner.Format(logEvent, buffer);
        output.Write(SensitiveDataRedactor.Redact(buffer.ToString()));
    }
}

internal static class RedactingFileLogProbe
{
    public static void Emit(string path, IReadOnlyList<string> values, string privateKey)
    {
        using var logger = new LoggerConfiguration()
            .MinimumLevel.Verbose()
            .WriteTo.File(
                new RedactingTextFormatter(),
                path,
                rollingInterval: RollingInterval.Day,
                fileSizeLimitBytes: 512,
                rollOnFileSizeLimit: true,
                retainedFileCountLimit: 4)
            .CreateLogger();

        foreach (var value in values)
            logger.Information("Synthetic sink value {SyntheticValue}", value);
        logger.Error(
            new InvalidOperationException($"Synthetic exception {values[0]} {privateKey}"),
            "Failure carried secret={Secret}",
            values[2]);
        logger.Information(
            "command fingerprint={Fingerprint} argumentCount={ArgumentCount}",
            "AABBCCDD",
            4);
    }
}
