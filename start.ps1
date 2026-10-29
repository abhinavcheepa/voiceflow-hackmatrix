param([switch]$Stop, [switch]$NoBridge, [switch]$Watch)

# Starts every part of VoiceFlow AI and reports what is actually live.
#
#   .\start.ps1              start everything
#   .\start.ps1 -Stop        stop everything
#   .\start.ps1 -NoBridge    skip WhatsApp (no Chrome needed)
#
# Each service runs detached, so closing this window leaves them running.
# Use -Stop to shut them down.


$ErrorActionPreference = "Continue"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Write-Host "VoiceFlow AI initializing from: $root" -ForegroundColor DarkGray
$envFile = Join-Path $root "backend\.env"

function Get-EnvValue($key) {
    if (-not (Test-Path $envFile)) { return "" }
    $m = [regex]::Match((Get-Content $envFile -Raw), "(?m)^$key=(.*)$")
    if ($m.Success) { $m.Groups[1].Value.Trim() } else { "" }
}

function Stop-Ours($pattern) {
    $ids = @(Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like $pattern } |
        ForEach-Object { $_.ProcessId })
    # A command line doesn't always name the project - the bridge runs plain
    # "node index.js", and a system-Python uvicorn names no path at all. So
    # also take whoever is holding our ports, or the next start finds them taken.
    $ids += @(Get-NetTCPConnection -LocalPort 8000, 5174, 8100 -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { $_.OwningProcess })
    $ids | Select-Object -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

function Wait-For($url, $headers, $seconds = 40) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try { Invoke-WebRequest $url -Headers $headers -UseBasicParsing -TimeoutSec 2 | Out-Null; return $true }
        catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

# -- stop ----------------------------------------------------------------
if ($Stop) {
    Write-Host "Stopping VoiceFlow AI..." -ForegroundColor Yellow
    Stop-Ours "*voiceflow-ai*"
    Write-Host "Stopped." -ForegroundColor Green
    exit 0
}

# -- preflight ---------------------------------------------------------- 
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

# -- backend ------------------------------------------------------------ 
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "main:app", "--port", "8000" `
    -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
$backendUp = Wait-For "http://127.0.0.1:8000/health" @{}

# -- frontend ------------------------------------------------------------
if (-not (Test-Path (Join-Path $root "node_modules"))) {
    Write-Host "Installing frontend dependencies (first run only)..." -ForegroundColor Yellow
    & npm.cmd install --prefix $root | Out-Null
}
Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory $root -WindowStyle Hidden
$frontendUp = Wait-For "http://localhost:5174" @{}

# -- whatsapp bridge ---------------------------------------------------- 
$bridgeUp = $false
$bridgeDir = Join-Path $root "wa-bridge"
$provider = Get-EnvValue "WHATSAPP_PROVIDER"
if (-not $NoBridge -and $provider -eq "web") {
    if (Test-Path (Join-Path $bridgeDir "node_modules")) {
        $env:BRIDGE_TOKEN = Get-EnvValue "WA_BRIDGE_TOKEN"
        Start-Process -FilePath "node.exe" -ArgumentList "index.js" -WorkingDirectory $bridgeDir `
            -WindowStyle Hidden -RedirectStandardOutput "$bridgeDir\bridge.log" -RedirectStandardError "$bridgeDir\bridge.err"
        # Fast check: /status answers immediately once port 8100 listens
        $bridgeUp = Wait-For "http://127.0.0.1:8100/status" @{ "X-Bridge-Token" = $env:BRIDGE_TOKEN } 15
    }
    else {
        Write-Host "wa-bridge not installed - run: cd wa-bridge; npm install" -ForegroundColor Yellow
    }
}

# -- status --------------------------------------------------------------
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
    Line "Voice (TTS)" $h.voiceReady (Get-EnvValue "VOICE_PROVIDER")
    $waLabel = if ($provider -eq "web") { "WhatsApp Web" } else { "Meta Cloud API" }
    Line "WhatsApp" $h.whatsapp $waLabel
    Line "Phone calls (Vapi)" $h.vapi "needs VAPI_PRIVATE_KEY"
    Line "Phone calls (Asterisk)" $h.asterisk "needs ASTERISK_ARI_URL"

    # Asked of the bridge, not the backend: the backend's route needs a
    # dashboard session, and this script has no account to sign in with.
    if ($bridgeUp -and $provider -eq "web") {
        $link = Invoke-RestMethod "http://127.0.0.1:8100/status" `
            -Headers @{ "X-Bridge-Token" = (Get-EnvValue "WA_BRIDGE_TOKEN") }
        if ($link.status -eq "qr") {
            Write-Host "`n  WhatsApp needs linking - open http://localhost:5174/app/whatsapp and scan the QR" -ForegroundColor Yellow
        }
        elseif ($link.status -eq "connected") {
            Write-Host "`n  WhatsApp linked as $($link.me)" -ForegroundColor Green
        }
    }
}

Write-Host "`nOpen  http://localhost:5174/app          Stop  .\start.ps1 -Stop`n" -ForegroundColor Cyan

# -- watch -------------------------------------------------------------- 
# The bridge repairs its own WhatsApp session, but nothing restarts the
# process if it dies outright. -Watch keeps this window open and does that.
if ($Watch) {
    Write-Host "Watching. Ctrl+C to stop watching (services keep running).`n" -ForegroundColor DarkGray
    $token = Get-EnvValue "WA_BRIDGE_TOKEN"
    while ($true) {
        Start-Sleep -Seconds 30

        $pythonAlive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like "*voiceflow-ai*" }
        if (-not $pythonAlive) {
            Write-Host "$(Get-Date -Format 'HH:mm:ss')  backend died - restarting" -ForegroundColor Yellow
            Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "main:app", "--port", "8000" `
                -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
        }

        if (-not $NoBridge -and $provider -eq "web") {
            $bridgeAlive = Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
                Where-Object { $_.CommandLine -like "*index.js*" }
            if (-not $bridgeAlive) {
                Write-Host "$(Get-Date -Format 'HH:mm:ss')  bridge died - restarting" -ForegroundColor Yellow
                $env:BRIDGE_TOKEN = $token
                Start-Process -FilePath "node.exe" -ArgumentList "index.js" -WorkingDirectory $bridgeDir `
                    -WindowStyle Hidden -RedirectStandardOutput "$bridgeDir\stdout.log" `
                    -RedirectStandardError "$bridgeDir\bridge.err"
            }
            else {
                # Alive but possibly deaf - the bridge reports how long it has
                # been silent, which is the failure that hid for three hours.
                try {
                    $link = Invoke-RestMethod "http://127.0.0.1:8100/status" `
                        -Headers @{ "X-Bridge-Token" = $token } -TimeoutSec 5
                    if ($link.status -ne "connected" -and $link.status -ne "qr") {
                        Write-Host "$(Get-Date -Format 'HH:mm:ss')  bridge status: $($link.status)" -ForegroundColor Yellow
                    }
                }
                catch {
                    Write-Host "$(Get-Date -Format 'HH:mm:ss')  bridge not answering on 8100" -ForegroundColor Yellow
                }
            }
        }
    }
}