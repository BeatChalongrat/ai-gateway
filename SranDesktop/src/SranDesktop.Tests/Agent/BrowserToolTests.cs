using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>browser_* tools against a mocked bridge: happy paths, the soft
/// not-connected message, stale-ref passthrough, and the credential guard.</summary>
public class BrowserToolTests
{
    private readonly Mock<IBrowserBridgeService> _bridge = new();

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private void SetupResult(string method, object result) =>
        _bridge.Setup(b => b.RequestAsync(method, It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ReturnsAsync(JsonSerializer.SerializeToElement(result));

    [Fact]
    public async Task Disconnected_Bridge_Yields_The_Install_Guidance()
    {
        _bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserNotConnectedException());

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Settings → Browser");
    }

    [Fact]
    public async Task ListTabs_Formats_The_Indexed_Tab_Lines()
    {
        SetupResult("tabs.list", new
        {
            tabs = new[]
            {
                new { id = 3, title = "Example Domain", url = "https://example.com/", active = true },
                new { id = 7, title = "Docs", url = "https://docs.example.com/", active = false }
            }
        });

        var result = await new BrowserListTabsTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("[3] Example Domain — https://example.com/ (active)");
        result.Content.Should().Contain("[7] Docs — https://docs.example.com/");
    }

    [Fact]
    public async Task ReadPage_Passes_The_Element_Listing_Through()
    {
        SetupResult("page.read", new
        {
            text = "[0] <a> \"More information\" (enabled)",
            observation_id = "dom_obs_1",
            dom_generation = 7,
            tab_id = 3
        });

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { filter = "interactive" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        // Page content is untrusted external content → wrapped with the boundary marker.
        result.Content.Should().StartWith(UntrustedObservationBoundary.Marker);
        UntrustedObservationBoundary.TryReadEnvelope(result.Content, out var envelope)
            .Should().BeTrue();
        envelope!.Content.Should().Contain("[0] <a> \"More information\" (enabled)");
        envelope.ProhibitedUses.Should().Contain("grant_permission");
    }

    [Fact]
    public async Task Click_Passes_A_Stale_Ref_Error_Through()
    {
        _bridge.Setup(b => b.RequestAsync("page.click", It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserBridgeException("refs are stale — call browser_read_page again"));

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 12,
                tab_id = 3,
                observation_id = "dom_obs_1",
                dom_generation = 7,
                expected_state = "details visible"
            }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("refs are stale");
    }

    [Fact]
    public async Task Click_Reports_Navigation_With_The_New_Page()
    {
        SetupResult("page.click", new
        {
            clicked = true,
            target_activated = true,
            effect_observed = true,
            expected_state_observed = true,
            navigated = true,
            url = "https://www.iana.org/help/example-domains",
            title = "Example Domains"
        });

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 0,
                tab_id = 3,
                observation_id = "dom_obs_1",
                dom_generation = 7,
                expected_state = "Example Domains"
            }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("Example Domains");
        result.Content.Should().Contain("stale");
    }

    [Fact]
    public async Task Click_Rejects_An_Unbound_Dom_Reference()
    {
        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new { @ref = 0, expected_state = "changed" }),
                CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("StaleDomReference");
        _bridge.Verify(b => b.RequestAsync(
            It.IsAny<string>(),
            It.IsAny<object?>(),
            It.IsAny<CancellationToken>(),
            It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Click_Is_Not_Verified_When_The_Dom_Does_Not_Change()
    {
        SetupResult("page.click", new
        {
            clicked = true,
            target_activated = true,
            effect_observed = false,
            expected_state_observed = false,
            navigated = false
        });

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 0,
                tab_id = 3,
                observation_id = "dom_obs_1",
                dom_generation = 7,
                expected_state = "changed"
            }), CancellationToken.None);

        result.Success.Should().BeTrue("the bridge dispatched the click");
        result.Effect.Should().NotBeNull();
        result.Effect!.Verified.Should().BeFalse();
        result.Effect.FailureCode.Should().Be("DomPostconditionNotObserved");
    }

