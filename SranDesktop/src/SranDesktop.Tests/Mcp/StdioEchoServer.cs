using System.Text;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// A REAL child-process MCP server for the stdio transport tests: Windows
/// PowerShell 5.1 running a newline-delimited JSON-RPC loop. powershell.exe
/// is preinstalled on every Windows box including the windows-latest CI
/// runners, so this is the most portable spawn target (no node/npm, no extra
/// compile step). Passed via -EncodedCommand to dodge all quoting issues.
///
/// Framing note: [Console]::Out.WriteLine emits \r\n on Windows — every reply
/// therefore also exercises the client's trailing-\r tolerance.
///
/// Behaviors:
///   initialize            → emits an UNSOLICITED notification line first
///                           (clients must skip non-responses), then the result
///   tools/list            → stdio_echo / stdio_crash / stdio_bye
///   tools/call stdio_echo → echoes params.arguments.text back as content
///   tools/call stdio_crash→ exits WITHOUT replying (pending-call crash path)
///   tools/call stdio_bye  → replies, sleeps 300ms, then exits (idle-exit path)
///   debug/env             → returns %SRAN_STDIO_TEST_ENV%
///   any other request     → JSON-RPC method-not-found error
/// </summary>
internal static class StdioEchoServer
{
    private const string Script = """
        while ($true) {
          $line = [Console]::In.ReadLine()
          if ($null -eq $line) { break }
          if ($line.Trim() -eq '') { continue }
          try { $msg = $line | ConvertFrom-Json } catch { continue }
          $id = $msg.id
          if ($null -eq $id) { continue }
          $out = [Console]::Out
          if ($msg.method -eq 'initialize') {
            $out.WriteLine('{"jsonrpc":"2.0","method":"notifications/message","params":{"level":"info","data":"unsolicited"}}')
            $out.WriteLine('{"jsonrpc":"2.0","id":' + $id + ',"result":{"serverInfo":{"name":"stdio-echo"},"capabilities":{}}}')
          } elseif ($msg.method -eq 'tools/list') {
            $out.WriteLine('{"jsonrpc":"2.0","id":' + $id + ',"result":{"tools":[{"name":"stdio_echo","description":"Echo text","inputSchema":{"type":"object","properties":{"text":{"type":"string"}}}},{"name":"stdio_crash","description":"Exit without replying","inputSchema":{"type":"object"}},{"name":"stdio_bye","description":"Reply then exit","inputSchema":{"type":"object"}}]}}')
          } elseif ($msg.method -eq 'tools/call') {
            $name = $msg.params.name
            if ($name -eq 'stdio_crash') { exit 1 }
            if ($name -eq 'stdio_bye') {
              $out.WriteLine('{"jsonrpc":"2.0","id":' + $id + ',"result":{"content":[{"type":"text","text":"bye"}],"isError":false}}')
              Start-Sleep -Milliseconds 300
              exit 0
            }
            $reply = [ordered]@{ jsonrpc = '2.0'; id = $id; result = [ordered]@{ content = @(,[ordered]@{ type = 'text'; text = [string]$msg.params.arguments.text }); isError = $false } }
            $out.WriteLine((ConvertTo-Json -InputObject $reply -Depth 6 -Compress))
          } elseif ($msg.method -eq 'debug/env') {
            $out.WriteLine('{"jsonrpc":"2.0","id":' + $id + ',"result":{"value":"' + $env:SRAN_STDIO_TEST_ENV + '"}}')
          } else {
            $out.WriteLine('{"jsonrpc":"2.0","id":' + $id + ',"error":{"code":-32601,"message":"method not found"}}')
          }
          [Console]::Error.WriteLine('handled ' + $msg.method)
        }
        """;

    /// <summary>Command + args to spawn the echo server.</summary>
    public static (string Command, List<string> Args) CommandLine()
    {
        var encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(Script));
        return ("powershell.exe", new List<string>
        {
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded
        });
    }
}
