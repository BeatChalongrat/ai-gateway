# sign-publish.ps1 — signs the published app binaries BEFORE packaging (FIX-E).
#
# Inno's SignTool only signs Setup.exe + the uninstaller; the payload exes
# (SranDesktop.exe, SranDesktop.BrowserHost.exe) must be signed first or
# SmartScreen still flags the app at first launch.
#
# Gated on environment variables so it is a NO-OP until a certificate exists:
#   SRAN_CERT_PFX      = full path to the .pfx (kept OUTSIDE the repo)
#   SRAN_CERT_PASSWORD = pfx password (set only in the build shell, never persisted)
#
# Usage (after `dotnet publish`, before ISCC):
#   powershell -File SranDesktop\build\sign-publish.ps1
#
# See SIGNING.md for the full release flow.

$ErrorActionPreference = "Stop"

if (-not $env:SRAN_CERT_PFX -or -not $env:SRAN_CERT_PASSWORD) {
    Write-Host "SRAN_CERT_PFX / SRAN_CERT_PASSWORD not set - skipping code signing (unsigned build)."
    exit 0
}
if (-not (Test-Path $env:SRAN_CERT_PFX)) {
    throw "SRAN_CERT_PFX points at '$($env:SRAN_CERT_PFX)' which does not exist."
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    # Fall back to the newest Windows SDK install.
    $sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $sdk) { throw "signtool.exe not found - install the Windows SDK." }
    $signtool = $sdk
}

$publishDir = Join-Path $PSScriptRoot "..\publish\win-x64"
$targets = @("SranDesktop.exe", "SranDesktop.BrowserHost.exe") |
    ForEach-Object { Join-Path $publishDir $_ } |
    Where-Object { Test-Path $_ }
if (-not $targets) { throw "No publish output found in $publishDir - run dotnet publish first." }

foreach ($exe in $targets) {
    & $signtool.Source sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
        /f $env:SRAN_CERT_PFX /p $env:SRAN_CERT_PASSWORD $exe
    if ($LASTEXITCODE -ne 0) { throw "signtool failed on $exe (exit $LASTEXITCODE)" }
    Write-Host "Signed: $exe"
}
Write-Host "All publish binaries signed."
