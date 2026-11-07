using System.IO.Pipes;
using SranDesktop.Infrastructure.Native.Browser;

// SranDesktop.BrowserHost — the native-messaging relay. Chrome only speaks
// native messaging to a process IT spawns, so this exe exists solely to
// forward frames 1:1 between Chrome (stdio) and the running SRAN Desktop app
// (named pipe). Deliberately dumb: no parsing, no logic — pairing and routing
// live in the app's BrowserBridgeService. Exits when either side closes.

using var stdin = Console.OpenStandardInput();
using var stdout = Console.OpenStandardOutput();

// FIX1 (collision #1/#4): which app's pipe do we relay to? A relay copy that
// ships beside the Electron sidecar carries a `browser-host.pipe` file naming
// its pipe; a relay with no such file (the WPF app's copy) uses the shared
// default — so the WPF relay is byte-for-byte unchanged. The file holds only a
// pipe name (no secret); it is written by the sidecar at deploy time.
static string ResolvePipeName()
{
    try
    {
        var cfg = Path.Combine(AppContext.BaseDirectory, "browser-host.pipe");
        if (File.Exists(cfg))
        {
            var name = File.ReadAllText(cfg).Trim();
            if (name.Length is > 0 and <= 128) return name;
        }
    }
    catch { /* unreadable — fall back to the default below */ }
    return BrowserWire.PipeName();
}

using var pipe = new NamedPipeClientStream(
    ".", ResolvePipeName(), PipeDirection.InOut, PipeOptions.Asynchronous);
try
{
    await pipe.ConnectAsync(5000);
}
catch (TimeoutException)
{
    // App not running (or bridge not started) — nothing to relay. Chrome will
    // see the port close and the extension reports "not connected".
    return 1;
}

using var cts = new CancellationTokenSource();

async Task Pump(Stream from, Stream to)
{
    try
    {
        while (await BrowserWire.ReadFrameAsync(from, cts.Token) is { } frame)
            await BrowserWire.WriteFrameAsync(to, frame, cts.Token);
    }
    catch (Exception)
    {
        // Broken pipe / closed stdio / oversize frame — all mean "session over".
    }
    cts.Cancel();
}

await Task.WhenAny(Pump(stdin, pipe), Pump(pipe, stdout));
return 0;
