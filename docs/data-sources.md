# Fuentes de datos

## Codex

Principal:

- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/*.jsonl`
- Campo: `payload.rate_limits` o `payload.info`/`payload.rate_limits` en eventos `token_count`
- Ventanas: se normalizan por duracion (`300` min = 5h, `10080` min = semanal)

Fallback:

- `~/.codex/logs_2.sqlite`
- Evento `codex.rate_limits`

Limitacion: Codex expone porcentaje usado y reset, no un limite absoluto en tokens/unidades.

## Claude

Plan:

- `~/.claude/.credentials.json`
- Campo: `claudeAiOauth.subscriptionType`
- Fallback: `~/.claude/backups/.claude.json.backup.*`

Uso observado:

- `~/.claude/projects/**/*.jsonl`
- `~/.claude/stats-cache.json`

Limites reales:

- Opcional via `https://api.anthropic.com/api/oauth/usage`.
- Requiere `providers.claude.usage_api_enabled=true` y `TOKEN` en `.env`.
- Soporta ventanas `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_cowork`,
  `seven_day_design`, `seven_day_routines` y `extra_usage` cuando la API las entrega.
- Si no esta habilitado o falla, mostrar `unknown`.

## Sistema

Metricas rapidas:

- Windows: `src/ai_meter/bin/win-x64/ai-meter-winprobe.exe`.
- Linux/Ubuntu: `psutil`.
- Entrega CPU total, RAM, disk y red.
- Fallback Windows: `psutil`.

Cores:

- Delta de `psutil.cpu_times(percpu=True)`.

Temperatura:

- Windows: WMI confiable si ya existe en el sistema.
- Windows: WMI ACPI puede usarse como fallback debil.
- Linux/Ubuntu: `psutil.sensors_temperatures()` si el kernel expone sensores (`coretemp`, `k10temp`, etc.).
- Si no hay fuente confiable, mostrar `unknown`.

## Driver legacy

No instalar ni usar LibreHardwareMonitor/WinRing0. Versiones anteriores podian generar `ai-meter-sensor-probe.sys`; si aparece Anti-Tampering, ejecutar la limpieza de `docs/troubleshooting.md`.
