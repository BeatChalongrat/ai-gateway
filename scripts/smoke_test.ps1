# SRAN AI Gateway — Smoke Test (Windows PowerShell)
$ErrorActionPreference = "Continue"

$Pass = 0
$Fail = 0

# Read admin key
$AdminKey = $env:ADMIN_API_KEY
if (-not $AdminKey) {
    $envFile = Join-Path $PSScriptRoot "..\admin-ui\.env.local"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "^ADMIN_API_KEY=" }
        if ($line) { $AdminKey = ($line -split "=", 2)[1] }
    }
}

function Test-Endpoint {
    param($Label, $Url, [switch]$CheckItems)

    $headers = @{}
    if ($AdminKey) { $headers["Authorization"] = "Bearer $AdminKey" }

    try {
        $resp = Invoke-RestMethod -Uri $Url -Headers $headers -TimeoutSec 5
        if ($CheckItems) {
            $count = @($resp.items).Count
            if ($count -ge 1) {
                Write-Host "  PASS  $Label  ($count items)" -ForegroundColor Green
                $script:Pass++
            } else {
                Write-Host "  FAIL  $Label  (0 items)" -ForegroundColor Red
                $script:Fail++
            }
        } else {
            Write-Host "  PASS  $Label" -ForegroundColor Green
            $script:Pass++
        }
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        Write-Host "  FAIL  $Label  (HTTP $code)" -ForegroundColor Red
        $script:Fail++
    }
}

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host " SRAN AI Gateway - Smoke Test" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host ""

Test-Endpoint "Backend health"   "http://127.0.0.1:28080/health"
Test-Endpoint "Admin: tenants"   "http://127.0.0.1:28080/admin/tenants"   -CheckItems
Test-Endpoint "Admin: models"    "http://127.0.0.1:28080/admin/models"    -CheckItems
Test-Endpoint "Admin: providers" "http://127.0.0.1:28080/admin/providers" -CheckItems
Test-Endpoint "Admin: policies"  "http://127.0.0.1:28080/admin/policies"  -CheckItems
Test-Endpoint "Frontend"         "http://127.0.0.1:23000/login"

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "  Results: $Pass passed, $Fail failed"
Write-Host "==============================" -ForegroundColor Cyan

if ($Fail -gt 0) { exit 1 }
