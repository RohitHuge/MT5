# install-service.ps1 — Register mt5-bridge as a Windows Service using NSSM
#
# Prerequisites:
#   1. Install NSSM: https://nssm.cc/download  or  choco install nssm
#   2. Run this script as Administrator
#   3. Ensure the Python environment with requirements.txt is installed
#
# Usage:
#   .\install-service.ps1
#   .\install-service.ps1 -Uninstall   # to remove the service
#
# After install:
#   Start-Service MT5Bridge
#   Get-Service  MT5Bridge    # check status
#   Stop-Service MT5Bridge

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# ── Configuration ────────────────────────────────────────────────────────────

$ServiceName    = "MT5Bridge"
$DisplayName    = "MT5 Bridge API"
$Description    = "FastAPI bridge exposing MT5 terminal over HTTP + WebSocket for the trading platform."

# Resolve paths relative to this script's directory
$ScriptDir      = Split-Path -Parent $MyInvocation.MyCommand.Definition
$MainPy         = Join-Path $ScriptDir "main.py"

# Python executable — uses the system Python by default.
# Change this if you're using a venv:
#   $PythonExe = "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
$PythonExe      = (Get-Command python).Source

$UvicornArgs    = "-m uvicorn main:app --host 0.0.0.0 --port 8000"

# ── Check prerequisites ──────────────────────────────────────────────────────

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Error "This script must be run as Administrator. Right-click PowerShell and choose 'Run as Administrator'."
    exit 1
}

$NssmExe = (Get-Command nssm -ErrorAction SilentlyContinue)?.Source
if (-not $NssmExe) {
    Write-Error @"
NSSM not found. Install it first:
  Option 1 (Chocolatey): choco install nssm
  Option 2 (manual):     Download nssm.exe from https://nssm.cc/download
                         and place it in C:\Windows\System32\ (or any PATH directory)
"@
    exit 1
}

# ── Uninstall path ───────────────────────────────────────────────────────────

if ($Uninstall) {
    Write-Host "Stopping and removing service '$ServiceName'..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm
    Write-Host "Service '$ServiceName' removed." -ForegroundColor Green
    exit 0
}

# ── Install ──────────────────────────────────────────────────────────────────

Write-Host "Installing '$ServiceName' as a Windows Service..." -ForegroundColor Cyan
Write-Host "  Python:  $PythonExe"
Write-Host "  Script:  $MainPy"
Write-Host "  WorkDir: $ScriptDir"

# Stop existing service if running
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Existing service found — stopping and reinstalling..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm 2>$null
}

# Register the service
& $NssmExe install $ServiceName $PythonExe $UvicornArgs

# Configure service properties
& $NssmExe set $ServiceName AppDirectory     $ScriptDir
& $NssmExe set $ServiceName DisplayName      $DisplayName
& $NssmExe set $ServiceName Description      $Description
& $NssmExe set $ServiceName Start            SERVICE_AUTO_START
& $NssmExe set $ServiceName AppRestartDelay  5000       # ms before restart on failure
& $NssmExe set $ServiceName AppThrottle      10000      # ms minimum between restarts

# Redirect stdout/stderr to log files
$LogDir = Join-Path $ScriptDir "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $LogDir "mt5-bridge.log")
& $NssmExe set $ServiceName AppStderr (Join-Path $LogDir "mt5-bridge-error.log")
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateBytes 10485760    # rotate at 10 MB

# Environment variables — read from .env file in the same directory
$EnvFile = Join-Path $ScriptDir ".env"
if (Test-Path $EnvFile) {
    # Pass .env contents to the service environment
    $envContent = Get-Content $EnvFile | Where-Object { $_ -match "^[A-Z]" -and $_ -notmatch "^#" }
    foreach ($line in $envContent) {
        $parts = $line -split "=", 2
        if ($parts.Length -eq 2) {
            & $NssmExe set $ServiceName AppEnvironmentExtra "$($parts[0])=$($parts[1])"
        }
    }
    Write-Host "  Loaded environment from $EnvFile"
} else {
    Write-Host "  No .env file found at $EnvFile — using system environment variables" -ForegroundColor Yellow
}

# Start the service
& $NssmExe start $ServiceName

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Service '$ServiceName' is RUNNING." -ForegroundColor Green
    Write-Host "  Logs: $LogDir"
    Write-Host "  Test: Invoke-RestMethod http://localhost:8000/health"
} else {
    Write-Host ""
    Write-Host "Service '$ServiceName' did not start. Check logs at $LogDir" -ForegroundColor Red
    exit 1
}
