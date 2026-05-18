# Arquitectura

`ai-meter` separa UI, orquestacion y recoleccion de datos para mantener fluida la TUI.

## Flujo principal

1. `main.py` carga config y crea `MonitorEngine`.
2. `AiMeterTui` programa timer de render y workers persistentes para light collect, heavy collect y Claude API si esta habilitada.
3. `MonitorEngine` ejecuta collectors y normaliza resultados en `DashboardSnapshot`.
4. La TUI renderiza paneles con datos ya cacheados.

## Ciclos

- Render: `refresh_ms`, rango 100ms a 5000ms.
- Light collect: `collector_light_interval_ms`, por defecto 250ms, solo sistema rapido.
- Heavy collect: `collector_heavy_interval_s`, minimo 2s, por defecto 10s.
- Claude API: deshabilitada por defecto; si se habilita usa `providers.claude.usage_api_interval_s` y backoff para errores 429.
- Winprobe nativo: `winprobe_interval_ms`, por defecto 250ms.

La recoleccion no crea un thread por tick. Cada ciclo vive en un `PeriodicWorker` persistente.

## Render por secciones

La UI no reconstruye todos los paneles en cada tick:

- CPU: `ui.cpu_render_interval_ms`
- System: `ui.system_render_interval_ms`
- AI panels: `ui.ai_render_interval_ms`
- Events: `ui.events_render_interval_ms`

## Hot path

El hot path esta en:

- `AiMeterTui._render_snapshot`
- `PeriodicWorker` de light collect
- `MonitorEngine.run_light_collection`
- `SystemCollector.collect(include_temp=False, include_top=False)`
- `NativeWinProbeSampler.snapshot`
- `_refresh_core_loads_fast`

Evitar IO pesado, subprocess bloqueante o escaneo recursivo en ese camino.

## Estado en memoria

`MonitorEngine` mantiene:

- provider metadata
- eventos recientes
- usage rows por proveedor
- historial CPU de 600 muestras
- resultado cacheado de Claude API

Los collectors de Codex y Claude se crean solo si `providers.codex.enabled` y `providers.claude.enabled` estan activos.

Si `persist_history=false`, offsets y eventos viven solo en memoria.

## Persistencia

SQLite solo se usa si `config.app.persist_history=true`. La base vive en `user_data_dir("ai-meter")`.

Tablas:

- `providers`
- `projects`
- `sessions`
- `usage_samples`
- `events`
- `file_offsets`

`retention_days` existe en config, pero no hay limpieza automatica implementada actualmente.
