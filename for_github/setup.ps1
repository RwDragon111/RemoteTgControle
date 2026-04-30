$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

function Get-BootstrapPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @("py", "-3")
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return @("python")
    }

    throw "Python 3 not found. Install Python 3.10+ and try again."
}

if (-not (Test-Path $pythonExe)) {
    $bootstrap = Get-BootstrapPython
    $command = @($bootstrap + @("-m", "venv", $venvPath))
    & $command[0] $command[1..($command.Length - 1)]
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")

$configPath = Join-Path $projectRoot "config.json"
$configExamplePath = Join-Path $projectRoot "config.example.json"
if (-not (Test-Path $configPath)) {
    Copy-Item $configExamplePath $configPath
}

$launchCatalogPath = Join-Path $projectRoot "launch_catalog.json"
$launchCatalogExamplePath = Join-Path $projectRoot "launch_catalog.example.json"
if ((Test-Path $launchCatalogExamplePath) -and (-not (Test-Path $launchCatalogPath))) {
    Copy-Item $launchCatalogExamplePath $launchCatalogPath
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "1. Fill in config.json"
Write-Host "2. Edit launch_catalog.json or open the launch editor"
Write-Host "3. Run start_bot.ps1"
