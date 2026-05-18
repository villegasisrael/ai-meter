# Performance

## Objetivo

La TUI debe seguir fluida a 100ms sin bloquear por IO, subprocess o parseo grande.

## Costos conocidos

- `winprobe` corre con `app.winprobe_interval_ms` (default 250ms).
- `light collect` corre con `app.collector_light_interval_ms` (default 250ms).
- `heavy collect` reescanea archivos Codex/Claude con `app.collector_heavy_interval_s` (default 10s) y parsea lineas nuevas.
- Claude API esta deshabilitada por defecto. Si se habilita, corre cada `providers.claude.usage_api_interval_s` (default 900s) y aplica backoff en 429.
- Render usa throttling por seccion; no todos los paneles se recalculan en cada frame.
- `cpu_freq()` esta cacheado por 2s para no penalizar el hot path.
- Top processes esta cacheado por 3s.
- Sensor probe esta rate-limited a 10s.

## Mantener ligero

- No agregar lecturas recursivas al light collect.
- No abrir SQLite en el light collect.
- No llamar PowerShell desde el render ni el light collect.
- No parsear JSONL completo; usar offsets o tail limitado.
- Mantener `persist_history=false` por defecto.
- Mantener Claude API como opt-in; los datos locales deben seguir funcionando aunque la API este apagada o rate-limitada.
- No volver a crear hilos por tick; usar workers persistentes.

## Ajuste recomendado

Para maxima fluidez:

```toml
[app]
refresh_interval_ms = 250
collector_light_interval_ms = 250
collector_heavy_interval_s = 10
winprobe_interval_ms = 250

[ui]
cpu_render_interval_ms = 250
system_render_interval_ms = 500
ai_render_interval_ms = 1000
events_render_interval_ms = 500
```

Para menor consumo:

```toml
[app]
refresh_interval_ms = 1000
collector_light_interval_ms = 500
collector_heavy_interval_s = 20
winprobe_interval_ms = 500

[ui]
cpu_render_interval_ms = 500
system_render_interval_ms = 1000
ai_render_interval_ms = 2000
events_render_interval_ms = 1000
```

## Tamano en disco

Fuentes grandes habituales:

- `.venv`
- `.git`
- `native/**/bin`
- `native/**/obj`
- exportes CSV/JSON
- `src/ai_meter/bin/win-x64`

No limpiar esos archivos sin pedido explicito. Para empaquetado final, revisar que solo se distribuya lo necesario.

## Mejora pendiente recomendada

- Implementar retencion real usando `retention_days`.
- Reducir `--collect-all` en PyInstaller si el EXE queda demasiado grande.
