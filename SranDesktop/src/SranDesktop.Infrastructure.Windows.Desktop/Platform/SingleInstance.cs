using System.IO;
using System.IO.Pipes;
using System.Threading;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Platform;

/// <summary>
/// Ensures only one instance runs. A second launch forwards its args over a
/// named pipe and exits; the first instance activates its window and, when
/// the args carry a ``sran://`` deep link (Phase 7 C2), hands the URI to the
/// deep-link callback. Wire format lives in <see cref="SingleInstanceProtocol"/>
/// and stays backward-compatible: an old sender's bare "activate" still works.
///
/// Shell Phase 1: moved here from SranDesktop.App so the WebView2 shell can
/// reuse it. The instance name is a parameter because the two hosts must NOT
/// share a mutex — otherwise launching the shell would forward its args to a
/// running WPF app and exit. The WPF app keeps the historical default name.
/// </summary>
public static class SingleInstance
{
    public const string DefaultInstanceName = "SranDesktop";

    private static string _mutexName = $"{DefaultInstanceName}_SingleInstance_Mutex";
    private static string _pipeName = $"{DefaultInstanceName}_SingleInstance_Pipe";

    private static Mutex? _mutex;
    private static CancellationTokenSource? _listenerCts;

    /// <summary>True if this is the first instance. Otherwise the args were
    /// forwarded to the running instance.</summary>
    public static bool Claim(string[]? args = null, string instanceName = DefaultInstanceName)
    {
        _mutexName = $"{instanceName}_SingleInstance_Mutex";
        _pipeName = $"{instanceName}_SingleInstance_Pipe";
        _mutex = new Mutex(initiallyOwned: true, _mutexName, out var isNew);
        if (isNew)
            return true;

        try
        {
            using var client = new NamedPipeClientStream(".", _pipeName, PipeDirection.Out);
            client.Connect(2000);
            using var writer = new StreamWriter(client) { AutoFlush = true };
            writer.Write(SingleInstanceProtocol.Encode(args));
        }
        catch (Exception)
        {
            // The existing instance may be starting up or shutting down — nothing
            // we can do; the user can click the tray icon.
        }

        return false;
    }

    /// <summary>Listen (background) for second-instance messages, marshaling
    /// onActivate to the dispatcher. ``onDeepLink`` receives a forwarded
    /// ``sran://`` URI (already length-capped by the protocol parser); the
    /// window is activated in that case too.</summary>
    public static void ListenForActivation(Action onActivate, Action<string>? onDeepLink = null)
    {
        _listenerCts = new CancellationTokenSource();
        var ct = _listenerCts.Token;

        _ = Task.Run(async () =>
        {
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    using var server = new NamedPipeServerStream(
                        _pipeName, PipeDirection.In, 1,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
                    await server.WaitForConnectionAsync(ct);

                    using var reader = new StreamReader(server);
                    var message = await reader.ReadToEndAsync(ct);
                    var parsed = SingleInstanceProtocol.Parse(message);
                    System.Windows.Application.Current?.Dispatcher.InvokeAsync(() =>
                    {
                        if (parsed.Activate)
                            onActivate();
                        if (parsed.DeepLink is not null)
                            onDeepLink?.Invoke(parsed.DeepLink);
                    });
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception)
                {
                    // Transient pipe error — loop and recreate the server.
                }
            }
        }, ct);
    }

    public static void Release()
    {
        _listenerCts?.Cancel();
        _listenerCts?.Dispose();
        try
        {
            _mutex?.ReleaseMutex();
        }
        catch (ApplicationException)
        {
            // Not owned (never claimed) — ignore.
        }
        _mutex?.Dispose();
    }
}
