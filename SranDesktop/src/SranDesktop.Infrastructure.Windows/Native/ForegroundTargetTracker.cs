using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Native;

/// <summary>
/// Win32 implementation of the consent-flow foreground tracker (computer-use
/// self-interference fix). The Win32 calls are injected so the record/restore
/// DECISIONS are unit-testable without a desktop session.
/// </summary>
public sealed class ForegroundTargetTracker : IForegroundTargetTracker
{
    private readonly Func<IntPtr> _getForeground;
    private readonly Func<IntPtr, uint> _pidOf;
    private readonly uint _ownPid;
    private readonly Func<IntPtr, bool> _isWindowAlive;
    private readonly Func<IntPtr, bool> _forceForeground;
    private IntPtr _target;

    public ForegroundTargetTracker() : this(
        Win32Input.GetForegroundWindow,
        static hWnd =>
        {
            Win32Input.GetWindowThreadProcessId(hWnd, out var pid);
            return pid;
        },
        (uint)Environment.ProcessId,
        Win32Input.IsWindowVisible,
        Win32Input.ForceForegroundWindow)
    {
    }

    /// <summary>Test seam: all Win32 dependencies injectable.</summary>
    internal ForegroundTargetTracker(
        Func<IntPtr> getForeground, Func<IntPtr, uint> pidOf, uint ownPid,
        Func<IntPtr, bool> isWindowAlive, Func<IntPtr, bool> forceForeground)
    {
        _getForeground = getForeground;
        _pidOf = pidOf;
        _ownPid = ownPid;
        _isWindowAlive = isWindowAlive;
        _forceForeground = forceForeground;
    }

    public bool HasTarget => _target != IntPtr.Zero;

    public void RecordBeforeConsent()
    {
        var foreground = _getForeground();
        // SRAN being foreground (chained consent dialogs, user typing in the
        // composer) must NOT erase the real target from an earlier record.
        if (foreground == IntPtr.Zero || _pidOf(foreground) == _ownPid)
            return;
        _target = foreground;
    }

    public bool RestoreTarget()
    {
        var target = _target;
        if (target == IntPtr.Zero || !_isWindowAlive(target))
            return false;
        return _forceForeground(target);
    }

    public bool RestoreIfOwnForeground()
    {
        var foreground = _getForeground();
        if (foreground != IntPtr.Zero && _pidOf(foreground) != _ownPid)
            return false; // a real app already holds the foreground — leave it
        return RestoreTarget();
    }
}
