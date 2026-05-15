# Fuentes de Datos

## Codex

Fuente principal:

- `~/.codex/sessions/**/*.jsonl`
- Buscar `payload.rate_limits` en los JSONL recientes.
- Esta fuente se actualiza antes que `logs_2.sqlite` y evita mostrar planes stale.

Campos utiles:

- `payload.rate_limits.plan_type`
- `payload.rate_limits.primary.used_percent`
- `payload.rate_limits.primary.window_minutes`
- `payload.rate_limits.primary.resets_at`
- `payload.rate_limits.secondary.used_percent`
- `payload.rate_limits.secondary.window_minutes`
- `payload.rate_limits.secondary.resets_at`

Fallback:

- `~/.codex/logs_2.sqlite`
- target `codex_api::endpoint::responses_websocket`
- evento `codex.rate_limits`

Limitacion:

- Codex expone porcentaje y reset, no limite absoluto en tokens/unidades.
- `logs_2.sqlite` puede quedar atrasado despues de un cambio de plan.

## Claude

Plan:

- Fuente principal: `~/.claude/.credentials.json`
- Campo: `claudeAiOauth.subscriptionType`
- Fallback: backups `~/.claude/backups/.claude.json.backup.*`

Uso observado:

- `~/.claude/projects/**/*.jsonl`
- `~/.claude/stats-cache.json`

Limites 5h/semanal:

- Se obtienen con `TOKEN` en `.env`.
- Endpoint: `https://api.anthropic.com/api/oauth/usage`
- No consume tokens de modelo.
- Puede devolver `401` si el token ya no sirve.
- Puede devolver `429`; el collector aplica backoff con `Retry-After`.

## Sistema

Metricas rapidas:

- `src/ai_meter/bin/win-x64/ai-meter-winprobe.exe`
- Corre en stream con intervalo fijo actual de 250ms.
- Entrega CPU total, RAM, disk y red.

Cores:

- Se calculan con delta de `psutil.cpu_times(percpu=True)`.
- No depender de `psutil.cpu_percent(interval=0.0, percpu=True)` en hilos nuevos porque puede devolver 0.

Temperaturas:

- Preferir `C:\ProgramData\ai-meter\sensor.json`, escrito por tarea `ai-meter-sensor`.
- El probe nativo usa LibreHardwareMonitor.
- CPU temp puede requerir driver/kernel y permisos elevados.
- GPU temp suele funcionar sin admin cuando LHM puede leer sensor.

Fallbacks:

- `_run_sensor_probe()` como fallback si no hay archivo de servicio.
- WMI ACPI solo como fallback debil; puede reportar valores poco utiles en laptops.

