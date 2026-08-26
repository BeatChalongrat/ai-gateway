using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using SranDesktop.Infrastructure.Mcp.Remote;

// ---------------------------------------------------------------------------
// E0 probe — the parent process.
//
//   SranDesktop.SidecarProbe <sidecar.exe> <output-dir> [label]
//
// Spawns the published sidecar as a CHILD PROCESS through the production
// StdioMcpTransport, performs the MCP handshake, then proves and measures:
//   §2  a tools/call round-trip whose effect is a file containing a value
//       generated in this run, verified by reading the filesystem directly;
//   §3  cold start, idle/during-call RSS, per-tool latency (median of 3).
// Every number is measured from THIS side of the pipe, i.e. what the
// Electron host would observe. Results → <output-dir>/e0-probe-<label>.json
// ---------------------------------------------------------------------------

if (args.Length < 2)
{
    Console.Error.WriteLine("usage: SranDesktop.SidecarProbe <sidecar.exe> <output-dir> [label]");
    return 2;
}
Console.OutputEncoding = Encoding.UTF8;
var sidecarPath = Path.GetFullPath(args[0]);
var outDir = Path.GetFullPath(args[1]);
var label = args.Length > 2 ? args[2] : "run";
Directory.CreateDirectory(outDir);
// Single-file + IncludeNativeLibrariesForSelfExtract: the first launch on a
// machine extracts the native libraries to %TEMP%\.net\<app>\<bundle-id>; later
// launches reuse them. Whether that directory existed tells apart a
// first-launch-after-install cold start from a steady-state one.
var extractDir = Path.Combine(Path.GetTempPath(), ".net", Path.GetFileNameWithoutExtension(sidecarPath));
var extractDirExistedBefore = Directory.Exists(extractDir);

var report = new JsonObject
{
    ["label"] = label,
    ["startedUtc"] = DateTime.UtcNow.ToString("O"),
    ["probePid"] = Environment.ProcessId,
    ["sidecarPath"] = sidecarPath,
    ["sidecarBytes"] = new FileInfo(sidecarPath).Length,
    ["machine"] = $"{Environment.OSVersion} / {System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture} / {Environment.ProcessorCount} cores",
};
var failures = new List<string>();
void Check(bool ok, string what)
{
    Console.WriteLine($"  [{(ok ? "PASS" : "FAIL")}] {what}");
    if (!ok) failures.Add(what);
}

using var loggerFactory = LoggerFactory.Create(b => b.AddProvider(new StderrLoggerProvider()).SetMinimumLevel(LogLevel.Information));
var log = loggerFactory.CreateLogger("Probe");

Console.WriteLine($"E0 probe [{label}] — probe pid {Environment.ProcessId}");
Console.WriteLine($"  sidecar: {sidecarPath} ({new FileInfo(sidecarPath).Length / 1024.0 / 1024.0:F1} MiB)");

var sidecarNamesBefore = Process.GetProcessesByName("SranDesktop.ToolsSidecar").Select(p => p.Id).ToHashSet();

// --- spawn + handshake ----------------------------------------------------
// The transport is the production one (Phase 12 D) — no probe-specific wire code.
await using var transport = new StdioMcpTransport(sidecarPath, logger: loggerFactory.CreateLogger("Transport"));
var exitedEarly = false;
transport.Exited += (_, _) => exitedEarly = true;

var coldStart = Stopwatch.StartNew();
var initResponse = await transport.SendRequestAsync("initialize", new
{
    protocolVersion = "2025-06-18",
    capabilities = new { },
    clientInfo = new { name = "SranDesktop.SidecarProbe", version = "e0" }
});
var tInitialize = coldStart.Elapsed.TotalMilliseconds;
await transport.SendNotificationAsync("notifications/initialized", null);
Check(initResponse.Error is null, "initialize returned a result");

var init = initResponse.Result!.Value;
var serverName = init.GetProperty("serverInfo").GetProperty("name").GetString();
var instructions = init.TryGetProperty("instructions", out var instr) ? instr.GetString() ?? "" : "";
var childPid = int.Parse(instructions.Replace("pid=", ""));
// tools/list through the production protocol client (McpRemoteClient).
var client = new McpRemoteClient(transport);
var listSw = Stopwatch.StartNew();
var tools = await client.ListToolsAsync();
var tList = listSw.Elapsed.TotalMilliseconds;

