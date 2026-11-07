using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Infrastructure.Oauth;

/// <summary>
/// FIX7 — PKCE (RFC 7636). Claude includes a PKCE <c>code_challenge</c> with
/// <c>code_challenge_method=S256</c> on EVERY authorization request, regardless of
/// how the client registered — so we always generate a high-entropy verifier and
/// its S256 challenge. The verifier never leaves this process; only the challenge
/// travels to the authorization server, and the verifier is presented only at the
/// token exchange. This is the one thing that makes a loopback redirect safe: a
/// local process that steals the authorization code cannot exchange it without the
/// verifier it never saw.
/// </summary>
public sealed record PkcePair(string Verifier, string Challenge)
{
    public const string Method = "S256";

    /// <summary>A fresh verifier (RFC 7636 §4.1: 43–128 unreserved chars) and its
    /// S256 challenge (base64url(SHA256(verifier)), no padding — RFC 7636 §4.2).</summary>
    public static PkcePair Create()
    {
        Span<byte> raw = stackalloc byte[32];               // 256 bits of entropy
        RandomNumberGenerator.Fill(raw);
        var verifier = Base64Url(raw);                        // 43 chars, unreserved
        var challenge = Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
        return new PkcePair(verifier, challenge);
    }

    /// <summary>Unpadded base64url (RFC 4648 §5) — the encoding both the verifier
    /// and the S256 challenge use.</summary>
    public static string Base64Url(ReadOnlySpan<byte> bytes) =>
        Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}
