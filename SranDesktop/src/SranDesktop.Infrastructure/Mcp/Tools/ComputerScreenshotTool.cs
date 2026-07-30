using System.Drawing;
using System.Drawing.Imaging;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Perception;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// The agent's eyes: captures the FULL virtual desktop (all monitors) or one
/// window, scales it down to bound token cost, and records the image→screen
/// mapping in <see cref="ScreenContext"/> so click/scroll/drag translate
/// coordinates automatically — the model must never rescale (the old
/// "multiply by X" instruction was routinely ignored → misclicks).
/// </summary>
public sealed class ComputerScreenshotTool : IMcpTool, ISensitiveDataTool
{
    /// <summary>Downscale cap on the LONGER side. Width-only 1920 made a
    /// 5760-wide triple-monitor desktop a 3x shrink — text unreadable. 2560 on
    /// the longer side keeps multi-monitor text legible while bounding token
    /// cost; a single 1920/2560 monitor still ships unscaled, and precise
    /// reading is computer_zoom's job.</summary>
    internal const int MaxLongSide = 2560;

    private readonly ScreenContext _screen;
    private readonly IForegroundTargetTracker? _foreground;
    private readonly IScreenCaptureSecurityPolicy _capturePolicy;
    private readonly IScreenImageRedactor _redactor;
    private readonly IScreenCaptureIndicator _indicator;
    private readonly ISecurityEventLogger? _securityEvents;
    private readonly VisualSnapshotStore? _visualSnapshots;

    public ComputerScreenshotTool(
        ScreenContext screen,
        IForegroundTargetTracker? foreground = null,
        IScreenCaptureSecurityPolicy? capturePolicy = null,
        IScreenImageRedactor? redactor = null,
        IScreenCaptureIndicator? indicator = null,
        ISecurityEventLogger? securityEvents = null,
        VisualSnapshotStore? visualSnapshots = null)
    {
        _screen = screen;
        _foreground = foreground;
        _capturePolicy = capturePolicy ?? new ScreenCaptureSecurityPolicy();
        _redactor = redactor ?? new SolidScreenImageRedactor();
        _indicator = indicator ?? new ScreenCaptureIndicator();
        _securityEvents = securityEvents;
        _visualSnapshots = visualSnapshots;
    }

