param(
    [string]$Configuration = "Release",
    [string]$ResultsDirectory = "$PSScriptRoot\results"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$testProject = Join-Path $repoRoot "src\SranDesktop.Tests\SranDesktop.Tests.csproj"
$probeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sran-symlink-probe-" + [guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Path $probeRoot | Out-Null
    $target = Join-Path $probeRoot "target.txt"
    $link = Join-Path $probeRoot "link.txt"
    [System.IO.File]::WriteAllText($target, "probe")
    try {
        New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
    }
    catch {
        throw "Symbolic-link creation is unavailable. Use a disposable Windows VM with Developer Mode or SeCreateSymbolicLinkPrivilege. $($_.Exception.Message)"
    }

    New-Item -ItemType Directory -Force -Path $ResultsDirectory | Out-Null
    dotnet test $testProject `
        --configuration $Configuration `
        --filter "Category=PrivilegedWindowsSecurity" `
        --logger "trx;LogFileName=privileged-filesystem.trx" `
        --results-directory $ResultsDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Privileged filesystem security tests failed with exit code $LASTEXITCODE."
    }

    [xml]$trx = Get-Content -LiteralPath (Join-Path $ResultsDirectory "privileged-filesystem.trx")
    $counters = $trx.TestRun.ResultSummary.Counters
    if ([int]$counters.notExecuted -ne 0) {
        throw "Privileged security acceptance cannot pass with skipped/not-executed tests."
    }
}
finally {
    if (Test-Path -LiteralPath $probeRoot) {
        Remove-Item -LiteralPath $probeRoot -Recurse -Force
    }
}
