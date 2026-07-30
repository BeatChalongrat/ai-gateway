using System.Buffers.Binary;
using System.Text;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Workers;

// Deterministic interactive worker used to prove the transport inside the real
// AppContainer. Every behaviour the transport must survive is reachable by
// method name, so a test can ask for a crash, a duplicate answer, or a malformed
// frame on demand rather than waiting for one to happen by luck.

var stdin = Console.OpenStandardInput();
var stdout = Console.OpenStandardOutput();
var cancelled = new HashSet<string>(StringComparer.Ordinal);
var running = true;

while (running)
{
    InteractiveFrame? frame;
    try
    {
        frame = await InteractiveFrameCodec.ReadAsync(stdin, CancellationToken.None);
    }
    catch (InteractiveProtocolException)
    {
        return 3;
    }
    if (frame is null)
        break;

    switch (frame.MessageType)
    {
        case "shutdown":
            running = false;
            continue;
        case "cancel":
            if (frame.RequestId is { Length: > 0 } cancelId)
                cancelled.Add(cancelId);
            continue;
        case "request":
            break;
        default:
            continue;
    }

    var requestId = frame.RequestId ?? "";
    var generation = frame.Generation;

    switch (frame.Method)
    {
        case "$/handshake":
            // Signalled by a marker file rather than an env var: the sandbox
            // environment is an allowlist by design, and weakening it just to
            // steer a fixture would defeat the isolation being tested.
            if (File.Exists(Path.Combine(
                    Directory.GetCurrentDirectory(), "crash-on-handshake")))
                Environment.Exit(7);
            Respond(requestId, "fixture-worker/1.0", generation);
            break;

        case "echo":
            Respond(requestId, frame.Payload, generation);
            break;

        case "ack-then-echo":
            Send(new InteractiveFrame
            {
                MessageType = "ack",
                RequestId = requestId,
                Generation = generation
            });
            Respond(requestId, frame.Payload, generation);
            break;

        case "delay":
            // Answered on a worker thread so later requests overtake it — this is
            // what makes out-of-order correlation observable.
            _ = Task.Run(async () =>
            {
                var ms = int.TryParse(frame.Payload, out var value) ? value : 200;
                await Task.Delay(ms);
                if (!cancelled.Contains(requestId))
                    Respond(requestId, $"delayed:{ms}", generation);
            });
            break;

        case "notify-then-respond":
            Send(new InteractiveFrame
            {
                MessageType = "notification",
                Method = "fixture/progress",
                Payload = "step-1",
                Generation = generation
            });
            Send(new InteractiveFrame
            {
                MessageType = "progress",
                Method = "fixture/progress",
                Payload = "step-2",
                Generation = generation
            });
            Respond(requestId, "done", generation);
            break;

        case "never-respond":
            break;

        case "ignore-cancellation":
            // Deliberately answers even after a cancel; the host must still treat
            // the cancelled request as unknown rather than believing this.
            _ = Task.Run(async () =>
            {
                await Task.Delay(600);
                Respond(requestId, "answered-anyway", generation);
            });
            break;

        case "duplicate-response":
            Respond(requestId, "first", generation);
            Respond(requestId, "second", generation);
            break;

        case "worker-error":
            Send(new InteractiveFrame
            {
                MessageType = "error",
                RequestId = requestId,
                Generation = generation,
                ErrorCode = "FixtureError",
                ErrorMessage = "the fixture refused this request"
            });
            break;

        case "crash-before-response":
            // Dies with the request already parsed: the effect is unknowable.
            Environment.Exit(9);
            break;

        case "malformed-frame":
        {
            var body = Encoding.UTF8.GetBytes("{ this is not valid json");
            var header = new byte[4];
            BinaryPrimitives.WriteInt32LittleEndian(header, body.Length);
            stdout.Write(header);
            stdout.Write(body);
            stdout.Flush();
            break;
        }

        case "oversized-frame":
        {
            // Only the DECLARED length is huge; the host must refuse before
            // allocating anything.
            var header = new byte[4];
            BinaryPrimitives.WriteInt32LittleEndian(
                header, InteractiveFrameCodec.MaxFrameBytes + 1024);
            stdout.Write(header);
            stdout.Flush();
            break;
        }

        case "large-payload":
        {
            var size = int.TryParse(frame.Payload, out var value) ? value : 64 * 1024;
            Respond(requestId, new string('x', size), generation);
            break;
        }

        case "stderr-flood":
        {
            var line = new string('e', 4096);
            for (var index = 0; index < 200; index++)
                Console.Error.WriteLine(line);
            Console.Error.Flush();
            Respond(requestId, "flooded", generation);
            break;
        }

        case "spawn-child":
        {
            try
            {
                using var child = System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo(
                        Path.Combine(
                            Environment.GetFolderPath(Environment.SpecialFolder.System),
                            "cmd.exe"),
                        "/d /q /s /c \"ping -n 30 127.0.0.1 >nul\"")
                    {
                        UseShellExecute = false,
                        CreateNoWindow = true
                    });
                Respond(requestId, $"child:{child?.Id ?? -1}", generation);
            }
            catch (Exception ex)
            {
                Respond(requestId, $"child-failed:{ex.GetType().Name}", generation);
            }
            break;
        }

        case "read-env":
            Respond(
                requestId,
                Environment.GetEnvironmentVariable(frame.Payload ?? "") ?? "(absent)",
                generation);
            break;

        case "probe-network":
        {
            try
            {
                using var client = new System.Net.Sockets.TcpClient();
                var connect = client.ConnectAsync(
                    System.Net.IPAddress.Loopback,
                    int.TryParse(frame.Payload, out var port) ? port : 80);
                Respond(
                    requestId,
                    await Task.WhenAny(connect, Task.Delay(1500)) == connect && client.Connected
                        ? "connected"
                        : "blocked",
                    generation);
            }
            catch (Exception ex)
            {
                Respond(requestId, $"blocked:{ex.GetType().Name}", generation);
            }
            break;
        }

        case "wrong-generation":
            // Impersonates an older process instance.
            Respond(requestId, "stale", generation + 1000);
            break;

        default:
            Send(new InteractiveFrame
            {
                MessageType = "error",
                RequestId = requestId,
                Generation = generation,
                ErrorCode = "UnknownMethod",
                ErrorMessage = $"'{frame.Method}' is not a fixture method"
            });
            break;
    }
}

return 0;

void Respond(string requestId, string? payload, long generation) =>
    Send(new InteractiveFrame
    {
        MessageType = "response",
        RequestId = requestId,
        Generation = generation,
        Payload = payload
    });

void Send(InteractiveFrame outbound)
{
    lock (stdout)
    {
        InteractiveFrameCodec.Write(stdout, outbound);
    }
}