    public string Name => "computer_screenshot";
    public string Description =>
        "Capture the active target application window at native resolution. " +
        "Use capture_scope=desktop_overview only to discover an application, or " +
        "capture_scope=monitor for one explicit monitor. Returns a capture_id. " +
        "TIP: for Windows applications, read_screen_elements + uia_click is usually more " +
        "accurate than looking at pixels — use screenshots when UIA gives poor results " +
        "(games, custom-drawn apps, Chrome without accessibility) or to visually verify " +
        "an action's outcome.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        LocalAction: "Screen.CaptureLocal",
        ExternalAction: "Screen.SendToModel",
        SourceType: "screen_capture",
        Sensitivity: "unknown",
        Destination: "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: 8 * 1024 * 1024);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "Optional target window title. Defaults to the active non-SRAN window."
                },
                "capture_scope": {
                    "type": "string",
                    "enum": ["target_window", "desktop_overview", "monitor"],
                    "description": "Default target_window. Overview is discovery-only and is not valid for element clicks."
                },
                "monitor_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Zero-based monitor index for capture_scope=monitor."
                }
            }
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        try
        {
            var windowTitle = ToolHelpers.GetString(arguments, "window_title");
            var captureScope = ParseCaptureScope(
                ToolHelpers.GetString(arguments, "capture_scope"));
            using var captureIndicator = _indicator.Begin(captureScope.ToString());
            int originX = 0, originY = 0;
            IntPtr windowHandle = IntPtr.Zero;
            string? monitorId = null;
            var captureType = CaptureType.Window;
            Bitmap? bitmap;
            if (windowTitle is null && captureScope == CaptureType.DesktopOverview)
            {
                bitmap = CaptureVirtualDesktop(out originX, out originY);
                captureType = CaptureType.DesktopOverview;
            }
            else if (windowTitle is null && captureScope == CaptureType.Monitor)
            {
                var monitorIndex = arguments.TryGetProperty("monitor_index", out var indexElement)
                                   && indexElement.TryGetInt32(out var parsedIndex)
                    ? parsedIndex
                    : 0;
                var monitors = Win32Input.ListMonitors();
                if (monitorIndex < 0 || monitorIndex >= monitors.Count)
                    return Task.FromResult(ToolResult.Fail(
                        $"monitor_index {monitorIndex} is invalid; {monitors.Count} monitor(s) are available."));
                var monitor = monitors[monitorIndex];
                bitmap = CaptureRect(monitor.Bounds);
                originX = monitor.Bounds.X;
                originY = monitor.Bounds.Y;
                monitorId = monitor.Id;
                captureType = CaptureType.Monitor;
            }
            else if (windowTitle is { Length: > 0 })
            {
                bitmap = CaptureWindow(windowTitle, out originX, out originY, out windowHandle);
                if (bitmap is null)
                    return Task.FromResult(ToolResult.Fail(
                        $"Could not capture window containing '{windowTitle}'. Is it open and visible?"));
            }
            else
            {
                // Self-interference fix: if SRAN itself holds the foreground
                // (a consent dialog just closed), a full-screen shot would show
                // SRAN instead of the app the agent is working on — put the
                // recorded target back first.
                if (_foreground?.RestoreIfOwnForeground() == true)
                    Thread.Sleep(200); // let the target repaint before the grab
                windowHandle = Win32Input.GetForegroundWindow();
                if (windowHandle == IntPtr.Zero)
                    return Task.FromResult(ToolResult.Fail(
                        "No active target window is available. Use desktop_overview to discover an application."));
                Win32Input.GetWindowThreadProcessId(windowHandle, out var foregroundPid);
                if (foregroundPid == (uint)Environment.ProcessId)
                    return Task.FromResult(ToolResult.Fail(
                        "The active window belongs to SRAN Desktop. Focus a target app or use desktop_overview."));
                bitmap = CaptureWindow(windowHandle, out originX, out originY);
                if (bitmap is null)
                    return Task.FromResult(ToolResult.Fail(
                        "The active target window could not be captured."));
            }

            using var source = bitmap;
            using var scaled = ScaleDown(source, MaxLongSide);

            // Every capture rewrites the session mapping — clicks always refer
            // to the LAST screenshot the model saw.
            int? processId = null;
            string? processName = null;
            if (windowHandle != IntPtr.Zero)
            {
                Win32Input.GetWindowThreadProcessId(windowHandle, out var pid);
                processId = checked((int)pid);
                try
                {
                    using var process = Process.GetProcessById(processId.Value);
                    processName = process.ProcessName;
                }
                catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
                {
                    processName = null;
                }
            }

            var capturedWindowTitle = windowHandle == IntPtr.Zero
                ? null
                : Win32Input.GetWindowTitleText(windowHandle);
            var policy = _capturePolicy.Evaluate(new ScreenCaptureSecurityContext(
                captureType,
                processName,
                capturedWindowTitle,
                monitorId,
                scaled.Width,
                scaled.Height));
            if (!policy.Allowed)
            {
                RecordCapturePolicy(policy, captureType, processName, capturedWindowTitle, monitorId);
                return Task.FromResult(ToolResult.Fail(
                    $"Screen capture blocked by policy ({policy.DecisionCode}): {policy.Reason}"));
            }

            try
            {
                if (policy.RedactionRegions.Count > 0)
                    _redactor.Redact(scaled, policy.RedactionRegions);
            }
            catch (Exception ex)
            {
                var failure = new ScreenCapturePolicyDecision(
                    false,
                    policy.ClassificationKnown,
                    [],
                    "ScreenRedactionFailed",
                    "The required screen redaction component failed.");
                RecordCapturePolicy(failure, captureType, processName, capturedWindowTitle, monitorId);
                return Task.FromResult(ToolResult.Fail(
                    $"Screen capture blocked by policy ({failure.DecisionCode}): {ex.GetType().Name}"));
            }
            RecordCapturePolicy(policy, captureType, processName, capturedWindowTitle, monitorId);

            var captureId = Guid.NewGuid().ToString("N");
            var captureContext = new CaptureContext(
                CaptureId: captureId,
                CapturedAtUtc: DateTimeOffset.UtcNow,
                CaptureType: captureType,
                ProcessId: processId,
                WindowHandle: windowHandle == IntPtr.Zero ? null : windowHandle.ToInt64(),
                WindowTitle: capturedWindowTitle,
                MonitorId: monitorId,
                CoordinateSpace: captureType switch
                {
                    CaptureType.Window => CoordinateSpace.WindowImagePixels,
                    CaptureType.Monitor => CoordinateSpace.MonitorImagePixels,
                    _ => CoordinateSpace.VirtualDesktopImagePixels
                },
                ImageWidth: scaled.Width,
                ImageHeight: scaled.Height,
                SourceRectPhysical: new PhysicalRect(originX, originY, source.Width, source.Height),
                DpiScaleX: windowHandle == IntPtr.Zero ? 1 : Win32Input.DpiScaleForWindow(windowHandle),
                DpiScaleY: windowHandle == IntPtr.Zero ? 1 : Win32Input.DpiScaleForWindow(windowHandle),
                DisplayTopologyVersion: Win32Input.DisplayTopologyVersion(),
                WindowGeometryVersion: windowHandle == IntPtr.Zero
                    ? 0
                    : Win32Input.WindowGeometryVersion(windowHandle));
            _screen.Set(captureContext);
            _visualSnapshots?.Save(captureContext, scaled);

            // Encode straight to a data URI in memory — no temp file (Phase 6
            // Task 4): the old write-then-read roundtrip leaked a PNG per shot.
            using var memory = new MemoryStream();
            scaled.Save(memory, ImageFormat.Png);
            var dataUri = "data:image/png;base64," + Convert.ToBase64String(memory.GetBuffer(), 0, (int)memory.Length);

            return Task.FromResult(ToolResult.OkWithImage(
                $"Screenshot captured: {scaled.Width}x{scaled.Height}; capture_id={captureId}; " +
                $"scope={captureType}; process={processName ?? "unknown"}; " +
                $"monitor={monitorId ?? "none"}; policy={policy.DecisionCode}. Coordinates you see in " +
                "this image can be used directly with computer_click — do NOT rescale them.",
                dataUri,
                [
                    $"screen_capture_id={captureId}",
                    $"capture_scope={captureType}",
                    $"captured_process={processName ?? "unknown"}"
                ]));
        }
        catch (Exception ex)
        {
            return Task.FromResult(ToolResult.Fail($"Screenshot failed: {ex.Message}"));
        }
    }

    /// <summary>Full virtual desktop — all monitors, including ones left of /
    /// above the primary (negative origin).</summary>
    private void RecordCapturePolicy(
        ScreenCapturePolicyDecision decision,
        CaptureType captureType,
        string? processName,
        string? windowTitle,
        string? monitorId) =>
        _securityEvents?.Write(new SecurityAuditEvent(
            "screen.capture_policy",
            decision.Allowed ? "allowed" : "denied",
            Name,
            "Screen.CaptureLocal",
            decision.ClassificationKnown ? "classified" : "unknown",
            $"{captureType}:{processName ?? "unknown"}",
            "configured model provider",
            true,
            decision.Allowed,
            decision.Allowed ? null : decision.DecisionCode,
            CorrelationId: SensitiveDataRedactor.Fingerprint(
                $"{processName}|{windowTitle}|{monitorId}")));

    private static Bitmap CaptureVirtualDesktop(out int originX, out int originY)
    {
        var (x, y, width, height) = Win32Input.VirtualScreenBounds();
        originX = x;
        originY = y;
        var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.CopyFromScreen(x, y, 0, 0, bitmap.Size);
        return bitmap;
    }

    internal static CaptureType ParseCaptureScope(string? value) =>
        value?.Trim().ToLowerInvariant() switch
        {
            "desktop_overview" => CaptureType.DesktopOverview,
            "monitor" => CaptureType.Monitor,
            _ => CaptureType.Window
        };

    private static Bitmap CaptureRect(PhysicalRect rect)
    {
        var bitmap = new Bitmap(rect.Width, rect.Height);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.CopyFromScreen(rect.X, rect.Y, 0, 0, bitmap.Size);
        return bitmap;
    }

    /// <summary>PrintWindow capture. The window's screen top-left goes into the
    /// mapping origin so window-relative pixels translate to correct absolute
    /// click coordinates (the old code returned the bitmap with no offset —
    /// clicks on window screenshots landed shifted).</summary>
    private static Bitmap? CaptureWindow(
        string titlePart, out int originX, out int originY, out IntPtr windowHandle)
    {
        originX = originY = 0;
        windowHandle = Win32Input.FindWindowByTitle(titlePart);
        if (windowHandle == IntPtr.Zero)
            return null;

        return CaptureWindow(windowHandle, out originX, out originY);
    }

    private static Bitmap? CaptureWindow(
        IntPtr windowHandle, out int originX, out int originY)
    {
        originX = originY = 0;
        Win32Input.GetWindowRect(windowHandle, out var rect);
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
            return null;

        originX = rect.Left;
        originY = rect.Top;
        var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        var hdc = graphics.GetHdc();
        var captured = false;
        try
        {
            captured = Win32Input.PrintWindow(windowHandle, hdc, 2); // PW_RENDERFULLCONTENT
        }
        finally
        {
            graphics.ReleaseHdc(hdc);
        }
        if (!captured)
        {
            bitmap.Dispose();
            return null;
        }
        return bitmap;
    }

    private static Bitmap ScaleDown(Bitmap source, int maxLongSide)
    {
        var longSide = Math.Max(source.Width, source.Height);
        if (longSide <= maxLongSide)
            return (Bitmap)source.Clone();

        var scale = maxLongSide / (double)longSide;
        var scaled = new Bitmap(
            Math.Max(1, (int)(source.Width * scale)),
            Math.Max(1, (int)(source.Height * scale)));
        using var graphics = Graphics.FromImage(scaled);
        graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        graphics.DrawImage(source, 0, 0, scaled.Width, scaled.Height);
        return scaled;
    }
}
