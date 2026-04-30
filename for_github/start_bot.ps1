$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcherPath = Join-Path $projectRoot "launch_hidden.vbs"

if (-not (Test-Path (Join-Path $projectRoot ".venv\Scripts\python.exe"))) {
    throw "Run setup.ps1 first"
}

if (-not (Test-Path $launcherPath)) {
    throw "launch_hidden.vbs not found"
}

Start-Process -FilePath "wscript.exe" -ArgumentList @($launcherPath) -WorkingDirectory $projectRoot -WindowStyle Hidden
Write-Host "Bot started in background. The tray icon should appear."
