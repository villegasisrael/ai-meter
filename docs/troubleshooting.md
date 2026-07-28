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

En Windows no existe una API genérica y confiable para la temperatura de todos
los CPUs. `doctor` muestra `cpu vendor` y carga sólo el adaptador correspondiente.

Para AMD, diferencia estas rutas:

- `wmi:admin_required`: WMI no se intentó porque la sesión no está elevada.
- `amd=sdk_not_installed`: AMD Ryzen Master Monitoring SDK no está instalado.
- `amd=probe_not_built`: falta compilar `ai-meter-amd-probe.exe`.
- `amd=admin_required`: abrir PowerShell como administrador.
- `amd=driver_not_installed` o `driver_not_running`: el servicio oficial del SDK
  no está disponible; ai-meter no lo instala ni lo inicia.

Para Intel, ai-meter usa WMI/ACPI cuando el firmware entrega una lectura
confiable. En caso contrario muestra `intel=no_supported_provider`; no intenta
cargar Ryzen Master ni instala drivers Intel.

Con el SDK ya instalado:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\.venv\Scripts\python.exe -m ai_meter.main doctor
```

Si el ejecutable existe pero falla, revisar `hardware providers` en `doctor`.
No usar `python` a secas si apunta al Python de Inkscape. `ai-meter` no instala
el driver ni acepta la licencia del SDK por el usuario.

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

Si la TUI muestra `token expired, relogin in Claude Code`, el refresh automatico ya no funciona
(refresh token vencido). Pulsa `l` en la TUI para hacer el re-login OAuth dentro de la app: se abre
el navegador, autorizas, y pegas el codigo `code#state` que muestra Claude. Se reescribe
`~/.claude/.credentials.json` y el panel se reanuda solo. (Alternativa: `/login` en Claude Code.)

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
