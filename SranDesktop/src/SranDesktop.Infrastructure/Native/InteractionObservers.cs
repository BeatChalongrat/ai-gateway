using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Infrastructure.Native;

public sealed class DesktopInteractionObserver
{
    private long _captureVersion;

    public Task<InteractionObservationSnapshot> ObserveBeforeAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        CancellationToken ct) =>
        ObserveAsync(arguments, action, isPostAction: false, ct);

    public async Task<InteractionObservationSnapshot> ObserveAfterAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        InteractionObservationSnapshot before,
        CancellationToken ct)
    {
        var deadline = DateTimeOffset.UtcNow + action.Timeout;
        InteractionObservationSnapshot current;
        do
        {
            current = await ObserveAsync(arguments, action, isPostAction: true, ct);
            if (!string.Equals(
                    before.StateFingerprint,
                    current.StateFingerprint,
                    StringComparison.Ordinal))
                return current;
            await Task.Delay(TimeSpan.FromMilliseconds(50), ct);
        } while (DateTimeOffset.UtcNow < deadline);

        return current;
    }

    public ToolContractVerification Verify(
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after)
    {
        var toolVerified = result.Effect?.Verified == true
                           || result.ComputerAction?.EffectVerified == true;
        var boundSemanticDispatch = result.ComputerAction is
        {
            TargetValidated: true,
            OsInputAccepted: true,
            TargetX: not null,
            TargetY: not null
        };
        var stateChanged = !string.Equals(
            before.StateFingerprint,
            after.StateFingerprint,
            StringComparison.Ordinal);
        var expectedStateTransition =
            !before.ExpectedStateObserved && after.ExpectedStateObserved;
        var evidence = new[]
        {
            $"state_changed={stateChanged.ToString().ToLowerInvariant()}",
            $"expected_state_transition={expectedStateTransition.ToString().ToLowerInvariant()}",
            $"pre_observation_preconditions={string.Join(",", before.Preconditions)}",
            $"post_observation_preconditions={string.Join(",", after.Preconditions)}",
            $"pre_window={before.ActiveWindow?.WindowHandle.ToString() ?? "none"}",
            $"post_window={after.ActiveWindow?.WindowHandle.ToString() ?? "none"}"
        };
        if ((toolVerified || boundSemanticDispatch)
            && (stateChanged || expectedStateTransition))
            return new ToolContractVerification(
                VerificationDisposition.Verified,
                evidence,
                "The bound tool evidence and independently observed desktop state both changed.");
        if (result.Effect?.FailureCode == "UnknownOutcome"
            || result.ComputerAction?.FailureCode == "UnknownOutcome")
            return new ToolContractVerification(
                VerificationDisposition.NeedsUser,
                evidence,
                "The action may have changed desktop state, but its outcome is unknown.");
        if (!result.Success)
            return new ToolContractVerification(
                VerificationDisposition.Failed,
                evidence,
                result.Error ?? "The desktop action failed before its postcondition was verified.");
        return new ToolContractVerification(
            VerificationDisposition.Inconclusive,
            evidence,
            toolVerified
                ? "Tool-local verification passed, but the independent desktop snapshot did not change."
                : "The desktop postcondition was not independently verified.");
    }

    private async Task<InteractionObservationSnapshot> ObserveAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        bool isPostAction,
        CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        var application = GetString(arguments, "application")
                          ?? GetString(arguments, "name")
                          ?? "";
        var hWnd = application.Length == 0
            ? Win32Input.GetForegroundWindow()
            : Win32Input.FindWindowByTitle(application);
        WindowObservation? activeWindow = null;
        var preconditions = new List<string>();
        var fingerprintMaterial = new StringBuilder();
        var expectedStateObserved = false;
        if (hWnd != IntPtr.Zero && Win32Input.IsWindow(hWnd))
        {
            Win32Input.GetWindowThreadProcessId(hWnd, out var pidValue);
            var pid = checked((int)pidValue);
            var title = Win32Input.GetWindowTitleText(hWnd);
            Win32Input.GetWindowRect(hWnd, out var rect);
            var processName = "";
            try
            {
                processName = Process.GetProcessById(pid).MainModule?.FileName
                              ?? Process.GetProcessById(pid).ProcessName;
            }
            catch (Exception ex) when (ex is ArgumentException or InvalidOperationException
                                       or System.ComponentModel.Win32Exception)
            {
                processName = $"pid:{pid}";
            }

            var foreground = Win32Input.GetForegroundWindow() == hWnd;
            activeWindow = new WindowObservation(
                hWnd.ToInt64(),
                pid,
                Path.GetFileName(processName),
                title,
                rect.Left,
                rect.Top,
                rect.Right - rect.Left,
                rect.Bottom - rect.Top,
                true,
                foreground,
                false);
            fingerprintMaterial.Append(hWnd.ToInt64()).Append('|')
                .Append(pid).Append('|').Append(title).Append('|')
                .Append(rect.Left).Append(',').Append(rect.Top).Append(',')
                .Append(rect.Right - rect.Left).Append(',')
                .Append(rect.Bottom - rect.Top).Append('|')
                .Append(foreground);
            try
            {
                var state = await Task.Run(
                        () => ApplicationReadinessService.DescribeObservableState(
                            AutomationElement.FromHandle(hWnd)),
                        ct)
                    .WaitAsync(TimeSpan.FromSeconds(2), ct);
                fingerprintMaterial.Append('|').Append(state);
                expectedStateObserved =
                    !string.IsNullOrWhiteSpace(action.ExpectedState)
                    && state.Contains(
                        action.ExpectedState,
                        StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception ex) when (ex is TimeoutException
                                       or ElementNotAvailableException
                                       or InvalidOperationException)
            {
                preconditions.Add($"accessibility_state_unavailable:{ex.GetType().Name}");
            }
        }
        else
        {
            fingerprintMaterial.Append("window_absent|").Append(application);
            if (action.Kind != InteractionActionKind.LaunchApplication)
                preconditions.Add("TargetWindowNotFound");
        }

        var displays = EnumerateDisplays();
        var topologyVersion = Win32Input.DisplayTopologyVersion();
        fingerprintMaterial.Append("|topology=").Append(topologyVersion);
        return new InteractionObservationSnapshot(
            $"desktop_obs_{Guid.NewGuid():N}",
            AgentRunExecutionContext.CurrentRunId,
            null,
            InteractionObservationSource.Desktop,
            Interlocked.Increment(ref _captureVersion),
            DateTimeOffset.UtcNow,
            topologyVersion,
            InteractionCoordinateSpace.PhysicalScreenPixels,
            displays,
            activeWindow,
            activeWindow is null ? [] : [activeWindow],
            null,
            activeWindow is null ? null : $"uia:hwnd:{activeWindow.WindowHandle}",
            null,
            null,
            Fingerprint(fingerprintMaterial.ToString()),
            ObservationTrust.ToolObservation,
            SensitiveDataClassification.Confidential,
            "metadata_only:no_raw_screen",
            preconditions.Count == 0,
            preconditions)
        {
            ExpectedStateObserved = expectedStateObserved
        };
    }

    private static string? GetString(JsonElement arguments, string name) =>
        arguments.ValueKind == JsonValueKind.Object
        && arguments.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string Fingerprint(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
            .ToLowerInvariant();

    private static IReadOnlyList<DisplaySurfaceObservation> EnumerateDisplays()
    {
        var result = new List<DisplaySurfaceObservation>();
        EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (monitor, _, _, _) =>
        {
            var info = new MonitorInfo { Size = Marshal.SizeOf<MonitorInfo>() };
            if (GetMonitorInfo(monitor, ref info))
            {
                result.Add(new DisplaySurfaceObservation(
                    $"monitor:{monitor.ToInt64()}",
                    info.Monitor.Left,
                    info.Monitor.Top,
                    info.Monitor.Right - info.Monitor.Left,
                    info.Monitor.Bottom - info.Monitor.Top,
                    1,
                    1,
                    (info.Flags & MonitorPrimary) != 0));
            }
            return true;
        }, IntPtr.Zero);
        if (result.Count == 0)
        {
            var virtualScreen = Win32Input.VirtualScreenBounds();
            result.Add(new DisplaySurfaceObservation(
                "virtual-desktop",
                virtualScreen.X,
                virtualScreen.Y,
                virtualScreen.Width,
                virtualScreen.Height,
                1,
                1,
                true));
        }
        return result;
    }

    private delegate bool MonitorEnumProc(
        IntPtr monitor,
        IntPtr deviceContext,
        IntPtr monitorRect,
        IntPtr data);

    [DllImport("user32.dll")]
    private static extern bool EnumDisplayMonitors(
        IntPtr deviceContext,
        IntPtr clipRect,
        MonitorEnumProc callback,
        IntPtr data);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);

    private const uint MonitorPrimary = 1;

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct MonitorInfo
    {
        public int Size;
        public NativeRect Monitor;
        public NativeRect Work;
        public uint Flags;
    }
}

