using System.Net;
using System.Net.Sockets;
using System.Text;
using Microsoft.Extensions.Logging;

namespace SranDesktop.Infrastructure.Oauth;

/// <summary>
/// FIX7 — the RFC 8252 loopback redirect receiver. Binds <c>127.0.0.1</c> on an
/// EPHEMERAL port (the OS picks it), so the redirect URI is
/// <c>http://127.0.0.1:{port}/callback</c> — a port that varies per flow, exactly
/// like Claude Code. Only loopback is bound (never <c>0.0.0.0</c>), so no other
/// machine can reach it. It captures ONE authorization response, checks the
/// <c>state</c>, shows the browser a "you can close this tab" page, and returns the
/// code. The code alone is useless without the PKCE verifier this process holds
/// (see <see cref="Pkce"/>), which is what makes loopback acceptable.
/// </summary>
public sealed class LoopbackRedirectServer : IDisposable
{
    private readonly HttpListener _listener = new();
    private readonly ILogger? _logger;

    public string RedirectUri { get; }
    public int Port { get; }

    public LoopbackRedirectServer(ILogger? logger = null)
    {
        _logger = logger;
        Port = FreeLoopbackPort();
        RedirectUri = $"http://127.0.0.1:{Port}/callback";
        _listener.Prefixes.Add($"http://127.0.0.1:{Port}/");
        _listener.Start();
    }

    /// <summary>Wait for the browser to hit the redirect with <c>?code=…&amp;state=…</c>
    /// (or <c>?error=…</c>). Verifies the returned <c>state</c> equals the one we
    /// sent — a mismatch is rejected (CSRF / another flow's callback). Returns the
    /// authorization code, or throws with the server's error. Honours the token.</summary>
    public async Task<string> WaitForCodeAsync(string expectedState, CancellationToken ct)
    {
        using var reg = ct.Register(() => { try { _listener.Stop(); } catch { /* disposing */ } });
        while (true)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch (Exception) when (ct.IsCancellationRequested) { throw new OperationCanceledException(ct); }

            var q = ctx.Request.QueryString;
            var code = q["code"];
            var state = q["state"];
            var error = q["error"];

            // A browser may probe /favicon.ico etc. — answer non-callback paths
            // 204 and keep waiting for the real redirect.
            if (ctx.Request.Url?.AbsolutePath is not "/callback")
            {
                Respond(ctx, 204, "");
                continue;
            }

            if (!string.IsNullOrEmpty(error))
            {
                Respond(ctx, 400, Page("Authorization failed", "You can close this tab and return to SRAN Desktop."));
                throw new InvalidOperationException($"authorization_error:{error}");
            }
            if (string.IsNullOrEmpty(state) || !FixedTimeEquals(state, expectedState))
            {
                Respond(ctx, 400, Page("Unexpected response", "The authorization response did not match this request. You can close this tab."));
                throw new InvalidOperationException("state_mismatch");
            }
            if (string.IsNullOrEmpty(code))
            {
                Respond(ctx, 400, Page("No authorization code", "You can close this tab."));
                throw new InvalidOperationException("code_missing");
            }

            Respond(ctx, 200, Page("Connected", "SRAN Desktop received the authorization. You can close this tab."));
            return code;
        }
    }

    private static int FreeLoopbackPort()
    {
        // Bind :0 on loopback, read the OS-assigned port, release it for the
        // HttpListener. A tiny race, standard for loopback OAuth clients.
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    private static bool FixedTimeEquals(string a, string b) =>
        System.Security.Cryptography.CryptographicOperations.FixedTimeEquals(
            Encoding.UTF8.GetBytes(a), Encoding.UTF8.GetBytes(b));

    private static void Respond(HttpListenerContext ctx, int status, string html)
    {
        try
        {
            var bytes = Encoding.UTF8.GetBytes(html);
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "text/html; charset=utf-8";
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
            ctx.Response.OutputStream.Close();
        }
        catch { /* the browser may have navigated away */ }
    }

    private static string Page(string title, string body) =>
        $"<!doctype html><meta charset=utf-8><title>{title}</title>" +
        "<body style=\"font-family:system-ui;max-width:32rem;margin:4rem auto;text-align:center\">" +
        $"<h2>{title}</h2><p>{body}</p></body>";

    public void Dispose()
    {
        try { _listener.Close(); } catch { /* already closed */ }
    }
}
