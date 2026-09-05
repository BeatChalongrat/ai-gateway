using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>FIX70 — rotating the pairing token: a fresh value is persisted and
/// becomes the ONLY accepted one (GetOrCreatePairingToken answers with it from
/// then on, and every read loop compares against that). The value never
/// appears in a log line.</summary>
public sealed class BrowserPairingRotationTests
{
    [Fact]
    public void Rotate_Persists_A_New_Token_And_Retires_The_Old_One()
    {
        var store = new Dictionary<string, string>();
        var creds = new Mock<ICredentialStore>();
        creds.Setup(c => c.Load(It.IsAny<string>())).Returns((string k) => store.GetValueOrDefault(k));
        creds.Setup(c => c.Save(It.IsAny<string>(), It.IsAny<string>())).Callback((string k, string v) => store[k] = v);
        var bridge = new BrowserBridgeService(creds.Object, NullLogger<BrowserBridgeService>.Instance,
            pipeName: "sran-test-" + Guid.NewGuid().ToString("N"));

        var first = bridge.GetOrCreatePairingToken();
        first.Should().MatchRegex("^[0-9a-f]{48}$");
        store["browser:pairing-token"].Should().Be(first);

        var rotated = bridge.RotatePairingToken();
        rotated.Should().MatchRegex("^[0-9a-f]{48}$").And.NotBe(first);
        store["browser:pairing-token"].Should().Be(rotated, "the new token is persisted at once");
        bridge.GetOrCreatePairingToken().Should().Be(rotated, "the old token is no longer the expected one");
        bridge.IsConnected.Should().BeFalse();
    }
}