// --- §2 round-trip with a runtime-chosen value ----------------------------
// Kept back-to-back with the handshake so the cold-start stopwatch measures
// launch → initialize → tools/list → first tools/call with nothing else in it.
var runId = Guid.NewGuid().ToString("N");
var randomValue = $"e0-{runId}-{Random.Shared.NextInt64():X16}-{DateTime.UtcNow:O}";
var targetFile = Path.Combine(outDir, $"roundtrip-{runId}.txt");
var targetExistedBefore = File.Exists(targetFile);

var firstCall = Stopwatch.StartNew();
var writeResp = await transport.SendRequestAsync("tools/call", new
{
    name = "write_file",
    arguments = new { path = targetFile, content = randomValue }
});
var tFirstCall = firstCall.Elapsed.TotalMilliseconds;
var tColdToFirstCallResponse = coldStart.Elapsed.TotalMilliseconds;
coldStart.Stop();

// --- process identity (after the timed section) ----------------------------
Check(serverName == "SranDesktop.ToolsSidecar", $"serverInfo.name = {serverName}");
Check(childPid != Environment.ProcessId, $"sidecar pid {childPid} ≠ probe pid {Environment.ProcessId}");
Check(!sidecarNamesBefore.Contains(childPid), "sidecar pid did not exist before this run");

var child = Process.GetProcessById(childPid);
string childExe;
try { childExe = child.MainModule?.FileName ?? "?"; } catch (Exception ex) { childExe = $"(unreadable: {ex.Message})"; }
Check(string.Equals(childExe, sidecarPath, StringComparison.OrdinalIgnoreCase), $"pid {childPid} main module = {childExe}");
Console.WriteLine($"  sidecar process: name={child.ProcessName} started={child.StartTime:HH:mm:ss.fff} exe={childExe}");
var parentOfChild = QueryParentPid(childPid);
Check(parentOfChild == Environment.ProcessId, $"Win32_Process.ParentProcessId of {childPid} = {parentOfChild} (probe is {Environment.ProcessId})");

var toolNames = tools.Select(t => t.Name).OrderBy(n => n).ToArray();
Check(toolNames.SequenceEqual(new[] { "computer_screenshot", "run_command", "write_file" }), $"tools/list = [{string.Join(", ", toolNames)}]");

Check(!targetExistedBefore, $"target file absent before call: {targetFile}");
Check(writeResp.Error is null && !IsError(writeResp), $"write_file result: {FirstText(writeResp)}");
Check(File.Exists(targetFile), "target file exists after call (read by probe from the filesystem)");
var onDisk = File.Exists(targetFile) ? File.ReadAllText(targetFile, Encoding.UTF8) : "";
Check(onDisk == randomValue, $"file content == runtime value ({randomValue})");
Console.WriteLine($"  cold start: launch→initialize {tInitialize:F0} ms · tools/list {tList:F0} ms · first tools/call {tFirstCall:F0} ms · launch→first tools/call response {tColdToFirstCallResponse:F0} ms");

// --- §3 idle RSS ----------------------------------------------------------
await Task.Delay(1000);
var idle = Sample(child);
Console.WriteLine($"  idle RSS: working set {Mib(idle.ws)} MiB · private {Mib(idle.priv)} MiB");

// --- §3 per-call latency, median of 3 ------------------------------------
var writeLat = new List<double>();
for (var i = 0; i < 3; i++)
{
    var p = Path.Combine(outDir, $"latency-{runId}-{i}.txt");
    var v = $"{runId}:{i}:{Random.Shared.Next()}";
    var sw = Stopwatch.StartNew();
    var r = await transport.SendRequestAsync("tools/call", new { name = "write_file", arguments = new { path = p, content = v } });
    sw.Stop();
    Check(r.Error is null && !IsError(r) && File.ReadAllText(p) == v, $"write_file #{i + 1} {sw.Elapsed.TotalMilliseconds:F1} ms, verified on disk");
    writeLat.Add(sw.Elapsed.TotalMilliseconds);
}

var cmdLat = new List<double>();
for (var i = 0; i < 3; i++)
{
    var token = $"token-{runId}-{i}-{Random.Shared.Next()}";
    var sw = Stopwatch.StartNew();
    var r = await transport.SendRequestAsync("tools/call", new { name = "run_command", arguments = new { command = $"echo {token}" } });
    sw.Stop();
    var text = FirstText(r);
    Check(r.Error is null && !IsError(r) && text.Contains(token) && text.Contains("[exit code 0]"), $"run_command #{i + 1} {sw.Elapsed.TotalMilliseconds:F1} ms, echoed token seen in output");
    cmdLat.Add(sw.Elapsed.TotalMilliseconds);
}

