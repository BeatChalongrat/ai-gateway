using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Oauth;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX7 — a FAKE authorization server for the flow tests: a real HttpMessageHandler
/// that answers the discovery / register / token endpoints with spec-shaped RFC
/// 9728 / 8414 / 7591 / 6749 JSON. It is a LABELLED fixture, never a silent mock for
/// the §5.3 live proof. It also verifies our PKCE: at the token exchange it recomputes
/// S256(code_verifier) and compares it to the challenge the browser was sent.
/// </summary>
internal sealed class FakeAuthServer : HttpMessageHandler
{
    public const string PrmUrl = "https://mcp.example.com/.well-known/oauth-protected-resource";
    public const string Issuer = "https://as.example.com";
    public const string AuthzEndpoint = "https://as.example.com/authorize";
    public const string TokenEndpoint = "https://as.example.com/token";
    public const string RegEndpoint = "https://as.example.com/register";
    public const string ClientId = "client-xyz";

    public bool RotateRefresh = true;
    public bool InvalidGrant = false;
    public bool VerifierMatchedChallenge { get; private set; }
    public FakeBrowser Browser { get; } = new();

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct)
    {
        var url = req.RequestUri!.GetLeftPart(UriPartial.Path);
        if (url == PrmUrl)
            return Json("""{"resource":"https://mcp.example.com/mcp","authorization_servers":["https://as.example.com"],"scopes_supported":["read","write"]}""");
        if (url.Contains("/.well-known/oauth-authorization-server") || url.Contains("/.well-known/openid-configuration"))
            return Json($$"""{"issuer":"{{Issuer}}","authorization_endpoint":"{{AuthzEndpoint}}","token_endpoint":"{{TokenEndpoint}}","registration_endpoint":"{{RegEndpoint}}","scopes_supported":["read","write","offline_access"],"code_challenge_methods_supported":["S256"]}""");
        if (url == RegEndpoint)
            return Json($$"""{"client_id":"{{ClientId}}","client_id_issued_at":1700000000}""");
        if (url == TokenEndpoint)
            return await TokenAsync(req, ct);
        return new HttpResponseMessage(HttpStatusCode.NotFound);
    }

    private async Task<HttpResponseMessage> TokenAsync(HttpRequestMessage req, CancellationToken ct)
    {
        var form = ParseForm(await req.Content!.ReadAsStringAsync(ct));
        var grant = form.GetValueOrDefault("grant_type");
        if (grant == "authorization_code")
        {
            var verifier = form.GetValueOrDefault("code_verifier") ?? "";
            var recomputed = PkcePair.Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
            VerifierMatchedChallenge = recomputed == Browser.CapturedChallenge;
            return Json("""{"access_token":"access_1","refresh_token":"refresh_1","token_type":"Bearer","expires_in":3600,"scope":"read offline_access"}""");
        }
        if (grant == "refresh_token")
        {
            if (InvalidGrant)
                return Json("""{"error":"invalid_grant","error_description":"refresh token revoked"}""", HttpStatusCode.BadRequest);
            return Json(RotateRefresh
                ? """{"access_token":"access_2","refresh_token":"refresh_2","token_type":"Bearer","expires_in":3600}"""
                : """{"access_token":"access_2","token_type":"Bearer","expires_in":3600}""");
        }
        return Json("""{"error":"unsupported_grant_type"}""", HttpStatusCode.BadRequest);
    }

    private static HttpResponseMessage Json(string body, HttpStatusCode code = HttpStatusCode.OK) =>
        new(code) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private static Dictionary<string, string> ParseForm(string body)
    {
        var d = new Dictionary<string, string>();
        foreach (var pair in body.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var i = pair.IndexOf('=');
            if (i <= 0) continue;
            d[Uri.UnescapeDataString(pair[..i])] = Uri.UnescapeDataString(pair[(i + 1)..]);
        }
        return d;
    }
}

/// <summary>A fake system browser: on Open it captures the PKCE challenge from the
/// authorization URL and, if primed, "redirects" to the real loopback server with
/// the code + state (a real HTTP GET, since the loopback is a real local server).</summary>
internal sealed class FakeBrowser : IBrowserLauncher
{
    public string? CapturedChallenge { get; private set; }
    private string? _code;

    public void DriveLoopbackWithCode(string code) => _code = code;

    public bool Open(string url)
    {
        var q = ParseQuery(new Uri(url).Query);
        CapturedChallenge = q.GetValueOrDefault("code_challenge");
        var redirect = q.GetValueOrDefault("redirect_uri");
        var state = q.GetValueOrDefault("state");
        if (_code is { } code && redirect is not null && state is not null)
        {
            _ = Task.Run(async () =>
            {
                using var h = new HttpClient();
                try { await h.GetAsync($"{redirect}?code={Uri.EscapeDataString(code)}&state={Uri.EscapeDataString(state)}"); }
                catch { /* the test asserts on the flow result */ }
            });
        }
        return true;
    }

    private static Dictionary<string, string> ParseQuery(string query)
    {
        var d = new Dictionary<string, string>();
        foreach (var pair in query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var i = pair.IndexOf('=');
            if (i <= 0) continue;
            d[Uri.UnescapeDataString(pair[..i])] = Uri.UnescapeDataString(pair[(i + 1)..]);
        }
        return d;
    }
}
