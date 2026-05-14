param(
  [string]$Python = '.\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

& "$PSScriptRoot\build_native.ps1"

& $Python -m pip install pyinstaller
$nativeBin = Resolve-Path ".\src\ai_meter\bin\win-x64"
& $Python -m PyInstaller --onefile --name ai-meter --collect-all textual --collect-all rich --add-data "$nativeBin;ai_meter/bin/win-x64" -m ai_meter.main

Write-Host 'EXE generado en .\dist\ai-meter.exe'
