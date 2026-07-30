using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

namespace SranDesktop.Infrastructure.Native.Browser;

/// <summary>
/// The browser-bridge wire format, shared verbatim by the app's pipe server
/// and the SranDesktop.BrowserHost relay (which source-links this file — it
/// must not reference the WPF-heavy Infrastructure assembly). Frames are
/// Chrome's native-messaging format end to end: 4-byte little-endian length +
/// UTF-8 JSON, so the relay can copy frames 1:1 with zero interpretation.
/// 1 MB cap matches Chrome's native-host→browser message limit — the
/// extension keeps screenshots under it by capturing JPEG.
/// </summary>
public static class BrowserWire
{
    public const int MaxMessageBytes = 1024 * 1024;

    /// <summary>Per-user pipe name: another user's session (or a low-privilege
    /// process guessing names) gets a different endpoint. The SID is hashed
    /// only to keep the name short and character-safe.</summary>
    public static string PipeName()
    {
        if (string.Equals(
                Environment.GetEnvironmentVariable("SRAN_BROWSER_TEST_MODE"),
                "1",
                StringComparison.Ordinal)
            && Environment.GetEnvironmentVariable("SRAN_BROWSER_PIPE_NAME")
                is { Length: >= 16 and <= 120 } testPipe
            && testPipe.All(character =>
                char.IsAsciiLetterOrDigit(character)
                || character is '-' or '_'))
            return testPipe;

        var sid = WindowsIdentity.GetCurrent().User?.Value ?? "anonymous";
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(sid));
        return "sran-browser-" + Convert.ToHexString(hash.AsSpan(0, 8)).ToLowerInvariant();
    }

    /// <summary>Reads one length-prefixed frame; null on clean end-of-stream.
    /// Throws InvalidDataException on an oversized or negative length.</summary>
    public static async Task<byte[]?> ReadFrameAsync(Stream stream, CancellationToken ct = default)
    {
        var header = new byte[4];
        if (!await TryReadExactAsync(stream, header, ct))
            return null;
        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length < 0 || length > MaxMessageBytes)
            throw new InvalidDataException($"Frame length {length} exceeds the {MaxMessageBytes}-byte limit.");
        var payload = new byte[length];
        if (!await TryReadExactAsync(stream, payload, ct))
            return null; // stream died mid-frame — treat as closed
        return payload;
    }

    /// <summary>Writes one length-prefixed frame. Throws InvalidDataException
    /// when the payload exceeds the cap (callers must not truncate silently).</summary>
    public static async Task WriteFrameAsync(Stream stream, byte[] payload, CancellationToken ct = default)
    {
        if (payload.Length > MaxMessageBytes)
            throw new InvalidDataException($"Frame length {payload.Length} exceeds the {MaxMessageBytes}-byte limit.");
        var header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
        await stream.WriteAsync(header, ct);
        await stream.WriteAsync(payload, ct);
        await stream.FlushAsync(ct);
    }

    private static async Task<bool> TryReadExactAsync(Stream stream, byte[] buffer, CancellationToken ct)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            var n = await stream.ReadAsync(buffer.AsMemory(read), ct);
            if (n == 0)
                return false;
            read += n;
        }
        return true;
    }
}
