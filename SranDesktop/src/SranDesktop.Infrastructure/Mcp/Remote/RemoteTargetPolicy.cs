using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography.X509Certificates;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// FIX69 — where a USER-TYPED remote MCP URL may point.
///
/// A custom http connector is an arbitrary URL typed by the end user; the
/// sidecar then connects to it with the user's key. Left unchecked that is a
/// server-side-request-forgery primitive: the app can be pointed at services
/// on the machine or the company network that the user should not reach this
/// way, and at cloud metadata endpoints that hand out credentials. Two rules,
/// enforced where the socket is opened (so a hostname that resolves to an
/// internal address at connect time is refused too, not only at add time):
///
///   • internal targets (loopback, RFC 1918, link-local, ULA, CGNAT, v4-mapped
///     forms of those, unspecified) are refused unless the process was started
///     with SRAN_CONNECTOR_PRIVATE_NETWORK=1 — the host sets that from the
///     shipped desktop config (connectorPrivateNetwork), an owner/deploy-time
///     decision, never a page setting;
///   • cloud metadata addresses are refused always.
///
/// The scheme rule (https only for this path) lives in <see cref="ValidateRemoteUrl"/>;
/// the host enforces the same rule before it ever reaches the sidecar.
/// </summary>
public static class RemoteTargetPolicy
{
    public const string EnvAllowPrivate = "SRAN_CONNECTOR_PRIVATE_NETWORK";

    public static bool AllowPrivateFromEnv() =>
        Environment.GetEnvironmentVariable(EnvAllowPrivate) == "1";

    /// <summary>https only; absolute; no credentials in the URL. Returns a
    /// reason code the UI can name, or null when acceptable.</summary>
    public static string? ValidateRemoteUrl(string? url)
    {
        if (string.IsNullOrWhiteSpace(url) || !Uri.TryCreate(url, UriKind.Absolute, out var uri))
            return "invalid-url";
        if (!uri.Scheme.Equals("https", StringComparison.OrdinalIgnoreCase))
            return "https-required";
        if (!string.IsNullOrEmpty(uri.UserInfo))
            return "userinfo-in-url";
        return null;
    }

    private static readonly IPAddress[] MetadataV4 =
    {
        IPAddress.Parse("169.254.169.254"), // AWS / GCP / Azure / OpenStack
        IPAddress.Parse("100.100.100.200"), // Alibaba Cloud
    };
    private static readonly IPAddress MetadataV6 = IPAddress.Parse("fd00:ec2::254"); // AWS IMDS v6

    public static bool IsMetadata(IPAddress ip)
    {
        var v4 = ip.IsIPv4MappedToIPv6 ? ip.MapToIPv4() : ip;
        return MetadataV4.Any(m => m.Equals(v4)) || ip.Equals(MetadataV6);
    }

    /// <summary>Loopback, private, link-local, unique-local, CGNAT, unspecified,
    /// multicast — anything that is not a public unicast address.</summary>
    public static bool IsInternal(IPAddress ip)
    {
        if (ip.IsIPv4MappedToIPv6) ip = ip.MapToIPv4();
        if (ip.AddressFamily == AddressFamily.InterNetwork)
        {
            var b = ip.GetAddressBytes();
            return b[0] == 10
                || b[0] == 127
                || b[0] == 0
                || (b[0] == 172 && b[1] >= 16 && b[1] <= 31)
                || (b[0] == 192 && b[1] == 168)
                || (b[0] == 169 && b[1] == 254)
                || (b[0] == 100 && b[1] >= 64 && b[1] <= 127)
                || b[0] >= 224;
        }
        if (ip.AddressFamily == AddressFamily.InterNetworkV6)
        {
            if (IPAddress.IPv6Loopback.Equals(ip) || IPAddress.IPv6Any.Equals(ip)) return true;
            if (ip.IsIPv6LinkLocal || ip.IsIPv6SiteLocal || ip.IsIPv6Multicast || ip.IsIPv6UniqueLocal) return true;
            return false;
        }
        return true; // anything else is not a routable target for us
    }

    /// <summary>"metadata" | "internal" | null (allowed).</summary>
    public static string? Refuse(IPAddress ip, bool allowPrivate)
    {
        if (IsMetadata(ip)) return "metadata";
        if (!allowPrivate && IsInternal(ip)) return "internal";
        return null;
    }