public sealed class BrowserInteractionObserver
{
    private readonly IBrowserBridgeService _bridge;
    private long _captureVersion;

    public BrowserInteractionObserver(IBrowserBridgeService bridge) => _bridge = bridge;

    public Task<InteractionObservationSnapshot> ObserveTabsBeforeAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        CancellationToken ct) =>
        ObserveTabsAsync(arguments, action, ct);

    public async Task<InteractionObservationSnapshot> ObserveTabsAfterAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        InteractionObservationSnapshot before,
        CancellationToken ct)
    {
        var deadline = DateTimeOffset.UtcNow + action.Timeout;
        InteractionObservationSnapshot current;
        do
        {
            current = await ObserveTabsAsync(arguments, action, ct);
            if (!string.Equals(
                    before.StateFingerprint,
                    current.StateFingerprint,
                    StringComparison.Ordinal))
                return current;
            await Task.Delay(TimeSpan.FromMilliseconds(50), ct);
        } while (DateTimeOffset.UtcNow < deadline);
        return current;
    }

    public Task<InteractionObservationSnapshot> ObserveBeforeAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        CancellationToken ct) =>
        ObserveAsync(arguments, action, ct);

    public async Task<InteractionObservationSnapshot> ObserveAfterAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        InteractionObservationSnapshot before,
        CancellationToken ct)
    {
        var deadline = DateTimeOffset.UtcNow + action.Timeout;
        InteractionObservationSnapshot current;
        do
        {
            current = await ObserveAsync(arguments, action, ct);
            if (!string.Equals(
                    before.StateFingerprint,
                    current.StateFingerprint,
                    StringComparison.Ordinal))
                return current;
            await Task.Delay(TimeSpan.FromMilliseconds(50), ct);
        } while (DateTimeOffset.UtcNow < deadline);
        return current;
    }

    public ToolContractVerification Verify(
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after)
    {
        var toolVerified = result.Effect?.Verified == true;
        var stateChanged = !string.Equals(
            before.StateFingerprint,
            after.StateFingerprint,
            StringComparison.Ordinal);
        var sameBrowser = string.Equals(
            before.BrowserDocument?.BrowserInstanceId,
            after.BrowserDocument?.BrowserInstanceId,
            StringComparison.Ordinal);
        var sameSession = !string.IsNullOrWhiteSpace(
                              before.BrowserDocument?.BridgeSessionId)
                          && string.Equals(
                              before.BrowserDocument?.BridgeSessionId,
                              after.BrowserDocument?.BridgeSessionId,
                              StringComparison.Ordinal);
        var transferVerified = result.Effect?.Evidence.Any(item =>
            item.StartsWith("browser_transfer_verified=true", StringComparison.Ordinal)
            || item.StartsWith("download_sha256=", StringComparison.Ordinal)) == true;
        var evidence = new[]
        {
            $"browser_state_changed={stateChanged.ToString().ToLowerInvariant()}",
            $"same_browser_instance={sameBrowser.ToString().ToLowerInvariant()}",
            $"same_bridge_session={sameSession.ToString().ToLowerInvariant()}",
            $"pre_dom_generation={before.BrowserDocument?.DomGeneration.ToString() ?? "none"}",
            $"post_dom_generation={after.BrowserDocument?.DomGeneration.ToString() ?? "none"}"
        };
        if (toolVerified && sameBrowser && sameSession
            && (stateChanged || transferVerified))
            return new ToolContractVerification(
                VerificationDisposition.Verified,
                evidence,
                "The browser tool evidence and an independent post-action observation agree.");
        if (result.Effect?.FailureCode == "UnknownOutcome")
            return new ToolContractVerification(
                VerificationDisposition.NeedsUser,
                evidence,
                "The browser may have applied a consequential action; reconcile before retry.");
        if (!result.Success)
            return new ToolContractVerification(
                VerificationDisposition.Failed,
                evidence,
                result.Error ?? "The browser action failed.");
        return new ToolContractVerification(
            VerificationDisposition.Inconclusive,
            evidence,
            "The expected browser state transition was not independently observed.");
    }

    private async Task<InteractionObservationSnapshot> ObserveAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        CancellationToken ct)
    {
        var tabId = arguments.ValueKind == JsonValueKind.Object
                    && arguments.TryGetProperty("tab_id", out var tab)
                    && tab.TryGetInt32(out var id)
            ? id
            : (int?)null;
        var result = await _bridge.RequestAsync(
            "page.observe",
            new
            {
                tab_id = tabId,
                expected_state = action.ExpectedState
            },
            ct,
            action.Timeout);
        var document = new BrowserDocumentObservation(
            GetString(result, "browser_instance_id"),
            GetInt(result, "tab_id"),
            GetString(result, "frame_path", "top"),
            GetLong(result, "navigation_id"),
            GetLong(result, "dom_generation"),
            GetString(result, "url"),
            GetString(result, "title"),
            GetString(result, "ready_state"),
            GetInt(result, "viewport_width"),
            GetInt(result, "viewport_height"),
            GetDouble(result, "zoom", 1),
            GetNullableString(result, "active_element_fingerprint"))
        {
            BridgeSessionId = GetNullableString(result, "bridge_session_id"),
            BrowserProfileId = GetNullableString(result, "browser_profile_id"),
            ExtensionSessionId = GetNullableString(result, "extension_session_id")
        };
        var preconditions = new List<string>();
        if (document.TabId <= 0)
            preconditions.Add("BrowserTabNotFound");
        if (string.IsNullOrWhiteSpace(document.BridgeSessionId))
            preconditions.Add("BrowserSessionIdentityMissing");
        if (!string.Equals(document.ReadyState, "complete", StringComparison.OrdinalIgnoreCase)
            && action.Kind is not InteractionActionKind.Navigate
                and not InteractionActionKind.OpenTab)
            preconditions.Add("DocumentNotReady");
        var fingerprint = GetString(result, "state_fingerprint");
        if (fingerprint.Length == 0)
            preconditions.Add("BrowserStateFingerprintMissing");
        return new InteractionObservationSnapshot(
            $"browser_obs_{Guid.NewGuid():N}",
            AgentRunExecutionContext.CurrentRunId,
            AgentRunExecutionContext.CurrentStepId,
            InteractionObservationSource.Browser,
            Interlocked.Increment(ref _captureVersion),
            DateTimeOffset.UtcNow,
            0,
            InteractionCoordinateSpace.DomViewportCssPixels,
            [],
            null,
            [],
            document,
            $"browser-a11y:{document.TabId}:{document.NavigationId}:{document.DomGeneration}",
            null,
            null,
            fingerprint,
            ObservationTrust.UntrustedExternalContent,
            SensitiveDataClassification.Confidential,
            "metadata_only:no_raw_dom",
            preconditions.Count == 0,
            preconditions)
        {
            ExpectedStateObserved = result.TryGetProperty(
                                        "expected_state_observed",
                                        out var expectedState)
                                    && expectedState.ValueKind == JsonValueKind.True
        };
    }

    private async Task<InteractionObservationSnapshot> ObserveTabsAsync(
        JsonElement arguments,
        InteractionActionDescriptor action,
        CancellationToken ct)
    {
        var result = await _bridge.RequestAsync(
            "tabs.list",
            null,
            ct,
            action.Timeout);
        var browserInstance = GetString(result, "browser_instance_id");
        var bridgeSession = GetNullableString(result, "bridge_session_id");
        var browserProfile = GetNullableString(result, "browser_profile_id");
        var extensionSession = GetNullableString(result, "extension_session_id");
        var requestedTab = arguments.ValueKind == JsonValueKind.Object
                           && arguments.TryGetProperty("tab_id", out var requested)
                           && requested.TryGetInt32(out var requestedId)
            ? requestedId
            : (int?)null;
        JsonElement? active = null;
        var targetFound = requestedTab is null;
        var fingerprintMaterial = new StringBuilder(browserInstance);
        if (result.TryGetProperty("tabs", out var tabs)
            && tabs.ValueKind == JsonValueKind.Array)
        {
            foreach (var tab in tabs.EnumerateArray())
            {
                var id = GetInt(tab, "id");
                var isActive = tab.TryGetProperty("active", out var activeValue)
                               && activeValue.ValueKind == JsonValueKind.True;
                if (isActive)
                    active = tab.Clone();
                if (requestedTab == id)
                    targetFound = true;
                fingerprintMaterial.Append('|').Append(id)
                    .Append(':').Append(isActive)
                    .Append(':').Append(GetLong(tab, "navigation_id"))
                    .Append(':').Append(GetString(tab, "url"));
            }
        }
        var preconditions = new List<string>();
        if (!targetFound && action.Kind is InteractionActionKind.CloseTab
                or InteractionActionKind.SwitchTab)
            preconditions.Add("BrowserTabNotFound");
        var document = active is { } activeTab
            ? new BrowserDocumentObservation(
                browserInstance,
                GetInt(activeTab, "id"),
                "top",
                GetLong(activeTab, "navigation_id"),
                0,
                GetString(activeTab, "url"),
                GetString(activeTab, "title"),
                GetString(activeTab, "status"),
                0,
                0,
                1,
                null)
            {
                BridgeSessionId = bridgeSession,
                BrowserProfileId = browserProfile,
                ExtensionSessionId = extensionSession
            }
            : null;
        if (string.IsNullOrWhiteSpace(bridgeSession))
            preconditions.Add("BrowserSessionIdentityMissing");
        return new InteractionObservationSnapshot(
            $"browser_tabs_obs_{Guid.NewGuid():N}",
            AgentRunExecutionContext.CurrentRunId,
            AgentRunExecutionContext.CurrentStepId,
            InteractionObservationSource.Browser,
            Interlocked.Increment(ref _captureVersion),
            DateTimeOffset.UtcNow,
            0,
            InteractionCoordinateSpace.DomViewportCssPixels,
            [],
            null,
            [],
            document,
            null,
            null,
            null,
            Convert.ToHexString(SHA256.HashData(
                    Encoding.UTF8.GetBytes(fingerprintMaterial.ToString())))
                .ToLowerInvariant(),
            ObservationTrust.UntrustedExternalContent,
            SensitiveDataClassification.Confidential,
            "metadata_only:no_raw_dom",
            preconditions.Count == 0,
            preconditions);
    }

    private static string GetString(JsonElement value, string name, string fallback = "") =>
        value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property)
        && property.ValueKind == JsonValueKind.String
            ? property.GetString() ?? fallback
            : fallback;

    private static string? GetNullableString(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property)
        && property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;

    private static int GetInt(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property)
        && property.TryGetInt32(out var result)
            ? result
            : 0;

    private static long GetLong(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property)
        && property.TryGetInt64(out var result)
            ? result
            : 0;

    private static double GetDouble(JsonElement value, string name, double fallback) =>
        value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property)
        && property.TryGetDouble(out var result)
            ? result
            : fallback;
}
