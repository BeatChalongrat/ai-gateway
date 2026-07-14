using SranDesktop.Presentation.Localization;
using Xunit;

namespace SranDesktop.Tests.Localization;

public class DictParityTests
{
    [Fact]
    public void EnglishAndThaiDictionariesHaveIdenticalKeySets()
    {
        var enOnly = Dict.En.Keys.Except(Dict.Th.Keys).OrderBy(k => k).ToList();
        var thOnly = Dict.Th.Keys.Except(Dict.En.Keys).OrderBy(k => k).ToList();

        Assert.True(enOnly.Count == 0 && thOnly.Count == 0,
            $"Missing in Th: [{string.Join(", ", enOnly)}] — Missing in En: [{string.Join(", ", thOnly)}]");
    }

    [Fact]
    public void NoEmptyTranslations()
    {
        Assert.All(Dict.En, kv => Assert.False(string.IsNullOrWhiteSpace(kv.Value), $"En[{kv.Key}] is empty"));
        Assert.All(Dict.Th, kv => Assert.False(string.IsNullOrWhiteSpace(kv.Value), $"Th[{kv.Key}] is empty"));
    }

    [Fact]
    public void TranslatorFallsBackToEnglishThenKey()
    {
        var svc = LocaleService.Instance;
        Assert.Equal("nonexistent.key", svc.T("nonexistent.key"));
    }

    [Fact]
    public void FormatArgumentsAreApplied()
    {
        var svc = LocaleService.Instance;
        svc.Locale = "en";
        Assert.Equal("Changes (5 actions):", svc.T("plan.changes", 5));
    }
}
