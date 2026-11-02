# ==============================================================================
# VoiceFlow - Local Voice Cloning Prerequisites Installer (Windows PowerShell)
# ==============================================================================
# Installs CPU PyTorch, librosa, and OpenVoice V2 converter checkpoints.
#
# Target Directory:
#   backend/openvoice/checkpoints_v2/converter/
#
# Checkpoint Files and Exact Sizes:
#   1. config.json     : 2,084 bytes (~2.1 KB)
#   2. checkpoint.pth  : 137,847,381 bytes (~131.5 MB)
#
# Windows Compatibility Note:
#   - If installing 'openvoice' from PyPI without '--no-deps', it pulls 'pyworld'
#     which fails with: "error: Microsoft Visual C++ 14.0 or greater is required".
#     VoiceFlow only uses ToneColorConverter, so we install with --no-deps.
#   - On Python 3.12+, 'librosa' dependencies (numba, llvmlite) may require pre-built
#     wheels. Recommended Python version: 3.10 or 3.11.
# ==============================================================================

[CmdletBinding()]
param(
    [switch]$SkipPip
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CheckpointsDir = Join-Path $ScriptDir "openvoice\checkpoints_v2\converter"

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " VoiceFlow Voice Cloning Installer (Windows)" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "Target Folder: $CheckpointsDir" -ForegroundColor Gray

if (-not (Test-Path $CheckpointsDir)) {
    New-Item -ItemType Directory -Force -Path $CheckpointsDir | Out-Null
}

# ------------------------------------------------------------------------------
# (a) Install Dependencies with CPU PyTorch Wheel
# ------------------------------------------------------------------------------
if (-not $SkipPip) {
    Write-Host "`n[Step 1/3] Installing CPU PyTorch, librosa, and OpenVoice..." -ForegroundColor Yellow

    Write-Host "Installing PyTorch (CPU wheel)..." -ForegroundColor Gray
    pip install --extra-index-url https://download.pytorch.org/whl/cpu torch

    Write-Host "Installing librosa..." -ForegroundColor Gray
    pip install librosa

    Write-Host "Installing openvoice (converter only, no-deps to bypass pyworld MSVC build)..." -ForegroundColor Gray
    pip install --no-deps git+https://github.com/myshell-ai/OpenVoice.git
} else {
    Write-Host "`n[Step 1/3] Skipping pip install (flag -SkipPip passed)." -ForegroundColor Gray
}

# ------------------------------------------------------------------------------
# (b) Download OpenVoice Checkpoints with Resume Support and Progress Line
# ------------------------------------------------------------------------------
Write-Host "`n[Step 2/3] Downloading OpenVoice V2 Checkpoints with Resume Support..." -ForegroundColor Yellow

function Download-WithResume {
    param(
        [string]$Url,
        [string]$DestinationPath,
        [int64]$ExpectedSize
    )

    $fileName = Split-Path -Leaf $DestinationPath
    $fileInfo = Get-Item -Path $DestinationPath -ErrorAction SilentlyContinue
    $existingBytes = 0

    if ($fileInfo) {
        $existingBytes = $fileInfo.Length
        if ($existingBytes -ge $ExpectedSize) {
            Write-Host "  [OK] $fileName already fully downloaded ($existingBytes bytes)." -ForegroundColor Green
            return
        }
        Write-Host "  Resuming $fileName from offset $existingBytes bytes..." -ForegroundColor Yellow
    }

    $req = [System.Net.HttpWebRequest]::Create($Url)
    $req.Timeout = 60000
    $req.UserAgent = "VoiceFlow-Cloning-Installer"
    if ($existingBytes -gt 0) {
        $req.AddRange($existingBytes)
    }

    try {
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        $webEx = $_.Exception
        if ($webEx.Response -and [int]$webEx.Response.StatusCode -eq 416) {
            Write-Host "  [OK] $fileName range satisfied (already complete)." -ForegroundColor Green
            return
        }
        throw $_
    }

    $totalBytes = $existingBytes + $resp.ContentLength
    $stream = $resp.GetResponseStream()
    $fileStream = [System.IO.File]::Open($DestinationPath, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write)

    $buffer = New-Object byte[] 65536
    $bytesRead = 0
    $downloadedSoFar = $existingBytes
    $lastReportTime = [DateTime]::MinValue

    try {
        while (($bytesRead = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $fileStream.Write($buffer, 0, $bytesRead)
            $downloadedSoFar += $bytesRead

            $now = [DateTime]::UtcNow
            if (($now - $lastReportTime).TotalMilliseconds -ge 500 -or $downloadedSoFar -eq $totalBytes) {
                $pct = if ($totalBytes -gt 0) { [math]::Round(($downloadedSoFar / $totalBytes) * 100, 1) } else { 0 }
                $currMb = [math]::Round($downloadedSoFar / 1MB, 2)
                $totalMb = [math]::Round($totalBytes / 1MB, 2)
                Write-Host "`r  Downloading $fileName : $currMb MB / $totalMb MB ($pct%)" -NoNewline
                $lastReportTime = $now
            }
        }
        Write-Host ""
    } finally {
        $fileStream.Flush()
        $fileStream.Close()
        $stream.Close()
        $resp.Close()
    }
}

# 1. config.json (~2.1 KB)
$ConfigUrl = "https://huggingface.co/myshell-ai/OpenVoiceV2/raw/main/converter/config.json"
$ConfigDest = Join-Path $CheckpointsDir "config.json"
Download-WithResume -Url $ConfigUrl -DestinationPath $ConfigDest -ExpectedSize 2084

# 2. checkpoint.pth (~131.5 MB)
$CkptUrl = "https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/main/converter/checkpoint.pth"
$CkptDest = Join-Path $CheckpointsDir "checkpoint.pth"
Download-WithResume -Url $CkptUrl -DestinationPath $CkptDest -ExpectedSize 137847381

# ------------------------------------------------------------------------------
# (c) Verification via doctor.py --cloning
# ------------------------------------------------------------------------------
Write-Host "`n[Step 3/3] Verifying Voice Cloning Installation via doctor.py --cloning..." -ForegroundColor Yellow
$DoctorScript = Join-Path $ScriptDir "doctor.py"
python $DoctorScript --cloning

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nVoice cloning prerequisites installed and verified successfully!" -ForegroundColor Green
} else {
    Write-Host "`nVoice cloning verification failed (Exit code $LASTEXITCODE). Review fix above." -ForegroundColor Red
    exit 1
}
