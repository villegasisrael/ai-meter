# Fuentes de datos

## Codex

Principal:

- `~/.codex/sessions/**/*.jsonl`
- Campo: `payload.rate_limits`

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
- Si no esta habilitado o falla, mostrar `unknown`.

## Sistema

Metricas rapidas:

- `src/ai_meter/bin/win-x64/ai-meter-winprobe.exe`
- Entrega CPU total, RAM, disk y red.
- Fallback: `psutil`.

Cores:

- Delta de `psutil.cpu_times(percpu=True)`.

Temperatura:

- Solo WMI confiable si ya existe en el sistema.
- WMI ACPI puede usarse como fallback debil.
- Si no hay fuente confiable, mostrar `unknown`.

## Driver legacy

No instalar ni usar LibreHardwareMonitor/WinRing0. Versiones anteriores podian generar `ai-meter-sensor-probe.sys`; si aparece Anti-Tampering, ejecutar la limpieza de `docs/troubleshooting.md`.
