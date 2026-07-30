using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// The remote MCP fixture, running in its own process.
///
/// Out-of-process matters for what it lets a test do that an in-process listener
/// cannot: kill the server outright, hold a request open past the client's
/// deadline, and read back what the server ACTUALLY did over a separate control
/// channel rather than from shared memory the client could have written.
/// </summary>
public sealed class RemoteMcpFixture : IAsyncDisposable
{
    private readonly Process _process;
    private static readonly HttpClient Control = new() { Timeout = TimeSpan.FromSeconds(10) };

    private RemoteMcpFixture(Process process, int port)
    {
        _process = process;
        Port = port;
        BaseUrl = $"http://127.0.0.1:{port}";
    }

    public int Port { get; }
    public string BaseUrl { get; }

    /// <summary>The MCP endpoint the connector is configured with. The transport
    /// appends /mcp itself.</summary>
    public string ServerUrl => BaseUrl;

    public static string ImagePath => Path.Combine(
        AppContext.BaseDirectory, "workers", "remote-mcp-fixture",
        "SranDesktop.RemoteMcpFixtureServer.dll");

    /// <summary>
    /// Starts the server and waits for it to say it is listening. Waiting for the
    /// READY line rather than polling the socket removes a race the first request
    /// would otherwise lose intermittently.
    /// </summary>
    /// <param name="port">Bind a specific port. Used when a test needs the
    /// server to come BACK at the address a connector was already configured
    /// with — a restart, rather than a different server.</param>
    public static async Task<RemoteMcpFixture> StartAsync(
        string profile = "default", string? requiredToken = null, int port = 0)
    {
        var arguments = $"\"{ImagePath}\" --profile={profile} --port={port}";
        if (requiredToken is { Length: > 0 })
            arguments += $" --token={requiredToken}";

        var process = new Process
        {
            StartInfo = new ProcessStartInfo("dotnet", arguments)
            {
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            }
        };
        process.Start();

        var ready = await Task.WhenAny(
            process.StandardOutput.ReadLineAsync(),
            Task.Delay(TimeSpan.FromSeconds(30)).ContinueWith(_ => (string?)null));
        var line = ready is Task<string?> text ? await text : null;
        if (line is null || !line.StartsWith("READY ", StringComparison.Ordinal))
        {
            try { process.Kill(entireProcessTree: true); } catch { }
            throw new InvalidOperationException(
                $"The remote MCP fixture did not start (last line: {line ?? "<none>"}).");
        }
        return new RemoteMcpFixture(process, int.Parse(line["READY ".Length..]));
    }

    /// <summary>What the SERVER says it did. Counting side effects from the
    /// client would only prove the client believes it did not repeat itself.</summary>
    public async Task<ServerStats> StatsAsync()
    {
        var json = await Control.GetStringAsync($"{BaseUrl}/control/stats");
        return JsonSerializer.Deserialize<ServerStats>(
            json, new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
    }

    /// <summary>Kills the process outright — a server that vanishes, rather than
    /// one that politely refuses.</summary>
    public void Kill()
    {
        try
        {
            if (!_process.HasExited)
                _process.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException)
        {
        }
    }

    public bool HasExited => _process.HasExited;

    public async ValueTask DisposeAsync()
    {
        try
        {
            if (!_process.HasExited)
                await Control.GetAsync($"{BaseUrl}/control/shutdown");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
        }
        Kill();
        try
        {
            await _process.WaitForExitAsync(
                new CancellationTokenSource(TimeSpan.FromSeconds(5)).Token);
        }
        catch (OperationCanceledException)
        {
        }
        _process.Dispose();
    }

    public sealed record ServerStats(
        int Created,
        int Requests,
        int Authorized,
        int Unauthorized,
        int ObservedAuthHeaders);
}
