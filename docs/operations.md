# Operacion

## Instalacion de desarrollo

```powershell
pip install -e "c:\xampp\htdocs\clawdex"
```

## Ejecucion

```powershell
python -m ai_meter.main run
```

## Diagnostico

```powershell
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

`doctor` debe confirmar:

- rutas de config/data
- homes de Codex/Claude
- fuente de metricas sistema
- estado del sensor probe
- temperatura CPU/GPU si existe
- cores detectados

## Servicio de temperatura

Instalar como administrador:

```powershell
python -m ai_meter.main install-service
```

Esto registra tarea `ai-meter-sensor` al logon del usuario actual con RunLevel Highest y escribe:

```text
C:\ProgramData\ai-meter\sensor.json
```

Desinstalar como administrador:

```powershell
python -m ai_meter.main uninstall-service
```

## Build nativo

```powershell
.\scripts\build_native.ps1
```

Requiere:

- .NET 6 SDK
- g++/MinGW si se quiere recompilar `ai-meter-winprobe.exe`

El output final que usa Python esta en:

```text
src\ai_meter\bin\win-x64
```

## Build EXE

```powershell
.\scripts\build_exe.ps1
```

Usa PyInstaller y agrega binarios nativos. Revisar tamano final si se modifica `--collect-all`.

## Pruebas

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python -m compileall src\ai_meter
```

Si se usa `.venv\Scripts\python.exe`, instalar dependencias de test si faltan.

