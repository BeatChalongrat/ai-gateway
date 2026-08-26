using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;

namespace SranDesktop.Shell.Seams;

/// <summary>
/// Seam 6 — the toast sink. <see cref="ToastService"/> is a pure queue; in the
/// WPF app a dispatcher timer pumps <see cref="ToastService.ExpireDue"/> and a
/// control renders <see cref="ToastService.Visible"/>. Four non-Presentation
/// services raise toasts (ConnectorOAuthCoordinator, ReadDocumentTool,
/// AgentConversationSyncService, the dialog service's error path). With no
/// consumer they would accumulate silently. Phase 1 drains them to the log;
/// a later phase forwards them to the page as <c>host.error</c>/<c>turn.event</c>.
/// </summary>
public sealed class ToastLogSink : IDisposable
{
    private readonly ILogger<ToastLogSink> _logger;
    private readonly ToastService _toasts;
    private readonly HashSet<ToastItem> _seen = new(ReferenceEqualityComparer.Instance);
    private readonly Timer _expiry;

    public ToastLogSink(ILogger<ToastLogSink> logger)
    {
        _logger = logger;
        _toasts = ToastService.Instance;
        _toasts.Changed += OnChanged;
        // Stand-in for the WPF dispatcher timer: without it a visible toast never
        // expires and the pending queue never promotes.
        _expiry = new Timer(_ => Pump(), null, TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1));
    }

    private void Pump()
    {
        try
        {
            _toasts.ExpireDue();
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Toast expiry pump failed");
        }
    }

    private void OnChanged(object? sender, EventArgs e)
    {
        foreach (var item in _toasts.Visible)
        {
            if (!_seen.Add(item))
                continue;
            // MessageKey is a localisation key; Detail is a literal (file name,
            // connector name) — both safe to log.
            var level = item.Kind == ToastKind.Error ? LogLevel.Warning : LogLevel.Information;
            _logger.Log(level, "Toast [{Kind}] {Key} {Detail}", item.Kind, item.MessageKey, item.Detail ?? "");
        }
    }

    public void Dispose()
    {
        _toasts.Changed -= OnChanged;
        _expiry.Dispose();
    }
}
