using System.Text.Json;
using SranDesktop.DocumentWorker;

// SranDesktop.DocumentWorker — document work, isolated.
//
// Runs inside the capability-free AppContainer with no network, no host
// environment, and no path outside its working directory. It speaks a file
// protocol rather than stdio so the host can still read a partial answer after a
// crash: request.json in, response.json out, relative paths only.

// The AppContainer rewrites %LOCALAPPDATA%\Packages\<pkg>\AC\Temp for us but does
// not create it, and the OpenXML/ClosedXML packages allocate temp files while
// saving. Materialize it here so a missing temp path cannot be mistaken for a
// corrupt document.
try
{
    Directory.CreateDirectory(Path.GetTempPath());
}
catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
{
    Console.Error.WriteLine($"WorkerTempUnavailable: {ex.Message}");
    return 4;
}

var workingDirectory = Directory.GetCurrentDirectory();
var requestPath = Path.Combine(workingDirectory, "request.json");
var responsePath = Path.Combine(workingDirectory, "response.json");
var startedUtc = DateTimeOffset.UtcNow;

WorkerRequest? request = null;
try
{
    request = JsonSerializer.Deserialize<WorkerRequest>(
        await File.ReadAllTextAsync(requestPath),
        WorkerJson.Options);
}
catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
{
    // No readable request: exit non-zero WITHOUT a response. The host classifies
    // a missing answer conservatively, which is the correct reading here.
    Console.Error.WriteLine($"WorkerRequestUnreadable: {ex.Message}");
    return 2;
}

if (request is null || request.ProtocolVersion != 1)
{
    Console.Error.WriteLine("WorkerProtocolMismatch");
    return 3;
}

WorkerResponse response;
try
{
    response = await DocumentOperations.ExecuteAsync(request, workingDirectory, startedUtc);
}
catch (Exception ex)
{
    // An unexpected fault still gets a truthful report: whether anything was
    // written is unknown to us, so we do not claim "no effect".
    response = WorkerResponse.Failed(
        request,
        startedUtc,
        "WorkerFault",
        $"{ex.GetType().Name}: {ex.Message}",
        sideEffectPerformed: true);
}

await File.WriteAllTextAsync(
    responsePath,
    JsonSerializer.Serialize(response, WorkerJson.Options));
return response.Succeeded ? 0 : 1;
