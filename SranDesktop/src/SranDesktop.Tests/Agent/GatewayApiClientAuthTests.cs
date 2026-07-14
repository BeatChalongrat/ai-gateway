using System.Net;
using System.Net.Http;
using System.Text.Json;
using FluentAssertions;
using Moq;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Api;
using Xunit;
using static SranDesktop.Tests.Agent.GatewayClientTestSupport;

namespace SranDesktop.Tests.Agent;

/// <summary>401 → re-provision-once behavior of GatewayApiClient (revoked/rotated agent key).</summary>
public class GatewayApiClientAuthTests
{
    private const string AuthErrorBody =
        """{"error":{"code":"authentication_error","message":"Invalid, expired, or revoked API key"},"request_id":"req-1"}""";

    private static HttpResponseMessage Unauthorized() =>
        JsonResponse(HttpStatusCode.Unauthorized, AuthErrorBody);

    [Fact]
    public async Task First_401_Reprovisions_Once_And_Streams_With_New_Key()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        handler.Enqueue(SseResponse("hello", "[DONE]"));
        var keys = KeyProvider();
        var client = CreateClient(handler, keys.Object);

        var frames = await CollectAsync(client);

        frames.Select(f => f.Data).Should().Equal("hello", "[DONE]");
        keys.Verify(k => k.ReprovisionAsync(It.IsAny<CancellationToken>()), Times.Once);
        handler.Sends.Should().Be(2);
        handler.BearerTokens.Should().Equal("gw_old", "gw_new");
    }

    [Fact]
    public async Task Second_401_Yields_Error_Body_Without_Looping()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        handler.Enqueue(Unauthorized());
        var keys = KeyProvider();
        var client = CreateClient(handler, keys.Object);

        var frames = await CollectAsync(client);

        frames.Should().ContainSingle().Which.Data.Should().Be(AuthErrorBody);
        keys.Verify(k => k.ReprovisionAsync(It.IsAny<CancellationToken>()), Times.Once);
        handler.Sends.Should().Be(2);
    }

    [Fact]
    public async Task Non_401_Error_Does_Not_Reprovision()
    {
        const string body = """{"error":{"code":"upstream_error","message":"bad gateway"}}""";
        var handler = new SequenceHandler();
        // 502 is transient-retried once (Task 3) but must never touch the key.
        handler.Enqueue(JsonResponse(HttpStatusCode.BadGateway, body));
        handler.Enqueue(JsonResponse(HttpStatusCode.BadGateway, body));
        var keys = KeyProvider();
        var client = CreateClient(handler, keys.Object);

        var frames = await CollectAsync(client);

        frames.Should().ContainSingle().Which.Data.Should().Be(body);
        keys.Verify(k => k.ReprovisionAsync(It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Reprovision_Failure_Yields_Clear_Error_Frame()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        var keys = KeyProvider(reprovisionedKey: null);
        var client = CreateClient(handler, keys.Object);

        var frames = await CollectAsync(client);

        frames.Should().ContainSingle().Which.Data.Should()
            .Contain("agent_key_reprovision_failed").And.Contain("signed in");
        handler.Sends.Should().Be(1);
    }

    [Fact]
    public async Task Search_401_Reprovisions_Once_And_Retries()
    {
        var searchResult = new GatewaySearchResponse("q", "tavily", 12,
            new List<GatewaySearchResultItem>
                { new("Title", "https://a.example", "snippet", "a.example", null) });
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        handler.Enqueue(JsonResponse(HttpStatusCode.OK,
            JsonSerializer.Serialize(searchResult, GatewayApiClient.JsonOptions)));
        var keys = KeyProvider();
        var client = CreateClient(handler, keys.Object);

        var response = await client.SearchAsync("q");

        response.Results.Should().ContainSingle().Which.Url.Should().Be("https://a.example");
        keys.Verify(k => k.ReprovisionAsync(It.IsAny<CancellationToken>()), Times.Once);
        handler.BearerTokens.Should().Equal("gw_old", "gw_new");
    }

    [Fact]
    public async Task Search_Double_401_Throws_Without_Looping()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        handler.Enqueue(Unauthorized());
        var keys = KeyProvider();
        var client = CreateClient(handler, keys.Object);

        var act = () => client.SearchAsync("q");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        keys.Verify(k => k.ReprovisionAsync(It.IsAny<CancellationToken>()), Times.Once);
        handler.Sends.Should().Be(2);
    }

    [Fact]
    public async Task Search_Reprovision_Failure_Throws_Clear_Error()
    {
        var handler = new SequenceHandler();
        handler.Enqueue(Unauthorized());
        var keys = KeyProvider(reprovisionedKey: null);
        var client = CreateClient(handler, keys.Object);

        var act = () => client.SearchAsync("q");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.Message.Should().Contain("signed in");
        handler.Sends.Should().Be(1);
    }
}
