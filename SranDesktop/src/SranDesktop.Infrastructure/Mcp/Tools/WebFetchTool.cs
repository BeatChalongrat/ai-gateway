using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// web_fetch — fetches a web page and returns its readable text so research
/// tasks can go beyond web_search snippets (open_url only launches the user's
/// browser). Read-only like web_search → AllowAlways, but SSRF-guarded: the
/// host is DNS-resolved and private/loopback/link-local ranges are refused,
/// re-checked on every redirect hop (redirects are followed manually for that
/// reason). Downloads are capped at 5 MB; HTML is reduced to title + main text
/// with regex only — no HTML-parser NuGet dependency.
/// </summary>
public sealed class WebFetchTool : IMcpTool
{
    /// <summary>Named HttpClient (AppHostBuilder): 30s timeout, redirects OFF
    /// so every hop passes the SSRF check here before being followed.</summary>
    public const string HttpClientName = "web_fetch";

    internal const int DefaultMaxLength = 50_000;
    internal const int MinMaxLength = 1_000;
    internal const int MaxMaxLength = 200_000;
    private const long MaxDownloadBytes = 5 * 1024 * 1024;
    private const int MaxRedirects = 5;

    private const string UserAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILogger<WebFetchTool> _logger;

    public WebFetchTool(IHttpClientFactory httpClientFactory, ILogger<WebFetchTool> logger)
    {
        _httpClientFactory = httpClientFactory;
        _logger = logger;
    }

    public string Name => "web_fetch";

    public string Description =>
        "Fetch a web page and return its readable text content. Use after web_search to read " +
        "the actual pages, or when the user gives you a URL. Returns title + main text with " +
        "scripts/styles/navigation removed.";

