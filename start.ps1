param([switch]$Demo, [int]$Port = 8765, [switch]$AllowSleep)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $python)) {
    if ($uv) { & $uv.Source venv --python '>=3.10' .venv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { python -m venv .venv }
    else { throw 'Install Python 3.10+ or uv, then run start.ps1 again.' }
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
$extras = if ($Demo) { '.' } else { '.[windows]' }
if ($uv) { & $uv.Source pip install --python $python -e $extras }
else { & $python -m pip install -e $extras }
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
$bridgeArgs = @('--port', $Port)
if ($Demo) { $bridgeArgs += '--demo' }
if ($AllowSleep) { $bridgeArgs += '--allow-sleep' }
& $python -m remote_bridge.app @bridgeArgs
