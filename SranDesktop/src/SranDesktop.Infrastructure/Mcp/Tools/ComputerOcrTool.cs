using System.Diagnostics;
using System.Drawing;
using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Perception;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerOcrTool : IMcpTool, ISensitiveDataTool
{
    private readonly ScreenContext _screen;
    private readonly WindowsOcrService _ocr;
    private readonly OcrSnapshotStore _snapshots;
    private readonly IScreenCaptureSecurityPolicy _policy;
    private readonly IScreenImageRedactor _redactor;
    private readonly IScreenCaptureIndicator _indicator;

    public ComputerOcrTool(
        ScreenContext screen,
        WindowsOcrService ocr,
        OcrSnapshotStore snapshots,
        IScreenCaptureSecurityPolicy policy,
        IScreenImageRedactor redactor,
        IScreenCaptureIndicator indicator)
    {
        _screen = screen;
        _ocr = ocr;
        _snapshots = snapshots;
        _policy = policy;
        _redactor = redactor;
        _indicator = indicator;
    }

    public string Name => "computer_read_text";
    public string Description =>
        "Locally OCR a protected target window and return short-lived, capture-bound text refs. " +
        "Use computer_click with ocr_observation_id and ocr_ref; refs expire after three seconds.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "Screen.CaptureLocal",
        "Ocr.SendToModel",
        "screen_ocr",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: 64 * 1024);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
          "type": "object",
          "properties": {
            "window_title": {
              "type": "string",
              "description": "Exact or partial title of the authorized target window."
            }
          },
          "required": ["window_title"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        var title = ToolHelpers.GetString(arguments, "window_title");
        if (string.IsNullOrWhiteSpace(title))
            return ToolResult.Fail("window_title is required.");
        var hWnd = Win32Input.FindWindowByTitle(title);
        if (hWnd == IntPtr.Zero || !Win32Input.IsWindow(hWnd))
            return ToolResult.Fail("TargetWindowNotFound: no matching visible window exists.");
        Win32Input.GetWindowThreadProcessId(hWnd, out var pidValue);
        var pid = checked((int)pidValue);
        Win32Input.GetWindowRect(hWnd, out var rect);
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
            return ToolResult.Fail("TargetWindowGeometryInvalid.");
        string processIdentity;
        try
        {
            using var process = Process.GetProcessById(pid);
            processIdentity = process.MainModule?.FileName ?? process.ProcessName;
        }
        catch (Exception ex) when (ex is ArgumentException
                                   or InvalidOperationException
                                   or System.ComponentModel.Win32Exception)
        {
            return ToolResult.Fail(
                $"Screen capture blocked by policy (ProcessIdentityUnavailable): {ex.GetType().Name}");
        }
        var actualTitle = Win32Input.GetWindowTitleText(hWnd);
        var decision = _policy.Evaluate(new ScreenCaptureSecurityContext(
            CaptureType.Window,
            processIdentity,
            actualTitle,
            null,
            width,
            height));
        if (!decision.Allowed)
            return ToolResult.Fail(
                $"Screen capture blocked by policy ({decision.DecisionCode}): {decision.Reason}");

        using var indicator = _indicator.Begin("ocr:target_window");
        using var bitmap = new Bitmap(width, height);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            var hdc = graphics.GetHdc();
            try
            {
                if (!Win32Input.PrintWindow(hWnd, hdc, 2))
                    return ToolResult.Fail("WindowCaptureFailed: PrintWindow did not capture the target.");
            }
            finally
            {
                graphics.ReleaseHdc(hdc);
            }
        }
        try
        {
            _redactor.Redact(bitmap, decision.RedactionRegions);
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"ScreenRedactionFailed: {ex.GetType().Name}");
        }

        var captureId = $"ocr_capture_{Guid.NewGuid():N}";
        var captureVersion = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var capture = new CaptureContext(
            captureId,
            DateTimeOffset.UtcNow,
            CaptureType.Window,
            pid,
            hWnd.ToInt64(),
            actualTitle,
            null,
            CoordinateSpace.WindowImagePixels,
            width,
            height,
            new PhysicalRect(rect.Left, rect.Top, width, height),
            1,
            1,
            Win32Input.DisplayTopologyVersion(),
            Win32Input.WindowGeometryVersion(hWnd));
        LocalOcrObservation observation;
        try
        {
            observation = await _ocr.RecognizeAsync(
                bitmap,
                captureId,
                captureVersion,
                ct);
        }
        catch (Exception ex) when (ex is InvalidOperationException
                                   or UnauthorizedAccessException
                                   or NotSupportedException)
        {
            return ToolResult.Fail(ex.Message);
        }
        _screen.Set(capture);
        _snapshots.Save(new OcrSnapshot(
            observation,
            capture,
            DateTimeOffset.UtcNow.AddSeconds(3)));

        var output = new StringBuilder()
            .Append("OCR observation: observation_id=")
            .Append(observation.ObservationId)
            .Append("; capture_id=")
            .Append(captureId)
            .Append("; capture_version=")
            .Append(captureVersion)
            .Append("; language=")
            .Append(observation.Language)
            .AppendLine(".");
        foreach (var region in observation.Regions)
        {
            var text = observation.Sensitivity == SensitiveDataClassification.Secret
                ? "[sensitive text withheld]"
                : SensitiveDataRedactor.Redact(region.Text);
            output.Append('[').Append(region.Reference).Append("] \"")
                .Append(text).Append("\" box=(")
                .Append(region.X).Append(',').Append(region.Y).Append(',')
                .Append(region.Width).Append(',').Append(region.Height)
                .AppendLine(")");
        }
        return ToolResult.Verified(
            output.ToString().TrimEnd(),
            [
                $"ocr_observation_id={observation.ObservationId}",
                $"capture_id={captureId}",
                $"capture_version={captureVersion}",
                $"ocr_region_count={observation.Regions.Count}",
                $"ocr_language={observation.Language}",
                "raw_screen_persisted=false"
            ]);
    }
}
