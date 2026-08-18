using FluentAssertions;
using SranDesktop.Presentation.Localization;
using Xunit;

namespace SranDesktop.Tests.Localization;

/// <summary>
/// Batch 2 T4 — the sidebar/offline banners and the usage panel moved
/// from raw English literals to Dict keys. Desktop dictionary lookups
/// are RUNTIME (no compile-time key check like web's DictKey union),
/// so this pins every key the view-models now reference: present in
/// BOTH locales, non-empty, and — for the Thai side of the offline
/// banners — actually translated (not the English value passed
/// through). A typo'd key in a VM surfaces here instead of as a raw
/// key string in the sidebar.
/// </summary>
public class T4LocalizedBannerTests
{
    private static readonly string[] Keys =
    {
        "window.minimize", "window.maximize",
        "usage.title", "usage.viewInPortal", "usage.byModel",
        "usage.totalUsed", "usage.unit.credits", "usage.unit.credShort",
        "usage.creditsTotalLine",
        "sidebar.status.offlineLocal", "sidebar.status.loadFailed",
        "archive.error.archiveOffline", "archive.error.restoreOffline",
        "project.error.createOffline", "project.error.renameOffline",
        "project.error.deleteOffline", "project.error.moveOffline",
        "conv.error.createOffline", "conv.error.deleteOffline",
        "conv.error.pinOffline", "conv.error.renameOffline",
        "conv.status.removedEmpty",
        "model.error.offline", "kb.error.offline", "memory.error.offline",
        "reasoning.error.offline", "export.error.offline", "copy.error.offline",
        "share.expiry", "share.expiry.1h", "share.expiry.24h",
        "share.expiry.7d", "share.expiry.never",
        "msg.stalled",
    };

    [Fact]
    public void Every_T4_Key_Exists_In_Both_Locales_And_Is_NonEmpty()
    {
        foreach (var key in Keys)
        {
            Dict.En.Should().ContainKey(key);
            Dict.Th.Should().ContainKey(key);
            Dict.En[key].Should().NotBeNullOrWhiteSpace(key);
            Dict.Th[key].Should().NotBeNullOrWhiteSpace(key);
        }
    }

    [Fact]
    public void Thai_Offline_Banners_Are_Actually_Translated()
    {
        // The whole point of the sweep: a Thai user having a bad time
        // (offline) must not get English. Thai values must differ from
        // English and contain Thai codepoints.
        foreach (var key in Keys)
        {
            if (!key.Contains("error") && !key.Contains("status")) continue;
            Dict.Th[key].Should().NotBe(Dict.En[key], key);
            Dict.Th[key].Any(c => c >= '฀' && c <= '๿')
                .Should().BeTrue($"{key} must contain Thai script");
        }
    }

    [Fact]
    public void Glossary_Decisions_Are_Pinned()
    {
        // Owner decisions 2026-08-19: one canonical Thai name per feature.
        Dict.Th["input.reasoning"].Should().Contain("โหมดคิดลึก");
        Dict.Th["sidebar.archiveTitle"].Should().Be("แชทในคลัง");
        Dict.Th["memory.suggested.title"].Should().Be("ความชอบที่แนะนำ");
        Dict.Th["canvas.showPreview"].Should().Be("พรีวิว");
    }
}
