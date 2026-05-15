# Troubleshooting

## Codex muestra plan viejo

Prioridad correcta:

1. `~/.codex/sessions/**/*.jsonl` con `payload.rate_limits.plan_type`
2. `~/.codex/logs_2.sqlite` como fallback

Si muestra `plus` estando en `pro`, revisar:

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

Si `source` es `logs_sqlite`, probablemente no hay JSONL reciente con `rate_limits`.

## Claude plan unknown

Revisar:

- `~/.claude/.credentials.json`
- `~/.claude/backups/.claude.json.backup.*`

El collector no debe imprimir tokens ni secretos.

## Claude API 401

El `.env` existe pero el token no es valido o expiro. Reemplazar `TOKEN`.

Formato:

```text
TOKEN = Bearer sk-ant-oat01--...
```

## Claude API 429

La API rate-limito la consulta. El collector respeta `Retry-After` o usa 300s por defecto.

La UI debe mostrar algo como:

```text
HTTP 429: retry in 123s
```

## CPU cores en 0

No usar `psutil.cpu_percent(interval=0.0, percpu=True)` como fuente principal de cores en hilos nuevos.

La implementacion correcta usa delta de:

```python
psutil.cpu_times(percpu=True)
```

## Hora de eventos incorrecta

Los timestamps de eventos suelen venir en UTC. La UI debe convertirlos a hora local antes de renderizar.

## CPU temp unknown

Ejecutar:

```powershell
python -m ai_meter.main doctor
```

Casos comunes:

- `needs_admin`: falta instalar servicio.
- `from_service=false`: no hay `C:\ProgramData\ai-meter\sensor.json` vigente.
- `INVALID_IMAGE_HASH`: Memory Integrity/HVCI puede bloquear el driver.
- `wmi:acpi`: fallback, no siempre confiable.

## UI sin colores truecolor

`main.run()` define:

```text
TEXTUAL_COLOR_SYSTEM=truecolor
```

Si se ejecuta la TUI de otra forma, puede faltar color truecolor.