var shotLat = new List<double>();
long shotBytes = 0, shotWireChars = 0; var shotDims = "";
for (var i = 0; i < 3; i++)
{
    var sw = Stopwatch.StartNew();
    var r = await transport.SendRequestAsync("tools/call", new { name = "computer_screenshot", arguments = new { } });
    sw.Stop();
    var ok = r.Error is null && !IsError(r);
    var image = ok ? r.Result!.Value.GetProperty("content").EnumerateArray().FirstOrDefault(b => b.GetProperty("type").GetString() == "image") : default;
    var hasImage = image.ValueKind == JsonValueKind.Object;
    byte[] png = Array.Empty<byte>();
    if (hasImage)
    {
        var data = image.GetProperty("data").GetString()!;
        shotWireChars = data.Length;
        png = Convert.FromBase64String(data);
        shotBytes = png.Length;
    }
    var isPng = png.Length > 8 && png[0] == 0x89 && png[1] == 0x50 && png[2] == 0x4E && png[3] == 0x47 && png[4] == 0x0D && png[5] == 0x0A && png[6] == 0x1A && png[7] == 0x0A;
    if (isPng)
    {
        // IHDR width/height (big-endian) at bytes 16..23 — proves it is a real image, not a stub.
        var w = (png[16] << 24) | (png[17] << 16) | (png[18] << 8) | png[19];
        var h = (png[20] << 24) | (png[21] << 16) | (png[22] << 8) | png[23];
        shotDims = $"{w}x{h}";
        if (i == 0) File.WriteAllBytes(Path.Combine(outDir, $"screenshot-{runId}.png"), png);
    }
    Check(ok && hasImage && isPng && image.GetProperty("mimeType").GetString() == "image/png",
        $"computer_screenshot #{i + 1} {sw.Elapsed.TotalMilliseconds:F1} ms, PNG {shotDims} {shotBytes / 1024} KiB decoded ({shotWireChars / 1024} KiB base64 on the wire)");
    shotLat.Add(sw.Elapsed.TotalMilliseconds);
}

// --- §3 RSS during a call --------------------------------------------------
// A ~2 s command so there is time to sample the working set mid-call.
var (busyWs, busyPriv) = (0L, 0L);
var busyTask = transport.SendRequestAsync("tools/call", new { name = "run_command", arguments = new { command = "ping -n 3 127.0.0.1 >nul & echo done" } });
while (!busyTask.IsCompleted)
{
    var s = Sample(child);
    busyWs = Math.Max(busyWs, s.ws); busyPriv = Math.Max(busyPriv, s.priv);
    await Task.Delay(50);
}
Check((await busyTask).Error is null, "run_command (2 s ping) completed while RSS was sampled");
Console.WriteLine($"  RSS during run_command: working set {Mib(busyWs)} MiB · private {Mib(busyPriv)} MiB");

var (shotWs, shotPriv) = (0L, 0L);
var shotTask = transport.SendRequestAsync("tools/call", new { name = "computer_screenshot", arguments = new { } });
while (!shotTask.IsCompleted)
{
    var s = Sample(child);
    shotWs = Math.Max(shotWs, s.ws); shotPriv = Math.Max(shotPriv, s.priv);
    await Task.Delay(5);
}
Check((await shotTask).Error is null, "computer_screenshot completed while RSS was sampled");
Console.WriteLine($"  RSS during computer_screenshot: working set {Mib(shotWs)} MiB · private {Mib(shotPriv)} MiB");

child.Refresh();
var peakWs = child.PeakWorkingSet64;
await Task.Delay(1000);
var idleAfter = Sample(child);
Console.WriteLine($"  RSS idle after all calls: working set {Mib(idleAfter.ws)} MiB · private {Mib(idleAfter.priv)} MiB · peak working set {Mib(peakWs)} MiB");

// --- error path: unknown tool is a JSON-RPC error, not a crash -------------
var bad = await transport.SendRequestAsync("tools/call", new { name = "no_such_tool", arguments = new { } });
Check(bad.Error is not null && !exitedEarly, $"unknown tool → JSON-RPC error ({bad.Error}), process still alive");

// --- shutdown --------------------------------------------------------------
Check(!exitedEarly, "sidecar did not exit during the run");
await transport.DisposeAsync(); // closes stdin → sidecar drains and exits
var exitSw = Stopwatch.StartNew();
var exited = child.WaitForExit(5000);
Check(exited, $"sidecar exited after stdin close in {exitSw.Elapsed.TotalMilliseconds:F0} ms");
Check(!Process.GetProcessesByName("SranDesktop.ToolsSidecar").Any(p => p.Id == childPid), $"no process with pid {childPid} remains");

