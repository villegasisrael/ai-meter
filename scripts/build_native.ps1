$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'src\ai_meter\bin\win-x64'
New-Item -ItemType Directory -Force $out | Out-Null

# Kill any running sensor-probe instances so the dll isn't locked during copy
Stop-Process -Name 'ai-meter-sensor-probe' -Force -ErrorAction SilentlyContinue

Write-Host 'Building bundled LibreHardwareMonitor sensor probe...'
dotnet publish (Join-Path $root 'native\AiMeter.SensorProbe\AiMeter.SensorProbe.csproj') -c Release -r win-x64 --self-contained false -o $out

$gpp = Get-Command g++ -ErrorAction SilentlyContinue
if ($gpp) {
    Write-Host 'Building C++ Windows metrics probe...'
    try {
        & $gpp.Source -std=c++17 -O2 (Join-Path $root 'native\winprobe\ai_meter_winprobe.cpp') -o (Join-Path $out 'ai-meter-winprobe.exe') -liphlpapi
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "C++ winprobe build exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Warning "C++ winprobe build failed: $($_.Exception.Message)"
    }
} else {
    Write-Warning 'g++ not found; C++ winprobe was not rebuilt.'
}

# Unblock binaries so Windows security doesn't block subprocess execution
Get-ChildItem $out -Filter '*.exe' | ForEach-Object { Unblock-File $_.FullName }
Get-ChildItem $out -Filter '*.dll' | ForEach-Object { Unblock-File $_.FullName }

Write-Host "Native probes output: $out"
