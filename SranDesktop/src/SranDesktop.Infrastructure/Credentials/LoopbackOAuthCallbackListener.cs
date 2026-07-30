using System.Net;
using System.Text;
using Microsoft.Extensions.Logging;

namespace SranDesktop.Infrastructure.Credentials;

public sealed record OAuthCallbackCapture(
    bool Success,
    string? Code,
    string? State,
    string? Error,
    string? ErrorDescription);

/// <summary>
/// Catches the provider's redirect on a loopback socket.
///
/// Everything here is narrow on purpose. It binds 127.0.0.1 only, so the
/// callback cannot arrive from another machine. It listens on an ephemeral port
/// chosen per attempt, so nothing can be waiting on a known one. It accepts
/// exactly one callback on the expected path and then stops, so a second
/// delivery — a replay, or a browser retrying — finds nothing listening. And it
/// answers every other request with 404 without reading it, so an unrelated
/// local process cannot use it as an oracle.
///
/// The listener does NOT validate state. That check belongs with the attempt
/// registry, which is the only thing that knows what was issued; splitting it
/// here would mean two places that both half-believe they did it.
/// </summary>
public sealed class LoopbackOAuthCallbackListener : IDisposable
{
    private readonly HttpListener _listener = new();
    private readonly ILogger? _logger;
    private bool _disposed;

    public LoopbackOAuthCallbackListener(string redirectPath, ILogger? logger = null)
    {
        _logger = logger;
        Port = FreeLoopbackPort();
        RedirectPath = NormalizePath(redirectPath);
        RedirectUri = $"http://127.0.0.1:{Port}{RedirectPath}";
        _listener.Prefixes.Add($"http://127.0.0.1:{Port}/");
        _listener.Start();
    }

    public int Port { get; }
    public string RedirectPath { get; }
    public string RedirectUri { get; }

    /// <summary>
    /// Waits for the one callback. A timeout is a refusal, not a retry: an
    /// authorization the user walked away from must not stay redeemable.
    /// </summary>
    public async Task<OAuthCallbackCapture> WaitAsync(
        TimeSpan timeout, CancellationToken ct = default)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);

        while (!deadline.IsCancellationRequested)
        {
            HttpListenerContext context;
            try
            {
                var pending = _listener.GetContextAsync();
                var finished = await Task.WhenAny(
                    pending, Task.Delay(Timeout.Infinite, deadline.Token));
                if (finished != pending)
                    break;
                context = await pending;
            }
            catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException
                                           or OperationCanceledException)
            {
                break;
            }

            var path = context.Request.Url?.AbsolutePath ?? "";
            if (!string.Equals(path, RedirectPath, StringComparison.OrdinalIgnoreCase))
            {
                // Anything that is not the redirect is somebody else's traffic.
                await RespondAsync(context, 404, "Not found.");
                continue;
            }

            var query = System.Web.HttpUtility.ParseQueryString(
                context.Request.Url?.Query ?? "");
            var error = query["error"];
            if (!string.IsNullOrEmpty(error))
            {
                await RespondAsync(context, 200,
                    "Sign-in was not completed. You can close this window.");
                return new OAuthCallbackCapture(
                    false, null, query["state"], error, query["error_description"]);
            }

            var code = query["code"];
            var state = query["state"];
            await RespondAsync(context, 200,
                "Signed in. You can close this window and return to SRAN Desktop.");
            if (string.IsNullOrEmpty(code))
                return new OAuthCallbackCapture(
                    false, null, state, "missing_code",
                    "The provider redirected without an authorization code.");
            return new OAuthCallbackCapture(true, code, state, null, null);
        }

        _logger?.LogInformation("OAuth callback timed out on port {Port}", Port);
        return new OAuthCallbackCapture(
            false, null, null, "timeout", "No callback arrived before the deadline.");
    }

    private static async Task RespondAsync(
        HttpListenerContext context, int status, string message)
    {
        try
        {
            var bytes = Encoding.UTF8.GetBytes(
                $"<!doctype html><meta charset=\"utf-8\"><title>SRAN Desktop</title>"
                + $"<body style=\"font-family:system-ui;padding:3rem\">{message}</body>");
            context.Response.StatusCode = status;
            context.Response.ContentType = "text/html; charset=utf-8";
            context.Response.ContentLength64 = bytes.Length;
            await context.Response.OutputStream.WriteAsync(bytes);
            context.Response.Close();
        }
        catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException
                                       or IOException)
        {
        }
    }

    private static string NormalizePath(string path) =>
        string.IsNullOrWhiteSpace(path) ? "/oauth/callback"
        : path.StartsWith('/') ? path
        : "/" + path;

    private static int FreeLoopbackPort()
    {
        var probe = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        try
        {
            _listener.Stop();
            _listener.Close();
        }
        catch (ObjectDisposedException)
        {
        }
    }
}
