# ai-meter

Monitor TUI local para Windows y Linux. Muestra uso de Codex/Claude, actividad reciente y metricas del sistema sin llamadas a modelos.

## Requisitos

- Python 3.11 o superior.
- Git.
- Acceso de lectura a los directorios locales de Codex y/o Claude:
  - Codex: `~/.codex`
  - Claude: `~/.claude`

No requiere servicios, drivers ni llamadas a modelos. En Windows incluye un probe nativo de usuario para metricas rapidas; en Linux usa `psutil`.

## Instalacion en Windows

Desde PowerShell:

```powershell
git clone <repo-url> ai-meter
cd ai-meter

py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e .

ai-meter doctor
ai-meter run
```

Si PowerShell bloquea la activacion del entorno:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## Instalacion en Ubuntu/Linux

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

git clone <repo-url> ai-meter
cd ai-meter

python3 --version
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .

ai-meter doctor
ai-meter run
```

La version de Python debe ser 3.11 o superior. En Ubuntu 24.04+ `python3` normalmente cumple; en versiones anteriores puede requerir instalar Python 3.11+ por separado.

La temperatura solo aparece si el sistema ya expone sensores seguros: CPU via WMI/ACPI o kernel Linux, GPU via `nvidia-smi`, `amd-smi`/`rocm-smi` o `hwmon`. Si no hay fuente confiable, se muestra `unknown`.

## Uso

Comandos principales:

```powershell
ai-meter run
ai-meter doctor
ai-meter paths
```

Tambien puede ejecutarse sin instalar el entrypoint, desde la raiz del repo:

Windows:

```powershell
$env:PYTHONPATH='src'
python -m ai_meter.main run
```

Linux:

```bash
PYTHONPATH=src python -m ai_meter.main run
```

## Actualizacion

Desde la raiz del repositorio:

```powershell
git pull
python -m pip install -e .
```

## Desinstalacion

```powershell
python -m pip uninstall ai-meter
```

Si existe una instalacion legacy del sensor en Windows, limpiar como administrador:

```powershell
python -m ai_meter.main uninstall-service
Remove-Item "$env:ProgramData\ai-meter\sensor.json" -Force -ErrorAction SilentlyContinue
Remove-Item ".\src\ai_meter\bin\win-x64\ai-meter-sensor-probe.sys" -Force -ErrorAction SilentlyContinue
```

## Datos

| Dato | Fuente principal | Fallback |
| --- | --- | --- |
| Codex plan/limites | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/*.jsonl` (`payload.rate_limits`) | `~/.codex/logs_2.sqlite` |
| Codex tokens/eventos | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/*.jsonl` | `logs_2.sqlite` |
| Claude plan | `~/.claude/.credentials.json` | `.claude/backups/*` |
| Claude tokens/eventos | `~/.claude/projects/**/*.jsonl` | `stats-cache.json` |
| Claude limites 5h/semanal | OAuth usage API opcional | `unknown` |
| CPU/RAM/disk/net | Windows: `ai-meter-winprobe.exe`; Linux: `psutil` | `psutil` |
| CPU cores | `psutil.cpu_times(percpu=True)` por delta | `unknown` |
| CPU temp | Windows: WMI/ACPI si el sistema lo expone; Linux: `psutil.sensors_temperatures()` / `hwmon` (`k10temp`, `coretemp`) | `unknown` |
| GPU temp | NVIDIA: `nvidia-smi`; AMD: `amd-smi`/`rocm-smi`; Linux: `hwmon` (`amdgpu`, `nouveau`) | `unknown` |

La temperatura puede quedar en `unknown`. No se instala ningun driver kernel para leer sensores.

## Anti-Tampering / driver legacy

Versiones anteriores podian usar `ai-meter-sensor-probe` con LibreHardwareMonitor/WinRing0 para temperatura. Ese camino esta deshabilitado porque Windows/EDR puede bloquearlo como controlador vulnerable.

No desactivar Memory Integrity/HVCI para ai-meter.

## Configuracion

El archivo real se ve con:

```powershell
python -m ai_meter.main paths
```

Opciones habituales:

```toml
[providers.codex]
enabled = true

[providers.claude]
enabled = true
usage_api_enabled = false
usage_api_interval_s = 900

[app]
persist_history = false
```

## Desarrollo

```powershell
.\scripts\build_native.ps1
.\scripts\build_exe.ps1
$env:PYTHONPATH='src'; python -m unittest discover -s tests
python -m compileall src\ai_meter
```

En Linux:

```bash
PYTHONPATH=src python -m unittest discover -s tests
python -m compileall src/ai_meter
```

## Documentacion

- `AGENTS.md`: reglas del repo para agentes.
- `docs/architecture.md`: flujo interno y ciclos.
- `docs/data-sources.md`: fuentes y confiabilidad.
- `docs/operations.md`: comandos operativos.
- `docs/performance.md`: hot path y costos.
- `docs/troubleshooting.md`: diagnostico puntual.

## Privacidad

- No hay llamadas a modelos.
- Los archivos locales de Codex/Claude se leen en el equipo.
- Secrets se redactan antes de guardar o mostrar metadata.
- SQLite solo se usa si `persist_history=true`.

Licencia: Apache-2.0. Ver `LICENSE`.
