# Arquitectura

`ai-meter` separa UI, orquestacion y recoleccion de datos para mantener fluida la TUI.

## Flujo principal

1. `main.py` carga config y crea `MonitorEngine`.
2. `AiMeterTui` programa timers de render, light collect, heavy collect y Claude API.
3. `MonitorEngine` ejecuta collectors y normaliza resultados en `DashboardSnapshot`.
4. La TUI renderiza paneles con datos ya cacheados.

## Ciclos

- Render: `refresh_ms`, rango 100ms a 5000ms.
- Light collect: 100ms a 1000ms, solo sistema rapido.
- Heavy collect: `collector_heavy_interval_s`, minimo 2s, por defecto 10s.
- Claude API: 60s con backoff para errores 429.

## Hot path

El hot path esta en:

- `AiMeterTui._render_snapshot`
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

