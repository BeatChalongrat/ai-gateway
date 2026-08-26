using Microsoft.Extensions.Logging;

namespace SranDesktop.ToolsSidecar;

/// <summary>
/// Minimal ILogger that writes to STDERR only. stdout is the MCP wire — a
/// single stray log line there corrupts the protocol — so no console
/// provider is used anywhere in this process.
/// </summary>
public sealed class StderrLoggerProvider : ILoggerProvider
{
    private readonly LogLevel _minimum;
    public StderrLoggerProvider(LogLevel minimum = LogLevel.Information) => _minimum = minimum;
    public ILogger CreateLogger(string categoryName) => new StderrLogger(categoryName, _minimum);
    public void Dispose() { }

    private sealed class StderrLogger : ILogger
    {
        private static readonly object Gate = new();
        private readonly string _category;
        private readonly LogLevel _minimum;
        public StderrLogger(string category, LogLevel minimum) { _category = category; _minimum = minimum; }
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel logLevel) => logLevel >= _minimum;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state,
            Exception? exception, Func<TState, Exception?, string> formatter)
        {
            if (!IsEnabled(logLevel)) return;
            var line = $"[{DateTime.UtcNow:HH:mm:ss.fff}] {logLevel,-5} {_category}: {formatter(state, exception)}"
                       + (exception is null ? "" : $" | {exception.GetType().Name}: {exception.Message}");
            lock (Gate) Console.Error.WriteLine(line);
        }
    }
}
