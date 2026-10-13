# ai-meter

Monitor TUI local para Windows y Linux. Muestra uso de Codex/Claude, actividad reciente y metricas del sistema sin llamadas a modelos.

## Requisitos

- Python 3.11 o superior.
- Git.
- Acceso de lectura a los directorios locales de Codex y/o Claude:
  - Codex: `~/.codex`
  - Claude: `~/.claude`

No hace llamadas a modelos ni instala servicios o drivers. En Windows incluye
probes nativos de usuario para métricas rápidas; el probe opcional de AMD usa
un Ryzen Master Monitoring SDK que el usuario ya haya instalado.

El panel CODEX muestra tokens de cuenta de hoy, ayer y acumulados, separados de
la última muestra y la gráfica locales. Consulta `account/usage/read` con el
Codex instalado y su sesión existente, cada 15 minutos y al pulsar `r`.
Es una consulta de estadísticas en línea, sin API key de facturación ni llamadas
a modelos. Los datos ausentes se muestran como `unknown`; una consulta fallida
conserva el último dato marcado como desactualizado. `K` = miles, `M` = millones,
`B` = mil millones. No se guarda esta respuesta en disco.

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

La temperatura se normaliza en el registro de hardware. El fabricante del CPU
se detecta antes de cargar adaptadores: AMD puede usar el probe incluido
construido con Ryzen Master Monitoring SDK; Intel usa por ahora WMI/ACPI seguro.
GPU usa `nvidia-smi`, `amd-smi`/`rocm-smi` o `hwmon`. Si no hay una fuente
confiable y vigente, se muestra `unknown`.

## Uso

Comandos principales:

```powershell
ai-meter run        # TUI de monitoreo
ai-meter doctor     # diagnostico local y modo sin red
ai-meter paths      # rutas locales resueltas
ai-meter init       # crea config y base de datos si faltan
ai-meter export --format json   # exporta datos del sqlite local (json o csv)
```

En Windows tambien existen comandos de mantenimiento del driver legacy:
`ai-meter install-service` (deshabilitado) y `ai-meter uninstall-service`
(requiere Administrador).

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
| Codex tokens de cuenta | Codex App Server `account/usage/read` | Último dato en memoria, marcado desactualizado, o `unknown` |
| Claude plan | `~/.claude/.credentials.json` | `.claude/backups/*` |
| Claude tokens/eventos | `~/.claude/projects/**/*.jsonl` | `stats-cache.json` |
| Claude limites 5h/semanal | OAuth usage API opcional | `unknown` |
| CPU/RAM/disk/net | Windows: `ai-meter-winprobe.exe`; Linux: `psutil` | `psutil` |
| CPU cores | `psutil.cpu_times(percpu=True)` por delta | `unknown` |
| CPU temp | Windows AMD: probe incluido + Ryzen Master Monitoring SDK instalado; Windows Intel: WMI/ACPI; Linux: `psutil.sensors_temperatures()` / `hwmon` | `unknown` |
| GPU temp | NVIDIA: `nvidia-smi`; AMD: `amd-smi` en ruta ROCm acotada o `AI_METER_AMD_SMI` / `rocm-smi`; Linux: `hwmon` (`amdgpu`, `nouveau`) | `unknown` |

Las métricas de hardware llevan `id`, dispositivo, tipo, valor, unidad, fuente,
timestamp y caducidad. La TUI las renderiza mediante `HardwarePanelSpec`, por lo
que nuevas métricas no requieren modificar el collector de sistema.

La temperatura puede quedar en `unknown`. `ai-meter` no instala ningún SDK,
servicio ni driver kernel para leer sensores.

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
usage_api_enabled = true
usage_api_interval_s = 900
app_server_path = ""

[providers.claude]
enabled = true
usage_api_enabled = true
usage_api_interval_s = 900

[providers.hardware]
enabled = true
poll_interval_ms = 1000
source_priority = ["amd_ryzen_master", "nvml", "nvidia_smi", "amd_smi", "rocm_smi", "linux_hwmon", "wmi"]
amd_probe_path = ""

[app]
persist_history = false
```

Con el SDK de AMD ya instalado, el probe incluido se detecta automáticamente.
Para Codex se prefiere el ejecutable del escritorio instalado en Windows y se
usa el CLI del PATH como alternativa. `app_server_path` permite indicar un
ejecutable concreto. `usage_api_enabled = false` desactiva las estadísticas de
cuenta. Las APIs de estadísticas habilitadas son excepciones explícitas al
modo local (`privacy.network_disabled`); no habilitan llamadas a modelos.
En desarrollo usa siempre el Python del proyecto:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\.venv\Scripts\python.exe -m ai_meter.main doctor
```

La lectura del SDK requiere una PowerShell iniciada como administrador. La
variable `AI_METER_AMD_PROBE` sólo sirve para probar un binario alternativo.

## Desarrollo

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
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
