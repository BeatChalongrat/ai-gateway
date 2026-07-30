using System.Diagnostics;
using System.Windows.Automation;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Native;

public sealed class ApplicationReadinessService
{
    public async Task<ApplicationReadinessResult> WaitForAsync(
        string application,
        long windowHandle,
        ApplicationReadinessLevel required,
        string? targetDescription,
        string? role,
        bool allowApplicationReceiver,
        TimeSpan timeout,
        CancellationToken ct)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        ApplicationReadinessResult? last = null;
        do
        {
            ct.ThrowIfCancellationRequested();
            last = await Task.Run(() => Observe(
                application, windowHandle, required, targetDescription, role,
                allowApplicationReceiver), ct);
            if (last.Ready) return last;
            await Task.Delay(100, ct);
        } while (DateTimeOffset.UtcNow < deadline);
        return last ?? Failed(application, "ApplicationNotReady",
            "No readiness observation was produced.");
    }

    public ApplicationReadinessResult Observe(
        string application,
        long windowHandle,
        ApplicationReadinessLevel required,
        string? targetDescription = null,
        string? role = null,
        bool allowApplicationReceiver = false)
    {
        var hWnd = new IntPtr(windowHandle);
        if (hWnd == IntPtr.Zero || !Win32Input.IsWindow(hWnd))
            return Failed(application, "StaleWindow", "The expected HWND is absent or stale.");

        Win32Input.GetWindowThreadProcessId(hWnd, out var rawPid);
        if (rawPid == 0)
            return Failed(application, "ApplicationNotReady", "The expected HWND has no process identity.");
        var pid = checked((int)rawPid);
        var title = Win32Input.GetWindowTitleText(hWnd);
        string processName;
        try
        {
            processName = Process.GetProcessById(pid).ProcessName;
        }
        catch (ArgumentException)
        {
            return Failed(application, "StaleWindow", "The window process exited before readiness validation.");
        }

        if (!IdentityMatches(application, title, processName))
            return new ApplicationReadinessResult(
                ApplicationReadinessLevel.WindowLocated, false, application, windowHandle, pid,
                null, Win32Input.IsWindowVisible(hWnd), false, false, false, false, false,
                "WrongWindow",
                $"HWND {windowHandle} belongs to '{processName}' / '{title}', not '{application}'.",
                [$"hwnd={windowHandle}", $"pid={pid}", $"process={processName}", $"title={title}"]);

        var visible = Win32Input.IsWindowVisible(hWnd) && !Win32Input.IsIconic(hWnd);
        var foreground = Win32Input.GetForegroundWindow() == hWnd;
        AutomationElement? root = null;
        AutomationElement? target = null;
        var accessibility = false;
        var blockingModal = false;
        try
        {
            root = AutomationElement.FromHandle(hWnd);
            _ = root.Current.ProcessId;
            accessibility = true;
            blockingModal = HasBlockingModal(root);
            if (!string.IsNullOrWhiteSpace(targetDescription))
                target = FindTarget(root, targetDescription, role, pid);
        }
        catch (ElementNotAvailableException)
        {
            accessibility = false;
        }
        catch (InvalidOperationException)
        {
            accessibility = false;
        }

        var targetReady = target is not null
                          && target.Current.IsEnabled
                          && !target.Current.IsOffscreen;
        var receiver = !blockingModal && foreground
                       && (targetReady || allowApplicationReceiver);
        var level = ApplicationReadinessLevel.ProcessStarted;
        if (hWnd != IntPtr.Zero) level = ApplicationReadinessLevel.WindowLocated;
        if (visible) level = ApplicationReadinessLevel.WindowVisible;
        if (visible && foreground) level = ApplicationReadinessLevel.ForegroundConfirmed;
        if (visible && foreground && accessibility)
            level = ApplicationReadinessLevel.AccessibilityReady;
        if (visible && foreground && accessibility && targetReady)
            level = ApplicationReadinessLevel.TargetControlReady;
        if (visible && foreground && accessibility && receiver)
            level = ApplicationReadinessLevel.InputReady;
        if (visible && accessibility && (targetReady || allowApplicationReceiver))
            level = required == ApplicationReadinessLevel.EffectObservable
                ? ApplicationReadinessLevel.EffectObservable
                : level;
        if (receiver && required == ApplicationReadinessLevel.ReadyForRequestedAction)
            level = ApplicationReadinessLevel.ReadyForRequestedAction;

        var ready = level >= required && !blockingModal;
        var controlIdentity = target is null ? null : IdentityOf(target);
        var failure = ready ? null
            : blockingModal ? "ApplicationNotReady"
            : !visible ? "WindowNotVisible"
            : !foreground && required >= ApplicationReadinessLevel.ForegroundConfirmed ? "FocusLost"
            : !accessibility && required >= ApplicationReadinessLevel.AccessibilityReady
                ? "AccessibilityUnavailable"
            : !targetReady && required >= ApplicationReadinessLevel.TargetControlReady
                ? "TargetNotFound"
            : "ApplicationNotReady";
        return new ApplicationReadinessResult(
            level, ready, application, windowHandle, pid, controlIdentity,
            visible, foreground, accessibility, targetReady, receiver, blockingModal,
            failure,
            ready
                ? $"Application reached {level}."
                : $"Application reached {level}; {required} is required.",
            [
                $"application={application}", $"process={processName}", $"pid={pid}",
                $"hwnd={windowHandle}", $"visible={visible.ToString().ToLowerInvariant()}",
                $"foreground={foreground.ToString().ToLowerInvariant()}",
                $"accessibility={accessibility.ToString().ToLowerInvariant()}",
                $"target_control_ready={targetReady.ToString().ToLowerInvariant()}",
                $"input_receiver={receiver.ToString().ToLowerInvariant()}", $"readiness={level}"
            ]);
    }

    internal static AutomationElement? FindTarget(
        AutomationElement root,
        string description,
        string? role,
        int expectedPid)
    {
        var candidates = root.FindAll(TreeScope.Descendants, Condition.TrueCondition);
        var matches = new List<(AutomationElement Element, int Score)>();
        foreach (AutomationElement element in candidates)
        {
            try
            {
                var current = element.Current;
                if (current.ProcessId != expectedPid || !current.IsEnabled || current.IsOffscreen)
                    continue;
                var type = current.ControlType.ProgrammaticName
                    .Replace("ControlType.", string.Empty, StringComparison.OrdinalIgnoreCase);
                if (!string.IsNullOrWhiteSpace(role)
                    && !type.Equals(role, StringComparison.OrdinalIgnoreCase)
                    && !(role.Equals("edit", StringComparison.OrdinalIgnoreCase)
                         && type.Equals("document", StringComparison.OrdinalIgnoreCase)))
                    continue;
                var name = current.Name ?? string.Empty;
                var automationId = current.AutomationId ?? string.Empty;
                var score = name.Equals(description, StringComparison.OrdinalIgnoreCase) ? 4
                    : automationId.Equals(description, StringComparison.OrdinalIgnoreCase) ? 4
                    : name.Contains(description, StringComparison.OrdinalIgnoreCase) ? 2
                    : automationId.Contains(description, StringComparison.OrdinalIgnoreCase) ? 2
                    : role is not null ? 1 : 0;
                if (score > 0) matches.Add((element, score));
            }
            catch (ElementNotAvailableException)
            {
                // A stale candidate is ignored; the caller will re-observe.
            }
        }
        return matches.OrderByDescending(item => item.Score).Select(item => item.Element).FirstOrDefault();
    }

    internal static string IdentityOf(AutomationElement element)
    {
        var current = element.Current;
        var type = current.ControlType.ProgrammaticName
            .Replace("ControlType.", string.Empty, StringComparison.OrdinalIgnoreCase);
        return $"pid={current.ProcessId};type={type};automation_id={current.AutomationId};name={current.Name}";
    }

    internal static string DescribeObservableState(AutomationElement root)
    {
        var values = new List<string>();
        var elements = root.FindAll(TreeScope.Subtree, Condition.TrueCondition);
        foreach (AutomationElement element in elements)
        {
            try
            {
                var current = element.Current;
                if (!string.IsNullOrWhiteSpace(current.Name)) values.Add(current.Name);
                if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
                    values.Add(((ValuePattern)value).Current.Value);
                if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
                    values.Add(((TogglePattern)toggle).Current.ToggleState.ToString());
            }
            catch (ElementNotAvailableException)
            {
                // A stale descendant does not invalidate the receiver root.
            }
            if (values.Sum(value => value.Length) > 16_000) break;
        }
        return string.Join("\n", values);
    }

    private static bool HasBlockingModal(AutomationElement root)
    {
        var windows = root.FindAll(TreeScope.Children,
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Window));
        foreach (AutomationElement window in windows)
            try
            {
                if (window.TryGetCurrentPattern(WindowPattern.Pattern, out var pattern)
                    && ((WindowPattern)pattern).Current.IsModal)
                    return true;
            }
            catch (ElementNotAvailableException)
            {
                return true;
            }
        return false;
    }

    private static bool IdentityMatches(string application, string title, string processName)
    {
        var expected = Normalize(application);
        return Normalize(title).Contains(expected, StringComparison.OrdinalIgnoreCase)
               || Normalize(processName).Contains(expected, StringComparison.OrdinalIgnoreCase)
               || expected.Contains(Normalize(processName), StringComparison.OrdinalIgnoreCase);
    }

    private static string Normalize(string value) =>
        value.Replace(".exe", string.Empty, StringComparison.OrdinalIgnoreCase)
            .Replace(" ", string.Empty, StringComparison.Ordinal)
            .Trim();

    private static ApplicationReadinessResult Failed(
        string application,
        string code,
        string reason) =>
        new(ApplicationReadinessLevel.NotStarted, false, application, null, null,
            null, false, false, false, false, false, false, code, reason, []);
}
