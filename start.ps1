# Starts every part of VoiceFlow AI and reports what is actually live.
#
#   .\start.ps1              start everything
#   .\start.ps1 -Stop        stop everything
#   .\start.ps1 -NoBridge    skip WhatsApp (no Chrome needed)
#
# Each service runs detached, so closing this window leaves them running.
# Use -Stop to shut them down.

param([switch]$Stop, [switch]$NoBridge)

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$envFile = Join-Path $root "backend\.env"

function Get-EnvValue($key) {
    if (-not (Test-Path $envFile)) { return "" }
    $m = [regex]::Match((Get-Content $envFile -Raw), "(?m)^$key=(.*)$")
    if ($m.Success) { $m.Groups[1].Value.Trim() } else { "" }
}

function Stop-Ours($pattern) {
    Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like $pattern } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Wait-For($url, $headers, $seconds = 40) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try { Invoke-WebRequest $url -Headers $headers -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
        catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

# ── stop ────────────────────────────────────────────────────────────────
if ($Stop) {
    Write-Host "Stopping VoiceFlow AI..." -ForegroundColor Yellow
    Stop-Ours "*voiceflow-ai*"
    Write-Host "Stopped." -ForegroundColor Green
    exit 0
}

# ── preflight ───────────────────────────────────────────────────────────
if (-not (Test-Path $envFile)) {
    Write-Host "backend\.env is missing. Copy backend\.env.example to backend\.env first." -ForegroundColor Red
    exit 1
}
$python = Join-Path $root "backend\venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Backend venv missing. Run:" -ForegroundColor Red
    Write-Host "  python -m venv backend\venv; backend\venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

Write-Host "`nStarting VoiceFlow AI`n" -ForegroundColor Cyan
Stop-Ours "*voiceflow-ai*"   # never leave two copies fighting over a port
Start-Sleep -Milliseconds 800

# ── backend ─────────────────────────────────────────────────────────────
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "main:app", "--port", "8000" `
    -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
$backendUp = Wait-For "http://127.0.0.1:8000/health" @{}

# ── frontend ────────────────────────────────────────────────────────────
if (-not (Test-Path (Join-Path $root "node_modules"))) {
    Write-Host "Installing frontend dependencies (first run only)..." -ForegroundColor Yellow
    & npm.cmd install --prefix $root | Out-Null
}
Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory $root -WindowStyle Hidden
$frontendUp = Wait-For "http://localhost:5174" @{}

# ── whatsapp bridge ─────────────────────────────────────────────────────
$bridgeUp = $false
$bridgeDir = Join-Path $root "wa-bridge"
$provider = Get-EnvValue "WHATSAPP_PROVIDER"
if (-not $NoBridge -and $provider -eq "web") {
    if (Test-Path (Join-Path $bridgeDir "node_modules")) {
        $env:BRIDGE_TOKEN = Get-EnvValue "WA_BRIDGE_TOKEN"
        Start-Process -FilePath "node.exe" -ArgumentList "index.js" -WorkingDirectory $bridgeDir `
            -WindowStyle Hidden -RedirectStandardOutput "$bridgeDir\bridge.log" -RedirectStandardError "$bridgeDir\bridge.err"
        $bridgeUp = Wait-For "http://127.0.0.1:8100/qr" @{} 60
        # /qr 404s until a QR exists, which still proves the port is listening.
        if (-not $bridgeUp) { $bridgeUp = Wait-For "http://127.0.0.1:8100/status" @{ "X-Bridge-Token" = $env:BRIDGE_TOKEN } 5 }
    }
    else {
        Write-Host "wa-bridge not installed — run: cd wa-bridge; npm install" -ForegroundColor Yellow
    }
}

# ── status ──────────────────────────────────────────────────────────────
function Line($label, $ok, $detail) {
    $mark = if ($ok) { "OK  " } else { "--  " }
    $colour = if ($ok) { "Green" } else { "DarkGray" }
    Write-Host ("  {0} {1,-22} {2}" -f $mark, $label, $detail) -ForegroundColor $colour
}

Write-Host "`nServices" -ForegroundColor Cyan
Line "Backend" $backendUp "http://localhost:8000"
Line "Dashboard" $frontendUp "http://localhost:5174/app"
Line "WhatsApp bridge" $bridgeUp "http://localhost:8100"

if ($backendUp) {
    $h = Invoke-RestMethod "http://127.0.0.1:8000/health"
    Write-Host "`nIntegrations" -ForegroundColor Cyan
    Line "Replies (LLM)" $h.brain "Groq"
    Line "Transcription" ($h.stt -ne $null) $h.stt
    Line "Voice (TTS)" $h.voiceReady (Invoke-RestMethod "http://127.0.0.1:8000/api/voice/profile").name
    Line "WhatsApp" $h.whatsapp $(if ($provider -eq "web") { "WhatsApp Web" } else { "Meta Cloud API" })
    Line "Phone calls (Vapi)" $h.vapi "needs VAPI_PRIVATE_KEY"
    Line "Phone calls (Asterisk)" $h.asterisk "needs ASTERISK_ARI_URL"

    if ($bridgeUp -and $provider -eq "web") {
        $link = Invoke-RestMethod "http://127.0.0.1:8000/api/whatsapp/link"
        if ($link.status -eq "qr") {
            Write-Host "`n  WhatsApp needs linking — open http://localhost:5174/app/whatsapp and scan the QR" -ForegroundColor Yellow
        }
        elseif ($link.status -eq "connected") {
            Write-Host "`n  WhatsApp linked as $($link.number)" -ForegroundColor Green
        }
    }
}

Write-Host "`nOpen  http://localhost:5174/app          Stop  .\start.ps1 -Stop`n" -ForegroundColor Cyan
