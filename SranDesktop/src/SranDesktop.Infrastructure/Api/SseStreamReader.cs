using System.Runtime.CompilerServices;
using System.Text;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Minimal SSE parser: groups "event:" / "data:" lines into frames separated by
/// blank lines. Multiple data lines are joined with '\n' per the SSE spec;
/// id:, retry: and comment lines (starting with ':') are ignored.
/// </summary>
public static class SseStreamReader
{
    public static async IAsyncEnumerable<SseFrame> ParseAsync(
        Stream stream,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var dataLines = new List<string>();
        string? eventType = null;

        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line is null)
            {
                // Stream ended — flush a trailing frame that had no blank line.
                if (dataLines.Count > 0)
                    yield return new SseFrame(eventType, string.Join("\n", dataLines));
                yield break;
            }

            if (line.Length == 0)
            {
                if (dataLines.Count > 0)
                {
                    yield return new SseFrame(eventType, string.Join("\n", dataLines));
                    dataLines.Clear();
                }
                eventType = null;
                continue;
            }

            if (line.StartsWith(':'))
                continue; // comment / keep-alive

            if (line.StartsWith("event:", StringComparison.Ordinal))
                eventType = line[6..].Trim();
            else if (line.StartsWith("data:", StringComparison.Ordinal))
                dataLines.Add(line[5..].TrimStart());
            // id:, retry: and unknown fields are ignored.
        }

        ct.ThrowIfCancellationRequested();
    }
}
