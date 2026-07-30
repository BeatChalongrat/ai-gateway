using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Workers;

public sealed class InteractiveProtocolException(string code, string message)
    : InvalidOperationException(message)
{
    public string Code { get; } = code;
}

/// <summary>
/// Length-prefixed JSON framing: 4-byte little-endian length + UTF-8 body.
///
/// Deliberately protocol-agnostic — it carries an opaque payload so the same
/// transport can host MCP or anything else. The declared length is never
/// trusted: a frame claiming more than the cap is refused before a single byte
/// is allocated, so a hostile or broken worker cannot drive the host out of
/// memory.
/// </summary>
public static class InteractiveFrameCodec
{
    public const int MaxFrameBytes = 4 * 1024 * 1024;

    private static readonly JsonSerializerOptions Json = new()
    {
        DefaultIgnoreCondition =
            System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    public static byte[] Encode(InteractiveFrame frame)
    {
        var body = JsonSerializer.SerializeToUtf8Bytes(frame, Json);
        if (body.Length > MaxFrameBytes)
            throw new InteractiveProtocolException(
                "FrameTooLarge",
                $"An outbound frame of {body.Length} bytes exceeds the {MaxFrameBytes}-byte limit.");
        var buffer = new byte[4 + body.Length];
        BinaryPrimitives.WriteInt32LittleEndian(buffer, body.Length);
        body.CopyTo(buffer.AsSpan(4));
        return buffer;
    }

    /// <summary>
    /// Reads exactly one frame. Returns null on a clean end of stream. Throws
    /// <see cref="InteractiveProtocolException"/> on anything malformed — the
    /// caller treats that as loss of protocol integrity and kills the session
    /// rather than trying to resynchronize on a corrupt stream.
    /// </summary>
    public static async Task<InteractiveFrame?> ReadAsync(
        Stream stream,
        CancellationToken ct)
    {
        var header = new byte[4];
        if (!await ReadExactlyAsync(stream, header, ct))
            return null;

        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length < 0)
            throw new InteractiveProtocolException(
                "FrameLengthInvalid", $"A frame declared a negative length ({length}).");
        if (length > MaxFrameBytes)
            // Refused on the DECLARED size: nothing is allocated for it.
            throw new InteractiveProtocolException(
                "FrameTooLarge",
                $"A frame declared {length} bytes, above the {MaxFrameBytes}-byte limit.");
        if (length == 0)
            throw new InteractiveProtocolException(
                "FrameEmpty", "A frame declared a zero-length body.");

        var body = new byte[length];
        if (!await ReadExactlyAsync(stream, body, ct))
            // Truncated mid-frame: the worker died while writing.
            return null;

        try
        {
            return JsonSerializer.Deserialize<InteractiveFrame>(body, Json)
                   ?? throw new InteractiveProtocolException(
                       "FrameNotAnObject", "A frame body deserialized to null.");
        }
        catch (JsonException ex)
        {
            // The raw body is deliberately NOT included: it may carry payload
            // content that should never reach a log.
            throw new InteractiveProtocolException(
                "FrameUnparseable",
                $"A frame body was not valid JSON ({ex.GetType().Name}).");
        }
    }

    private static async Task<bool> ReadExactlyAsync(
        Stream stream,
        byte[] buffer,
        CancellationToken ct)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            // Partial reads are normal on a pipe; a frame routinely spans several.
            var chunk = await stream.ReadAsync(buffer.AsMemory(read), ct);
            if (chunk == 0)
                return false;
            read += chunk;
        }
        return true;
    }

    /// <summary>Convenience for fixtures and workers that speak the same framing
    /// over a synchronous stream.</summary>
    public static void Write(Stream stream, InteractiveFrame frame)
    {
        var buffer = Encode(frame);
        stream.Write(buffer, 0, buffer.Length);
        stream.Flush();
    }

    public static string Describe(InteractiveFrame frame) =>
        $"{frame.MessageType}"
        + (frame.RequestId is { Length: > 0 } id ? $" id={id}" : "")
        + (frame.Method is { Length: > 0 } method ? $" method={method}" : "");

    internal static string SafeText(byte[] body) =>
        Encoding.UTF8.GetString(body, 0, Math.Min(body.Length, 256));
}
