# Performance

## Objetivo

La TUI debe seguir fluida sin bloquear render por IO, subprocess o parseo grande.

## Costos

- `winprobe`: solo Windows, `app.winprobe_interval_ms`, default 250ms.
- Linux/Ubuntu: sistema rapido via `psutil`.
- Light collect: `app.collector_light_interval_ms`, default 250ms.
- Heavy collect: `app.collector_heavy_interval_s`, default 10s.
- Hardware: `providers.hardware.poll_interval_ms`, mínimo/default 1000ms.
- Claude API: opt-in, `providers.claude.usage_api_interval_s`, default 900s.
- `cpu_freq()`: cache 2s.
- Top processes: cache 3s.
- Hardware: worker persistente separado; el render sólo consume su cache.
- Métricas AMD: caducidad 3s y máximo una consulta por segundo.
- Métricas de plataforma/subprocess: caducidad 15s.
- Windows WMI/ACPI no se intenta en sesiones no elevadas para evitar prompts repetidos de permisos.
- Deteccion de herramientas GPU cacheada; no hacer glob recursivo en `%ProgramFiles%`.

## Reglas

- No SQLite en light collect.
- No PowerShell desde render ni light collect.
- No parsear JSONL completo; usar offsets o tail limitado.
- Mantener `persist_history=false` por defecto.
- Mantener Claude API como opt-in.
- No instalar ni ejecutar drivers para temperatura; usar `unknown` si no hay fuente segura.
- No crear hilos por tick; usar workers persistentes.
- No ejecutar proveedores desde `render`.

## Config util

Fluidez:

```toml
[app]
refresh_interval_ms = 250
collector_light_interval_ms = 250
collector_heavy_interval_s = 10
winprobe_interval_ms = 250

[providers.hardware]
poll_interval_ms = 1000
```

Menor consumo:

```toml
[app]
refresh_interval_ms = 1000
collector_light_interval_ms = 500
collector_heavy_interval_s = 20
winprobe_interval_ms = 500

[providers.hardware]
poll_interval_ms = 2000
```
