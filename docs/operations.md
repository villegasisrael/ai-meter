# Operacion

## Instalacion de desarrollo

```powershell
git clone <repo-url>
cd ai-meter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

## Ejecucion

```powershell
python -m ai_meter.main run
```

Si instalaste el entrypoint:

```powershell
ai-meter run
```

## Proveedores

Editar el config mostrado por `python -m ai_meter.main paths`:

```toml
[providers.codex]
enabled = true

[providers.claude]
enabled = true
usage_api_enabled = false
usage_api_interval_s = 900
```

Para mostrar solo Claude, poner `providers.codex.enabled=false`. Para mostrar solo Codex, poner `providers.claude.enabled=false`.

En la TUI tambien se puede ocultar/mostrar temporalmente cada panel sin cambiar config:
`c` alterna Claude y `x` alterna Codex.

## Performance

Config recomendada por defecto:

```toml
[app]
refresh_interval_ms = 1000
collector_light_interval_ms = 250
collector_heavy_interval_s = 10
winprobe_interval_ms = 250

[ui]
cpu_render_interval_ms = 250
system_render_interval_ms = 500
ai_render_interval_ms = 1000
events_render_interval_ms = 500
```

## Diagnostico

```powershell
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

`doctor` debe confirmar:

- rutas de config/data
- proveedores habilitados y estado de Claude usage API
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
