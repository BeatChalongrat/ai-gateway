using System.IO.Pipes;
using SranDesktop.Infrastructure.Native.Browser;

if (!BrowserNativeHostPolicy.IsAllowedInvocation(args))
    return 2;

// SranDesktop.BrowserHost — the native-messaging relay. Chrome only speaks
// native messaging to a process IT spawns, so this exe exists solely to
// forward frames 1:1 between Chrome (stdio) and the running SRAN Desktop app
// (named pipe). Deliberately dumb: no parsing, no logic — pairing and routing
// live in the app's BrowserBridgeService. Exits when either side closes.

using var stdin = Console.OpenStandardInput();
using var stdout = Console.OpenStandardOutput();

using var pipe = new NamedPipeClientStream(
    ".", BrowserWire.PipeName(), PipeDirection.InOut, PipeOptions.Asynchronous);
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
