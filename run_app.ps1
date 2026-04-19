param(
    [int]$Port = 8501,
    [switch]$Headless,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

function Test-PortInUse {
    param([int]$TargetPort)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction SilentlyContinue
    return ($null -ne $conn)
}

function Get-FreePort {
    param([int]$PreferredPort)
    if (-not (Test-PortInUse -TargetPort $PreferredPort)) {
        return $PreferredPort
    }

    foreach ($candidate in ($PreferredPort + 1)..($PreferredPort + 20)) {
        if (-not (Test-PortInUse -TargetPort $candidate)) {
            return $candidate
        }
    }

    throw "No free port found between $PreferredPort and $($PreferredPort + 20)."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$selectedPort = Get-FreePort -PreferredPort $Port
$url = "http://127.0.0.1:$selectedPort"

Write-Host "[system] Starting VaaniScribe on $url" -ForegroundColor Cyan
if ($selectedPort -ne $Port) {
    Write-Host "[warn] Port $Port is busy. Switched to $selectedPort" -ForegroundColor Yellow
}

if (-not $NoBrowser) {
    Start-Process $url | Out-Null
}

$args = @(
    "-m", "streamlit", "run", "app.py",
    "--server.address", "127.0.0.1",
    "--server.port", "$selectedPort"
)

if ($Headless) {
    $args += @("--server.headless", "true")
}

& $python @args