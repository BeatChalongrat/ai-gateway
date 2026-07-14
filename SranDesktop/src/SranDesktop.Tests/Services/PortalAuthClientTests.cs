using System.Net;
using System.Net.Http;
using FluentAssertions;
using SranDesktop.Infrastructure.Api;
using Xunit;

namespace SranDesktop.Tests.Services;

public class PortalAuthClientTests
{
    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly HttpResponseMessage _response;
        public StubHandler(HttpResponseMessage response) => _response = response;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(_response);
    }

    private static PortalAuthClient CreateClient(HttpResponseMessage response) => new(
        new HttpClient(new StubHandler(response)) { BaseAddress = new Uri("http://localhost:8080") });

    [Fact]
    public void ParseSetCookies_Extracts_Token_Cookies()
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK);
        response.Headers.TryAddWithoutValidation("Set-Cookie",
            "portal_access_token=eyJhbGc.abc.def; Path=/; HttpOnly; SameSite=Lax");
        response.Headers.TryAddWithoutValidation("Set-Cookie",
            "portal_refresh_token=eyJhbGc.xyz.123; Path=/; HttpOnly; Max-Age=604800");

        var cookies = PortalAuthClient.ParseSetCookies(response);

        cookies["portal_access_token"].Should().Be("eyJhbGc.abc.def");
        cookies["portal_refresh_token"].Should().Be("eyJhbGc.xyz.123");
    }

    [Fact]
    public async Task Login_200_With_Error_Envelope_And_No_Cookies_Throws_Unauthorized()
    {
        // The stack can return 200 for bad credentials with an error envelope.
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"error":{"code":"authentication_error","message":"Invalid email or password."}}""")
        };
        var client = CreateClient(response);

        var act = () => client.LoginAsync("a@b.c", "wrong");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        ex.Which.Message.Should().Contain("Invalid email or password");
    }

    [Fact]
    public async Task Login_Success_Returns_Tokens_And_User()
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"user":{"id":"u1","email":"a@b.c","display_name":"A"}}""")
        };
        response.Headers.TryAddWithoutValidation("Set-Cookie", "portal_access_token=at1; Path=/");
        response.Headers.TryAddWithoutValidation("Set-Cookie", "portal_refresh_token=rt1; Path=/");
        var client = CreateClient(response);

        var result = await client.LoginAsync("a@b.c", "pw");

        result.AccessToken.Should().Be("at1");
        result.RefreshToken.Should().Be("rt1");
        result.User!.Id.Should().Be("u1");
    }

    [Fact]
    public async Task Login_NonSuccess_Status_Throws_With_Status()
    {
        var response = new HttpResponseMessage(HttpStatusCode.Unauthorized)
        {
            Content = new StringContent("""{"error":{"message":"nope"}}""")
        };
        var client = CreateClient(response);

        var act = () => client.LoginAsync("a@b.c", "pw");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }
}