    [Fact]
    public async Task Click_Reobserves_And_Regrounds_One_Unique_Stale_Target()
    {
        _bridge.SetupSequence(b => b.RequestAsync(
                "page.click",
                It.IsAny<object?>(),
                It.IsAny<CancellationToken>(),
                It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserBridgeException("StaleDomReference: rerendered"))
            .ReturnsAsync(JsonSerializer.SerializeToElement(new
            {
                target_activated = true,
                effect_observed = true,
                expected_state_observed = true,
                navigated = false
            }));
        SetupResult("page.resolve", new
        {
            observation_id = "dom_obs_fresh",
            dom_generation = 8,
            tab_id = 3,
            candidates = new[] { new { @ref = 9 } }
        });

        var result = await new BrowserClickTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 0,
                tab_id = 3,
                observation_id = "dom_obs_old",
                dom_generation = 7,
                target_description = "Advance workflow",
                role = "button",
                expected_state = "Step two ready"
            }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Effect!.Evidence.Should().Contain("stale_target_recovered=true");
        _bridge.Verify(b => b.RequestAsync(
            "page.click",
            It.IsAny<object?>(),
            It.IsAny<CancellationToken>(),
            It.IsAny<TimeSpan?>()), Times.Exactly(2));
    }

    [Fact]
    public async Task Navigate_Rejects_Bad_Schemes_Without_Calling_The_Bridge()
    {
        var result = await new BrowserNavigateTool(_bridge.Object)
            .ExecuteAsync(Args(new { url = "javascript:alert(1)" }), CancellationToken.None);

        result.Success.Should().BeFalse();
        _bridge.Verify(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Type_Refuses_Credential_Text_Without_Calling_The_Bridge()
    {
        var result = await new BrowserTypeTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 4,
                text = "pass : hunter2",
                tab_id = 3,
                observation_id = "dom_obs_1",
                dom_generation = 7
            }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("prohibited");
        _bridge.Verify(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
            It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task ReadPage_Fails_Closed_Without_Observation_Identity()
    {
        SetupResult("page.read", new { text = "[0] <button> \"Send\" (enabled)" });

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Effect!.FailureCode.Should().Be("BrowserObservationIdentityMissing");
    }

    [Fact]
    public async Task Submit_Requires_An_Expected_Postcondition()
    {
        var result = await new BrowserTypeTool(_bridge.Object)
            .ExecuteAsync(Args(new
            {
                @ref = 4,
                text = "safe value",
                submit = true,
                tab_id = 3,
                observation_id = "dom_obs_1",
                dom_generation = 7
            }), CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("SubmitRequiresPostcondition");
        _bridge.Verify(b => b.RequestAsync(
            It.IsAny<string>(),
            It.IsAny<object?>(),
            It.IsAny<CancellationToken>(),
            It.IsAny<TimeSpan?>()), Times.Never);
    }

    [Fact]
    public async Task Screenshot_Returns_The_Image_Data_Uri()
    {
        SetupResult("page.screenshot", new { image = "data:image/jpeg;base64,abc123", title = "Example" });

        var result = await new BrowserScreenshotTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.ImageDataUri.Should().Be("data:image/jpeg;base64,abc123");
    }

    [Fact]
    public async Task Tabs_Tool_Validates_Its_Action_Arguments()
    {
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "close" }), CancellationToken.None))
            .Success.Should().BeFalse("close needs tab_id");
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "new", url = "notaurl" }), CancellationToken.None))
            .Success.Should().BeFalse("new needs a valid url");
        (await new BrowserTabsTool(_bridge.Object)
                .ExecuteAsync(Args(new { action = "explode" }), CancellationToken.None))
            .Success.Should().BeFalse("unknown action");
    }

    public static TheoryData<Exception> InterruptedDispatches() =>
    [
        new BrowserNotConnectedException(),
        new TimeoutException("The Chrome extension did not answer 'page.click' in time."),
        new BrowserBridgeException("The extension crashed after the click was dispatched.")
    ];

    [Theory]
    [MemberData(nameof(InterruptedDispatches))]
    public async Task Interrupted_Mutating_Dispatch_Is_Unknown_Outcome_Not_A_Clean_Failure(
        Exception interruption)
    {
        _bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(interruption);

        var result = await new BrowserClickTool(_bridge.Object).ExecuteAsync(
            Args(new
            {
                @ref = 3,
                tab_id = 7,
                observation_id = "obs-1",
                dom_generation = 2,
                expected_state = "Order submitted"
            }),
            CancellationToken.None);

        var decision = RuntimeFailurePolicy.Classify(
            result,
            SideEffectClass.Consequential);
        decision.Outcome.Should().Be(
            DurableToolOutcome.UnknownOutcome,
            "the click may have landed before the answer was lost");
        decision.ReconciliationRequired.Should().BeTrue();
        decision.RetryAllowed.Should().BeFalse();
    }

    [Fact]
    public async Task Extension_Guard_Before_Dispatch_Stays_A_Definite_Failure()
    {
        _bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserBridgeException(
                "StaleDomReference: the element was replaced."));

        var result = await new BrowserClickTool(_bridge.Object).ExecuteAsync(
            Args(new
            {
                @ref = 3,
                tab_id = 7,
                observation_id = "obs-1",
                dom_generation = 2,
                expected_state = "Order submitted"
            }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        RuntimeFailurePolicy.Classify(result, SideEffectClass.Consequential)
            .Outcome.Should().NotBe(
                DurableToolOutcome.UnknownOutcome,
                "a pre-dispatch guard proves the page was never touched");
    }

    [Fact]
    public async Task Interrupted_Observation_Keeps_The_Install_Guidance()
    {
        _bridge.Setup(b => b.RequestAsync(It.IsAny<string>(), It.IsAny<object?>(),
                It.IsAny<CancellationToken>(), It.IsAny<TimeSpan?>()))
            .ThrowsAsync(new BrowserNotConnectedException());

        var result = await new BrowserReadPageTool(_bridge.Object)
            .ExecuteAsync(Args(new { }), CancellationToken.None);

        result.Error.Should().Contain("Settings → Browser");
        RuntimeFailurePolicy.Classify(result, SideEffectClass.ReadOnly)
            .ReconciliationRequired.Should().BeFalse(
                "a read that never happened has nothing to reconcile");
    }
}

