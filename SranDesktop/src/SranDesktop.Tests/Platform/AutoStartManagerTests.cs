using FluentAssertions;
using Microsoft.Win32;
using SranDesktop.Infrastructure.Platform;
using Xunit;

namespace SranDesktop.Tests.Platform;

/// <summary>
/// Exercises the real HKCU Run key. Each test cleans up after itself so it
/// never leaves a stray auto-start entry behind.
/// </summary>
public class AutoStartManagerTests : IDisposable
{
    private const string RunKeyPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "SRAN Desktop";

    private readonly bool _wasEnabled;

    public AutoStartManagerTests()
    {
        _wasEnabled = AutoStartManager.IsEnabled;
        RemoveEntry();
    }

    [Fact]
    public void Enable_Sets_Registry_Value_With_Minimized_Flag()
    {
        AutoStartManager.Enable();

        AutoStartManager.IsEnabled.Should().BeTrue();
        using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, false);
        var value = key?.GetValue(ValueName) as string;
        value.Should().NotBeNull();
        value.Should().Contain("--minimized");
    }

    [Fact]
    public void Disable_Removes_The_Value()
    {
        AutoStartManager.Enable();
        AutoStartManager.Disable();

        AutoStartManager.IsEnabled.Should().BeFalse();
    }

    [Fact]
    public void Set_Toggles_Both_Directions()
    {
        AutoStartManager.Set(true);
        AutoStartManager.IsEnabled.Should().BeTrue();

        AutoStartManager.Set(false);
        AutoStartManager.IsEnabled.Should().BeFalse();
    }

    [Fact]
    public void Disable_When_Absent_Is_NoOp()
    {
        AutoStartManager.Disable();
        AutoStartManager.IsEnabled.Should().BeFalse();
    }

    private static void RemoveEntry()
    {
        using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, true);
        if (key?.GetValue(ValueName) is not null)
            key.DeleteValue(ValueName, false);
    }

    public void Dispose()
    {
        // Restore whatever state the machine had before the test ran.
        if (_wasEnabled)
            AutoStartManager.Enable();
        else
            RemoveEntry();
    }
}
