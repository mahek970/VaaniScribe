param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$Token
)

$ErrorActionPreference = "Stop"

$base = $BaseUrl.TrimEnd('/')
$headers = @{ Authorization = "Bearer $Token" }

Write-Host "[bridge-test] Health check..." -ForegroundColor Cyan
try {
    $health = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 10
    Write-Host "[bridge-test] /health -> $($health.StatusCode)" -ForegroundColor Green
}
catch {
    Write-Host "[bridge-test] /health failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "[bridge-test] Push test payload..." -ForegroundColor Cyan
$payload = @{
    connected = $true
    device = "bridge_test"
    interim = ""
    final_lines = @("hello from bridge_test")
    updated_at = [double]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
} | ConvertTo-Json

try {
    $push = Invoke-RestMethod -Uri "$base/push" -Method Post -Headers $headers -ContentType "application/json" -Body $payload -TimeoutSec 10
    Write-Host "[bridge-test] /push ok. line_count=$($push.line_count)" -ForegroundColor Green
}
catch {
    Write-Host "[bridge-test] /push failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "[bridge-test] Fetch state..." -ForegroundColor Cyan
try {
    $state = Invoke-RestMethod -Uri "$base/state" -Headers $headers -TimeoutSec 10
    Write-Host "[bridge-test] /state ok. connected=$($state.connected) lines=$($state.final_lines.Count)" -ForegroundColor Green
}
catch {
    Write-Host "[bridge-test] /state failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host "[bridge-test] SUCCESS" -ForegroundColor Green