// --- write report ------------------------------------------------------------
report["sidecarPid"] = childPid;
report["sidecarParentPid"] = parentOfChild;
report["sidecarExe"] = childExe;
report["nativeExtractDirExistedBefore"] = extractDirExistedBefore;
report["coldStartMs"] = new JsonObject
{
    ["launchToInitializeResponse"] = Math.Round(tInitialize, 1),
    ["toolsList"] = Math.Round(tList, 1),
    ["firstToolsCall"] = Math.Round(tFirstCall, 1),
    ["launchToFirstToolsCallResponse"] = Math.Round(tColdToFirstCallResponse, 1)
};
report["rssMiB"] = new JsonObject
{
    ["idleWorkingSet"] = Mib(idle.ws), ["idlePrivate"] = Mib(idle.priv),
    ["duringRunCommandWorkingSet"] = Mib(busyWs), ["duringRunCommandPrivate"] = Mib(busyPriv),
    ["duringScreenshotWorkingSet"] = Mib(shotWs), ["duringScreenshotPrivate"] = Mib(shotPriv),
    ["idleAfterWorkingSet"] = Mib(idleAfter.ws), ["idleAfterPrivate"] = Mib(idleAfter.priv),
    ["peakWorkingSet"] = Mib(peakWs)
};
report["latencyMs"] = new JsonObject
{
    ["write_file"] = Lat(writeLat), ["run_command"] = Lat(cmdLat), ["computer_screenshot"] = Lat(shotLat)
};
report["screenshot"] = new JsonObject { ["dims"] = shotDims, ["pngBytes"] = shotBytes, ["base64Chars"] = shotWireChars };
report["roundTrip"] = new JsonObject { ["file"] = targetFile, ["value"] = randomValue, ["verifiedOnDisk"] = onDisk == randomValue };
report["failures"] = new JsonArray(failures.Select(f => (JsonNode)f).ToArray());
var reportPath = Path.Combine(outDir, $"e0-probe-{label}.json");
File.WriteAllText(reportPath, report.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"  report: {reportPath}");
Console.WriteLine(failures.Count == 0 ? "ALL CHECKS PASSED" : $"{failures.Count} CHECK(S) FAILED");
return failures.Count == 0 ? 0 : 1;

// --- helpers -----------------------------------------------------------------
static bool IsError(JsonRpcResponse r) =>
    r.Result is { } res && res.TryGetProperty("isError", out var e) && e.ValueKind == JsonValueKind.True;

static string FirstText(JsonRpcResponse r)
{
    if (r.Error is not null) return $"error: {r.Error}";
    if (r.Result is not { } res || !res.TryGetProperty("content", out var c)) return "";
    foreach (var b in c.EnumerateArray())
        if (b.TryGetProperty("text", out var t)) return t.GetString() ?? "";
    return "";
}

static (long ws, long priv) Sample(Process p)
{
    try { p.Refresh(); return (p.WorkingSet64, p.PrivateMemorySize64); }
    catch (InvalidOperationException) { return (0, 0); }
}

static double Mib(long bytes) => Math.Round(bytes / 1024.0 / 1024.0, 1);

static JsonObject Lat(List<double> samples)
{
    var sorted = samples.OrderBy(x => x).ToList();
    return new JsonObject
    {
        ["samples"] = new JsonArray(samples.Select(s => (JsonNode)Math.Round(s, 1)).ToArray()),
        ["median"] = Math.Round(sorted[sorted.Count / 2], 1)
    };
}

static int QueryParentPid(int pid)
{
    try
    {
        var psi = new ProcessStartInfo("powershell.exe",
            $"-NoProfile -NonInteractive -Command \"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId\"")
        { RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true };
        using var p = Process.Start(psi)!;
        var text = p.StandardOutput.ReadToEnd().Trim();
        p.WaitForExit();
        return int.TryParse(text, out var parent) ? parent : -1;
    }
    catch { return -1; }
}

sealed class StderrLoggerProvider : ILoggerProvider
{
    public ILogger CreateLogger(string categoryName) => new L(categoryName);
    public void Dispose() { }
    private sealed class L : ILogger
    {
        private readonly string _c;
        public L(string c) => _c = c;
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel l) => l >= LogLevel.Information;
        public void Log<TState>(LogLevel l, EventId id, TState s, Exception? ex, Func<TState, Exception?, string> f) =>
            Console.Error.WriteLine($"    {l,-5} {_c}: {f(s, ex)}");
    }
}