    /// <summary>Owner-supplied extra trust anchors (PEM file) for remote MCP
    /// servers on a private CA — the normal case for a server inside a company
    /// network. Chain validation is NOT relaxed: name, dates and the chain are
    /// still checked, only against the system roots PLUS these. Set by the host
    /// from the shipped config (connectorExtraCaFile); absent = system roots only.</summary>
    public const string EnvExtraCaFile = "SRAN_CONNECTOR_EXTRA_CA_FILE";

    public static X509Certificate2Collection? LoadExtraRootsFromEnv()
    {
        var path = Environment.GetEnvironmentVariable(EnvExtraCaFile);
        return string.IsNullOrWhiteSpace(path) ? null : LoadExtraRoots(path);
    }

    public static X509Certificate2Collection LoadExtraRoots(string pemPath)
    {
        var roots = new X509Certificate2Collection();
        roots.ImportFromPemFile(pemPath);
        return roots;
    }

    /// <summary>The validation used when extra roots are configured. Accepts a
    /// certificate the system already trusts; otherwise re-validates the FULL
    /// chain with the extra roots as the trust store. A name mismatch or any
    /// other error is refused regardless of the extra roots.</summary>
    public static bool ValidateWithExtraRoots(X509Certificate? certificate, X509Chain? chain, SslPolicyErrors errors, X509Certificate2Collection extraRoots)
    {
        if (errors == SslPolicyErrors.None) return true;
        if (certificate is null || errors != SslPolicyErrors.RemoteCertificateChainErrors) return false;
        using var leaf = new X509Certificate2(certificate);
        using var custom = new X509Chain();
        custom.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
        custom.ChainPolicy.CustomTrustStore.AddRange(extraRoots);
        custom.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
        if (chain is not null)
            foreach (var el in chain.ChainElements)
                if (!el.Certificate.Equals(leaf)) custom.ChainPolicy.ExtraStore.Add(el.Certificate);
        return custom.Build(leaf);
    }

    /// <summary>An HttpClient whose every connection is checked against the
    /// policy at connect time (DNS is resolved here, not trusted from earlier).</summary>
    public static HttpClient CreateHttpClient(bool allowPrivate, TimeSpan timeout, X509Certificate2Collection? extraRoots = null)
    {
        var handler = new SocketsHttpHandler
        {
            ConnectCallback = async (ctx, ct) =>
            {
                var host = ctx.DnsEndPoint.Host;
                var port = ctx.DnsEndPoint.Port;
                IPAddress[] addresses = IPAddress.TryParse(host, out var literal)
                    ? new[] { literal }
                    : await Dns.GetHostAddressesAsync(host, ct);
                if (addresses.Length == 0)
                    throw new HttpRequestException($"remote target unresolvable: {host}");
                foreach (var a in addresses)
                {
                    var why = Refuse(a, allowPrivate);
                    if (why is not null)
                        throw new RemoteTargetRefusedException(why, host, a);
                }
                Exception? last = null;
                foreach (var a in addresses)
                {
                    var socket = new Socket(a.AddressFamily, SocketType.Stream, ProtocolType.Tcp) { NoDelay = true };
                    try
                    {
                        await socket.ConnectAsync(new IPEndPoint(a, port), ct);
                        return new NetworkStream(socket, ownsSocket: true);
                    }
                    catch (Exception ex) when (ex is SocketException or OperationCanceledException)
                    {
                        last = ex;
                        socket.Dispose();
                        if (ex is OperationCanceledException) throw;
                    }
                }
                throw new HttpRequestException($"connect failed: {host}:{port}", last);
            },
        };
        if (extraRoots is { Count: > 0 })
            handler.SslOptions.RemoteCertificateValidationCallback =
                (_, cert, chain, errors) => ValidateWithExtraRoots(cert, chain, errors, extraRoots);
        return new HttpClient(handler) { Timeout = timeout };
    }

    /// <summary>Walks an exception chain for a policy refusal.</summary>
    public static RemoteTargetRefusedException? FindRefusal(Exception? ex)
    {
        for (var e = ex; e is not null; e = e.InnerException)
            if (e is RemoteTargetRefusedException r) return r;
        return null;
    }
}

/// <summary>A connection the policy refused. An HttpRequestException so every
/// existing catch clause on the connector paths already handles it.</summary>
public sealed class RemoteTargetRefusedException : HttpRequestException
{
    public string Reason { get; }
    public string Host { get; }
    public IPAddress Address { get; }

    public RemoteTargetRefusedException(string reason, string host, IPAddress address)
        : base($"remote target refused ({reason}): {host} → {address}")
    {
        Reason = reason;
        Host = host;
        Address = address;
    }
}