/// <summary>Part C: the shared credential guard — extended labels catch what
/// the old password|passwd|pwd list missed, without new false positives.</summary>
public class CredentialTextGuardTests
{
    [Theory]
    [InlineData("pass : hunter2")]            // the gap that let ssh.txt content through
    [InlineData("secret=abc123")]
    [InlineData("token: xyz789")]
    [InlineData("api key = sk_val")]
    [InlineData("api-key: value")]
    [InlineData("apikey: value")]             // api[ _-]?key collapses to apikey too
    [InlineData("otp: 123456")]
    [InlineData("PIN = 9876")]
    [InlineData("password: hunter2")]
    [InlineData("pwd = s3cret!")]
    public void Extended_Labels_Are_Blocked(string text)
    {
        CredentialTextGuard.LooksLikeCredential(text, out _).Should().BeTrue(text);
    }

    [Theory]
    [InlineData("a passage from the book")]
    [InlineData("tokenize the input stream")]
    [InlineData("compass = north")]           // \bpass\b must not match inside compass
    [InlineData("the pinned message")]
    [InlineData("passport renewal form")]
    [InlineData("The password policy requires rotation")] // label without a value
    [InlineData("secrets management is a good practice")]
    [InlineData("spin = 0.5")]
    public void Innocent_Words_Are_Not_Blocked(string text)
    {
        CredentialTextGuard.LooksLikeCredential(text, out _).Should().BeFalse(text);
    }
}
