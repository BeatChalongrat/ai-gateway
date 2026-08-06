using System.Windows;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using SranDesktop.Presentation.Rendering;

namespace SranDesktop.Tests.Rendering;

/// <summary>What actually happened when a canvas document was loaded.</summary>
public sealed record CanvasRenderOutcome(
    bool NavigationSucceeded,
    string NavigationError,
    string ProbeResult,
    string ProcessFailure,
    IReadOnlyList<string> ArtifactMessages);

/// <summary>
/// Hosts a REAL WebView2 on a real STA thread, loads a canvas document exactly
/// the way <c>CanvasWindow</c> does, and probes the resulting DOM.
///
/// This exists because the Phase 15 suite tested the security POSTURE (CSP
/// strings, host names, navigation policy) and never once asked whether the
/// document rendered — which is how a canvas that produced an empty pane
/// shipped with a green test run. Posture assertions cannot catch a blank
/// page; only reading back the live DOM can.
///
/// Skips itself when the WebView2 runtime is absent so a machine without it
/// (CI) reports "not run" rather than a false failure.
/// </summary>
internal static class CanvasRenderHarness
{
    public static bool RuntimeAvailable
    {
        get
        {
            try
            {
                return !string.IsNullOrEmpty(
                    CoreWebView2Environment.GetAvailableBrowserVersionString());
            }
            catch (WebView2RuntimeNotFoundException)
            {
                return false;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    /// <summary>
    /// Renders <paramref name="html"/> and evaluates <paramref name="probeScript"/>
    /// against the loaded document, returning its JSON result.
    /// </summary>
    public static CanvasRenderOutcome Render(
        string html, string probeScript, int timeoutSeconds = 30)
    {
        var completion = new TaskCompletionSource<CanvasRenderOutcome>(
            TaskCreationOptions.RunContinuationsAsynchronously);

        var thread = new Thread(() =>
        {
            Window? window = null;
            var loader = new CanvasDocumentLoader();
            try
            {
                var messages = new List<string>();
                var processFailure = string.Empty;

                window = new Window
                {
                    Width = 800,
                    Height = 600,
                    // Off-screen but REALLY shown: WebView2 does not create its
                    // core process for a window that never renders.
                    Left = -10000,
                    Top = -10000,
                    ShowInTaskbar = false,
                    WindowStyle = WindowStyle.None,
                };
                var view = new WebView2();
                window.Content = view;
                window.Show();

                async void Run()
                {
                    var success = false;
                    var error = string.Empty;
                    var probe = string.Empty;
                    try
                    {
                        var environment = await CoreWebView2Environment.CreateAsync(
                            userDataFolder: Path.Combine(
                                Path.GetTempPath(), "SranDesktopTests", "WebView2"));
                        await view.EnsureCoreWebView2Async(environment);
                        var core = view.CoreWebView2;

                        // EXACTLY the posture CanvasWindow applies.
                        core.Settings.AreDevToolsEnabled = false;
                        core.Settings.IsStatusBarEnabled = false;
                        core.Settings.AreDefaultContextMenusEnabled = false;
                        core.Settings.IsPasswordAutosaveEnabled = false;
                        core.Settings.IsGeneralAutofillEnabled = false;
                        core.Settings.IsWebMessageEnabled = true;

                        var libFolder = Path.Combine(
                            AppContext.BaseDirectory, "Assets", "WebLibs");
                        if (Directory.Exists(libFolder))
                        {
                            core.SetVirtualHostNameToFolderMapping(
                                CanvasSandbox.VirtualHost, libFolder,
                                CoreWebView2HostResourceAccessKind.DenyCors);
                        }
                        core.SetVirtualHostNameToFolderMapping(
                            CanvasSandbox.DocumentHost, loader.Folder,
                            CoreWebView2HostResourceAccessKind.DenyCors);

                        core.ProcessFailed += (_, e) => processFailure = e.ProcessFailedKind.ToString();
                        core.WebMessageReceived += (_, e) =>
                        {
                            try { messages.Add(e.TryGetWebMessageAsString()); }
                            catch (ArgumentException) { /* non-string message */ }
                        };
                        core.NavigationStarting += (_, e) =>
                        {
                            var allowed = CanvasSandbox.IsNavigationAllowed(e.Uri);
                            messages.Add($"[nav] allowed={allowed} uri={Truncate(e.Uri)}");
                            if (!allowed)
                                e.Cancel = true;
                        };

                        var navigated = new TaskCompletionSource<CoreWebView2NavigationCompletedEventArgs>(
                            TaskCreationOptions.RunContinuationsAsynchronously);
                        void OnCompleted(object? _, CoreWebView2NavigationCompletedEventArgs e)
                        {
                            core.NavigationCompleted -= OnCompleted;
                            navigated.TrySetResult(e);
                        }
                        core.NavigationCompleted += OnCompleted;

                        // The SAME loader the Canvas window uses.
                        loader.Load(core, html, CanvasSandbox.NewRenderToken());

                        var completed = await navigated.Task.WaitAsync(
                            TimeSpan.FromSeconds(timeoutSeconds));
                        success = completed.IsSuccess;
                        error = completed.WebErrorStatus.ToString();

                        // Scripts inside the document (React/mermaid bootstraps)
                        // finish after load; give them a beat before probing.
                        await Task.Delay(700);
                        probe = await core.ExecuteScriptAsync(probeScript);
                    }
                    catch (Exception ex)
                    {
                        error = ex.GetType().Name + ": " + ex.Message;
                    }
                    finally
                    {
                        completion.TrySetResult(new CanvasRenderOutcome(
                            success, error, probe, processFailure, messages));
                        loader.Dispose();
                        window!.Close();
                        Dispatcher.CurrentDispatcher.BeginInvokeShutdown(DispatcherPriority.Background);
                    }
                }

                Dispatcher.CurrentDispatcher.BeginInvoke((Action)Run);
                Dispatcher.Run();
            }
            catch (Exception ex)
            {
                completion.TrySetResult(new CanvasRenderOutcome(
                    false, ex.Message, string.Empty, string.Empty, Array.Empty<string>()));
                try { window?.Close(); } catch { /* shutting down */ }
            }
        });

        static string Truncate(string? s) =>
            s is null ? "<null>" : s.Length <= 120 ? s : s[..120] + "…";

        thread.SetApartmentState(ApartmentState.STA);
        thread.IsBackground = true;
        thread.Start();

        return completion.Task.Wait(TimeSpan.FromSeconds(timeoutSeconds + 20))
            ? completion.Task.Result
            : new CanvasRenderOutcome(false, "harness timed out", string.Empty, string.Empty,
                Array.Empty<string>());
    }
}
