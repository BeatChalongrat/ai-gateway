using System;

namespace SranDesktop.Presentation.Rendering;

/// <summary>How an inline markdown image should be rendered (Phase C PART 1).</summary>
public enum InlineImageKind
{
    /// <summary>Not renderable inline (file://, local path, malformed) → keep the icon+link.</summary>
    None,
    /// <summary>A decoded base64 <c>data:image/*</c> URI (the common agent case: charts).</summary>
    DataUri,
    /// <summary>An http(s) URL to load asynchronously.</summary>
    Remote,
}

public sealed record InlineImagePlan(InlineImageKind Kind, byte[]? Data, string? RemoteUrl)
{
    public static readonly InlineImagePlan Link = new(InlineImageKind.None, null, null);
}

/// <summary>
/// Pure decision + decode for <c>![alt](src)</c> inline images. Kept free of WPF
/// so it is unit-testable. SECURITY: only <c>data:image/*;base64</c> and
/// <c>http(s)</c> are ever auto-loaded — a <c>file://</c> or bare local path is
/// NEVER loaded from markdown (stays a link), so model/tool output can't pull an
/// arbitrary local file onto the screen.
/// </summary>
public static class InlineImageResolver
{
    /// <summary>Decoded-size ceiling so a giant data URI can't blow memory.</summary>
    public const int MaxDataUriBytes = 10 * 1024 * 1024;

    public static InlineImagePlan Plan(string? url)
    {
        if (string.IsNullOrWhiteSpace(url))
            return InlineImagePlan.Link;

        var u = url.Trim();

        if (u.StartsWith("data:image/", StringComparison.OrdinalIgnoreCase))
            return TryDecodeDataUri(u, out var bytes)
                ? new InlineImagePlan(InlineImageKind.DataUri, bytes, null)
                : InlineImagePlan.Link;

        if (u.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            || u.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            return new InlineImagePlan(InlineImageKind.Remote, null, u);

        // file://, local paths, and any other scheme are NOT auto-loaded.
        return InlineImagePlan.Link;
    }

    /// <summary>Decodes a <c>data:image/*;base64,...</c> URI. False (no throw) on
    /// a malformed, non-base64, or oversized payload.</summary>
    public static bool TryDecodeDataUri(string dataUri, out byte[] bytes)
    {
        bytes = Array.Empty<byte>();
        var comma = dataUri.IndexOf(',');
        if (comma < 0)
            return false;

        var meta = dataUri.AsSpan(0, comma);
        if (!meta.Contains("base64", StringComparison.OrdinalIgnoreCase))
            return false; // only base64 data images (avoid url-encoded text payloads)

        var payload = dataUri.AsSpan(comma + 1);
        // Cheap size guard before allocating the decode buffer.
        if ((long)payload.Length * 3 / 4 > MaxDataUriBytes)
            return false;

        try
        {
            bytes = Convert.FromBase64String(payload.ToString());
        }
        catch (FormatException)
        {
            bytes = Array.Empty<byte>();
            return false;
        }

        if (bytes.Length == 0 || bytes.Length > MaxDataUriBytes)
        {
            bytes = Array.Empty<byte>();
            return false;
        }
        return true;
    }
}
