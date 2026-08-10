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

    private sealed class CapturingHandler : HttpMessageHandler
    {
        private readonly HttpResponseMessage _response;
        public HttpRequestMessage? Request;
        public string? RequestBody;
        public CapturingHandler(HttpResponseMessage response) => _response = response;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Request = request;
            RequestBody = request.Content is null ? null : await request.Content.ReadAsStringAsync(ct);
            return _response;
        }
    }

    // ── SSO Phase 2 (B3): refresh body-fallback + desktop ticket exchange ──

    [Fact]
    public async Task Refresh_Without_Cookies_Falls_Back_To_Body_Tokens()
    {
        // Desktop has no cookie jar — the refresh endpoint's body fallback
        // must be honoured when Set-Cookie is absent.
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"access_token":"body-at","refresh_token":"body-rt","user":{"id":"u1","email":"a@b.c","display_name":null}}""")
        };
        var client = CreateClient(response);

        var result = await client.RefreshAsync("old-rt");

        result.AccessToken.Should().Be("body-at");
        result.RefreshToken.Should().Be("body-rt");
        result.User!.Id.Should().Be("u1");
    }

    [Fact]
    public async Task Refresh_Prefers_Cookie_Tokens_Over_Body()
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"access_token":"body-at","refresh_token":"body-rt"}""")
        };
        response.Headers.TryAddWithoutValidation("Set-Cookie", "portal_access_token=cookie-at; Path=/");
        response.Headers.TryAddWithoutValidation("Set-Cookie", "portal_refresh_token=cookie-rt; Path=/");
        var client = CreateClient(response);

        var result = await client.RefreshAsync("old-rt");

        result.AccessToken.Should().Be("cookie-at");
        result.RefreshToken.Should().Be("cookie-rt");
    }

    [Fact]
    public async Task ExchangeTicket_Posts_Ticket_And_Nonce_And_Reads_Body_Tokens()
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"access_token":"at-x","refresh_token":"rt-x","user":{"id":"u9","email":"d@gbtech.co.th","display_name":"D"}}""")
        };
        var handler = new CapturingHandler(response);
        var client = new PortalAuthClient(
            new HttpClient(handler) { BaseAddress = new Uri("http://localhost:8080") });

        var result = await client.ExchangeTicketAsync("ticket-123", "nonce-abc");

        handler.Request!.RequestUri!.AbsolutePath
            .Should().Be("/portal/auth/oauth/desktop/exchange");
        handler.RequestBody.Should().Contain("\"ticket\":\"ticket-123\"")
            .And.Contain("\"nonce\":\"nonce-abc\"");
        result.AccessToken.Should().Be("at-x");
        result.RefreshToken.Should().Be("rt-x");
        result.User!.Email.Should().Be("d@gbtech.co.th");
    }

    [Fact]
    public async Task ExchangeTicket_401_Throws_With_Status()
    {
        var response = new HttpResponseMessage(HttpStatusCode.Unauthorized)
        {
            Content = new StringContent(
                """{"error":{"code":"invalid_ticket","message":"OAuth ticket is invalid, expired, or already used."}}""")
        };
        var client = CreateClient(response);

        var act = () => client.ExchangeTicketAsync("t", "n");

        var ex = await act.Should().ThrowAsync<HttpRequestException>();
        ex.Which.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task GetPublicSettings_Reads_Password_Login_Flag()
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                """{"free_trial_tokens":150000,"password_login_enabled":false}""")
        };
        var client = CreateClient(response);

        var settings = await client.GetPublicSettingsAsync();

        settings.PasswordLoginEnabled.Should().BeFalse();
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
