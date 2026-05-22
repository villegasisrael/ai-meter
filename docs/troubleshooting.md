# Troubleshooting

## Diagnostico base

```powershell
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

## Anti-Tampering / `ai-meter-sensor-probe.sys`

Ese archivo viene del sensor legacy LibreHardwareMonitor/WinRing0 usado por versiones anteriores para temperatura. Ya no es una ruta soportada.

Limpiar como administrador:

```powershell
python -m ai_meter.main uninstall-service
Remove-Item "$env:ProgramData\ai-meter\sensor.json" -Force -ErrorAction SilentlyContinue
Remove-Item ".\src\ai_meter\bin\win-x64\ai-meter-sensor-probe.sys" -Force -ErrorAction SilentlyContinue
```

No desactivar Memory Integrity/HVCI para ai-meter. La temperatura debe caer a `unknown` si no hay fuente segura.

## Temperatura CPU/GPU unknown

En Windows, muchas CPUs AMD Ryzen no publican temperatura por una API de usuario estandar. Si WMI/ACPI no expone el dato, `ai-meter` debe mostrar `unknown`.

Validar GPU:

```powershell
nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader,nounits
amd-smi monitor --temperature
```

En Linux:

```bash
python - <<'PY'
import psutil
print(psutil.sensors_temperatures(fahrenheit=False))
PY
find /sys/class/hwmon -maxdepth 2 -type f \( -name name -o -name 'temp*_input' -o -name 'temp*_label' \) -print
rocm-smi --showtemp --json
```

## Codex muestra plan viejo

Prioridad:

1. `~/.codex/sessions/**/*.jsonl` con `payload.rate_limits.plan_type`
2. `~/.codex/logs_2.sqlite`

Snippet:

```powershell
$env:PYTHONPATH='src'
@'
from ai_meter.paths import resolve_app_paths
from ai_meter.runtime_store import MemoryOffsetStore
from ai_meter.collectors.codex import CodexCollector
paths = resolve_app_paths('ai-meter')
rl = CodexCollector(paths, MemoryOffsetStore()).collect().provider_status.metadata.get('rate_limits', {})
print(rl)
'@ | python -
```

## Claude unknown

Revisar:

- `~/.claude/.credentials.json`
- `~/.claude/backups/.claude.json.backup.*`
- `~/.claude/projects/**/*.jsonl`
- `~/.claude/stats-cache.json`

## Claude API 401/429

- `401`: token OAuth invalido o expirado.
- `429`: rate limit; el collector aplica backoff.

Para no depender de esa API:

```toml
[providers.claude]
usage_api_enabled = false
```

## CPU cores en 0

La fuente correcta es delta de:

```python
psutil.cpu_times(percpu=True)
```

No usar `psutil.cpu_percent(interval=0.0, percpu=True)` como fuente principal en hilos nuevos.

## Hora de eventos incorrecta

Los timestamps suelen venir en UTC. La UI debe convertirlos a hora local antes de renderizar.
