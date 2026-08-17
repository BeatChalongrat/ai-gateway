using System.Globalization;
using SranDesktop.Presentation.Localization;
using Xunit;

namespace SranDesktop.Tests.Localization;

/// <summary>
/// Guards the translation-only i18n sweep (agent status, message meta,
/// Settings, Shortcuts, FirstRun, tray): a sample of the newly added keys
/// must resolve to non-empty, locale-distinct strings in both dictionaries.
/// </summary>
public class I18nSweepKeysTests
{
    [Theory]
    [InlineData("agent.workingStep")]
    [InlineData("agent.waitingPermission")]
    [InlineData("voice.unavailable")]
    [InlineData("msg.thinking")]
    [InlineData("msg.working")]
    [InlineData("msg.analyzed")]
    [InlineData("msg.thoughtFor")]
    [InlineData("msg.tokens")]
    [InlineData("settings.title")]
    [InlineData("settings.general")]
    [InlineData("settings.defaultModel")]
    [InlineData("settings.advanced")]
    [InlineData("settings.cleanupEmptyChats")]
    [InlineData("shortcuts.title")]
    [InlineData("shortcuts.sendMessage")]
    [InlineData("firstRun.title")]
    [InlineData("firstRun.getStarted")]
    [InlineData("tray.show")]
    [InlineData("tray.exit")]
    [InlineData("tray.stillRunning")]
    [InlineData("notify.turnDone")]
    [InlineData("notify.turnError")]
    [InlineData("notify.clickToOpen")]
    public void NewKeys_ResolveToNonEmptyDistinctStrings_InBothLocales(string key)
    {
        Assert.True(Dict.En.TryGetValue(key, out var en), $"En missing [{key}]");
        Assert.True(Dict.Th.TryGetValue(key, out var th), $"Th missing [{key}]");
        Assert.False(string.IsNullOrWhiteSpace(en), $"En[{key}] is empty");
        Assert.False(string.IsNullOrWhiteSpace(th), $"Th[{key}] is empty");
        Assert.NotEqual(en, th);
    }

    [Fact]
    public void FormatKeys_ApplyArguments()
    {
        Assert.Equal("Working... (step 2/25)",
            string.Format(CultureInfo.InvariantCulture, Dict.En["agent.workingStep"], 2, 25));
        Assert.Equal("Waiting for permission: run_command...",
            string.Format(CultureInfo.InvariantCulture, Dict.En["agent.waitingPermission"], "run_command"));
        Assert.Equal("Thought for 3.5s",
            string.Format(CultureInfo.InvariantCulture, Dict.En["msg.thoughtFor"], 3.5));
        Assert.Equal("128 tokens",
            string.Format(CultureInfo.InvariantCulture, Dict.En["msg.tokens"], 128));
    }
}
