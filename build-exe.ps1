param()
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run start.ps1 once to create the development environment.' }
& $python -m pip --version *> $null
if ($LASTEXITCODE -eq 0) {
    & $python -m pip install -e '.[windows,desktop]'
} else {
    $uv = Get-Command uv -ErrorAction Stop
    & $uv.Source pip install --python $python -e '.[windows,desktop]'
}
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name PocketChatGPTBridge `
    --collect-data remote_bridge --collect-submodules uvicorn `
    --hidden-import comtypes.stream --hidden-import PIL._tkinter_finder desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dist\PocketChatGPTBridge.exe') -Destination $PSScriptRoot -Force
Write-Host "Built: $PSScriptRoot\PocketChatGPTBridge.exe"