    public ToolCategory Category => ToolCategory.Network;

    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "Absolute http:// or https:// URL to fetch" },
                "max_length": { "type": "integer", "description": "Maximum characters of text to return (default 50000, clamped to 1000-200000)" }
            },
            "required": ["url"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        var validationError = ValidateUrl(url, out var uri);
        if (validationError is not null)
            return ToolResult.Fail(validationError);

        var maxLength = ClampMaxLength(GetInt(arguments, "max_length", DefaultMaxLength));

        try
        {
            var client = _httpClientFactory.CreateClient(HttpClientName);
            using var response = await SendFollowingRedirectsAsync(client, uri!, ct);
            if (response is null)
                return ToolResult.Fail($"Too many redirects (more than {MaxRedirects}).");

            if (!response.IsSuccessStatusCode)
                return ToolResult.Fail(
                    $"Request failed: {(int)response.StatusCode} {response.ReasonPhrase} for {response.RequestMessage?.RequestUri}");

            var mediaType = response.Content.Headers.ContentType?.MediaType?.ToLowerInvariant();
            var isHtml = mediaType is "text/html" or "application/xhtml+xml";
            var isText = mediaType is not null
                && (mediaType.StartsWith("text/", StringComparison.Ordinal)
                    || mediaType is "application/json" or "application/xml");
            if (!isHtml && !isText)
                return ToolResult.Fail(
                    $"Unsupported content type '{mediaType ?? "unknown"}' — web_fetch can only read HTML and text content, " +
                    "not images, PDFs or other binary formats.");

            if (response.Content.Headers.ContentLength is > MaxDownloadBytes)
                return ToolResult.Fail("The page exceeds the 5 MB download limit.");

            var body = await ReadCappedAsync(response, ct);
            if (body is null)
                return ToolResult.Fail("The page exceeds the 5 MB download limit.");

            // The page body is untrusted external content: wrap it with the
            // observation boundary so it can't be read as instructions (prompt
            // injection). Truncate first so the marker survives the max_length cap.
            if (!isHtml)
                return ToolResult.Verified(
                    UntrustedObservationBoundary.ProtectIfUntrusted(
                        Name, ApplyMaxLength(body, maxLength)),
                    [$"http_status={(int)response.StatusCode}", $"fetched_url={uri}"]);

            var (title, text) = ExtractReadableText(body);
            var result = "# " + (string.IsNullOrWhiteSpace(title) ? uri!.Host : title) + "\n\n" + text;
            return ToolResult.Verified(
                UntrustedObservationBoundary.ProtectIfUntrusted(
                    Name, ApplyMaxLength(result, maxLength)),
                [$"http_status={(int)response.StatusCode}", $"fetched_url={uri}"]);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            _logger.LogWarning(ex, "web_fetch failed for {Url}", url);
            return ToolResult.Fail($"Fetch failed: {ex.Message}");
        }
    }

    /// <summary>Manual redirect chase — each hop is SSRF-validated exactly like
    /// the original URL, so a public page can't bounce us to 169.254.169.254.
    /// Returns null when the redirect budget is exhausted.</summary>
    private async Task<HttpResponseMessage?> SendFollowingRedirectsAsync(
        HttpClient client, Uri uri, CancellationToken ct)
    {
        var current = uri;
        for (var hop = 0; hop <= MaxRedirects; hop++)
        {
            var hostError = await CheckHostAllowedAsync(current, ct);
            if (hostError is not null)
                throw new HttpRequestException(hostError);

            using var request = new HttpRequestMessage(HttpMethod.Get, current);
            request.Headers.TryAddWithoutValidation("User-Agent", UserAgent);
            request.Headers.TryAddWithoutValidation("Accept", "text/html,*/*");

            var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
            if ((int)response.StatusCode is not (301 or 302 or 303 or 307 or 308))
                return response;

            var location = response.Headers.Location;
            response.Dispose();
            if (location is null)
                throw new HttpRequestException("Redirect response without a Location header.");

            var next = location.IsAbsoluteUri ? location : new Uri(current, location);
            if (next.Scheme is not ("http" or "https"))
                throw new HttpRequestException($"Redirect to unsupported scheme '{next.Scheme}'.");
            current = next;
        }
        return null;
    }

    /// <summary>Resolves the host and refuses private / loopback / link-local
    /// destinations. Returns an error message, or null when allowed.</summary>
    private static async Task<string?> CheckHostAllowedAsync(Uri uri, CancellationToken ct)
    {
        if (IPAddress.TryParse(uri.DnsSafeHost, out var literal))
            return IsBlockedAddress(literal)
                ? $"Refusing to fetch '{uri.Host}': private, loopback and link-local addresses are not allowed."
                : null;

        IPAddress[] addresses;
        try
        {
            addresses = await Dns.GetHostAddressesAsync(uri.DnsSafeHost, ct);
        }
        catch (SocketException ex)
        {
            return $"Could not resolve host '{uri.Host}': {ex.Message}";
        }

        if (addresses.Length == 0)
            return $"Could not resolve host '{uri.Host}'.";
        foreach (var address in addresses)
        {
            if (IsBlockedAddress(address))
                return $"Refusing to fetch '{uri.Host}': it resolves to a private, loopback or link-local address.";
        }
        return null;
    }

    /// <summary>Scheme/shape validation only (no network). Returns an error
    /// message, or null with the parsed Uri when acceptable.</summary>
    internal static string? ValidateUrl(string? url, out Uri? uri)
    {
        uri = null;
        if (string.IsNullOrWhiteSpace(url))
            return "Missing required argument: url";
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed))
            return "URL must be absolute, e.g. https://example.com/page";
        if (parsed.Scheme is not ("http" or "https"))
            return $"Unsupported scheme '{parsed.Scheme}' — only http:// and https:// are allowed.";
        uri = parsed;
        return null;
    }

    /// <summary>True for addresses web_fetch must never contact: loopback
    /// (127/8, ::1), RFC1918 (10/8, 172.16/12, 192.168/16), link-local
    /// (169.254/16, fe80::/10) and IPv6 unique-local (fc00::/7). Unknown
    /// address families are refused (fail-safe).</summary>
    internal static bool IsBlockedAddress(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6)
            address = address.MapToIPv4();

        if (address.AddressFamily == AddressFamily.InterNetwork)
        {
            var b = address.GetAddressBytes();
            return b[0] is 127 or 10 or 0
                || (b[0] == 172 && b[1] >= 16 && b[1] <= 31)
                || (b[0] == 192 && b[1] == 168)
                || (b[0] == 169 && b[1] == 254);
        }

        if (address.AddressFamily == AddressFamily.InterNetworkV6)
        {
            if (address.Equals(IPAddress.IPv6Loopback) || address.Equals(IPAddress.IPv6Any))
                return true;
            var b = address.GetAddressBytes();
            return (b[0] & 0xFE) == 0xFC              // fc00::/7 unique local
                || (b[0] == 0xFE && (b[1] & 0xC0) == 0x80); // fe80::/10 link-local
        }

        return true;
    }

    /// <summary>Reads the body up to the 5 MB cap; null when exceeded. Decodes
    /// with the declared charset, falling back to UTF-8.</summary>
    private static async Task<string?> ReadCappedAsync(HttpResponseMessage response, CancellationToken ct)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var buffered = new MemoryStream();
        var buffer = new byte[81920];
        int read;
        while ((read = await stream.ReadAsync(buffer, ct)) > 0)
        {
            if (buffered.Length + read > MaxDownloadBytes)
                return null;
            buffered.Write(buffer, 0, read);
        }

        var encoding = Encoding.UTF8;
        if (response.Content.Headers.ContentType?.CharSet is { Length: > 0 } charset)
        {
            try { encoding = Encoding.GetEncoding(charset.Trim('"')); }
            catch (ArgumentException) { /* unknown charset — UTF-8 fallback */ }
        }
        return encoding.GetString(buffered.GetBuffer(), 0, (int)buffered.Length);
    }

    // Regexes get a match timeout so a pathological page can't wedge the turn.
    private static readonly TimeSpan RxTimeout = TimeSpan.FromSeconds(5);
    private const RegexOptions Rx = RegexOptions.IgnoreCase | RegexOptions.Singleline;

    /// <summary>Reduces raw HTML to (title, readable text): comments and
    /// script/style/nav/header/footer subtrees removed, links become
    /// "text (url)" when the href adds information, block tags become
    /// newlines, entities decoded, whitespace collapsed.</summary>
    internal static (string? Title, string Text) ExtractReadableText(string html)
    {
        html = html.Replace("\r\n", "\n").Replace('\r', '\n');
        html = Regex.Replace(html, "<!--.*?-->", " ", Rx, RxTimeout);
        foreach (var tag in new[] { "script", "style", "nav", "header", "footer" })
            html = Regex.Replace(html, $@"<{tag}\b[^>]*>.*?</{tag}\s*>", " ", Rx, RxTimeout);

        string? title = null;
        var titleMatch = Regex.Match(html, "<title[^>]*>(.*?)</title>", Rx, RxTimeout);
        if (titleMatch.Success)
        {
            title = CollapseWhitespace(WebUtility.HtmlDecode(titleMatch.Groups[1].Value));
            if (title.Length == 0) title = null;
        }

        // <a href> → "text (url)" only when the href says more than the text.
        html = Regex.Replace(html, @"<a\b[^>]*\bhref\s*=\s*[""']([^""']*)[""'][^>]*>(.*?)</a\s*>", m =>
        {
            var href = m.Groups[1].Value.Trim();
            var text = CollapseWhitespace(Regex.Replace(m.Groups[2].Value, "<[^>]+>", " ", Rx, RxTimeout));
            if (text.Length == 0)
                return " ";
            var uninformative = href.Length == 0 || href.StartsWith('#')
                || href.StartsWith("javascript:", StringComparison.OrdinalIgnoreCase)
                || string.Equals(text, href, StringComparison.OrdinalIgnoreCase);
            return uninformative ? $" {text} " : $" {text} ({href}) ";
        }, Rx, RxTimeout);

        html = Regex.Replace(html, @"<(?:br|hr)\s*/?>", "\n", Rx, RxTimeout);
        html = Regex.Replace(html,
            @"</?(?:p|div|section|article|main|aside|h[1-6]|li|ul|ol|dl|dt|dd|table|thead|tbody|tfoot|tr|blockquote|pre|figure|figcaption|form|fieldset)\b[^>]*>",
            "\n", Rx, RxTimeout);
        html = Regex.Replace(html, "<[^>]+>", " ", Rx, RxTimeout);

        var text = WebUtility.HtmlDecode(html);
        text = Regex.Replace(text, "[ \t\u00A0]+", " ", RegexOptions.None, RxTimeout);
        text = Regex.Replace(text, " ?\n ?", "\n", RegexOptions.None, RxTimeout);
        text = Regex.Replace(text, "\n{3,}", "\n\n", RegexOptions.None, RxTimeout).Trim();
        return (title, text);
    }

    internal static int ClampMaxLength(int requested) =>
        Math.Clamp(requested, MinMaxLength, MaxMaxLength);

    /// <summary>Truncates with an explicit note so the model knows content was cut.</summary>
    internal static string ApplyMaxLength(string text, int maxLength) =>
        text.Length <= maxLength
            ? text
            : text[..maxLength] + $"\n[truncated — {maxLength} of {text.Length} chars]";

    private static string CollapseWhitespace(string value) =>
        Regex.Replace(value, @"\s+", " ", RegexOptions.None, RxTimeout).Trim();

    private static int GetInt(JsonElement args, string name, int fallback) =>
        args.ValueKind == JsonValueKind.Object
        && args.TryGetProperty(name, out var el)
        && el.ValueKind == JsonValueKind.Number
        && el.TryGetInt32(out var value)
            ? value
            : fallback;
}
