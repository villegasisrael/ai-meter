# Arquitectura

`ai-meter` separa UI, orquestacion y recoleccion para mantener fluida la TUI.

## Flujo

1. `main.py` carga config y crea `MonitorEngine`.
2. `AiMeterTui` crea workers persistentes para light collect, heavy collect y Claude API opcional.
3. `MonitorEngine` ejecuta collectors y normaliza en `DashboardSnapshot`.
4. La TUI renderiza datos cacheados.

## Ciclos

- Render: `app.refresh_interval_ms`, rango 100ms a 5000ms.
- Light collect: `app.collector_light_interval_ms`, por defecto 250ms, solo sistema rapido.
- Heavy collect: `app.collector_heavy_interval_s`, por defecto 10s.
- Claude API: deshabilitada por defecto; si se habilita usa `providers.claude.usage_api_interval_s`.
- Winprobe: solo Windows, `app.winprobe_interval_ms`, por defecto 250ms.
- Linux/Ubuntu: `SystemCollector` usa `psutil` para sistema rapido.

No crear hilos por tick. Usar `PeriodicWorker`.

## Hot path

- `AiMeterTui._render_snapshot`
- `MonitorEngine.run_light_collection`
- `SystemCollector.collect(include_temp=False, include_top=False)`
- `NativeWinProbeSampler.snapshot`
- `_refresh_core_loads_fast`

No agregar IO pesado, SQLite, PowerShell ni escaneos recursivos en ese camino.

## Persistencia

SQLite solo se usa si `config.app.persist_history=true`.

Tablas:

- `providers`
- `projects`
- `sessions`
- `usage_samples`
- `events`
- `file_offsets`

`retention_days` existe en config, pero la limpieza automatica aun no esta implementada.
