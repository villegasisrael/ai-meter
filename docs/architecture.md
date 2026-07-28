# Arquitectura

`ai-meter` separa UI, orquestacion y recoleccion para mantener fluida la TUI.

## Flujo

1. `main.py` carga config y crea `MonitorEngine`.
2. `AiMeterTui` crea workers persistentes para light collect, heavy collect y Claude API opcional.
3. `HardwareRegistry` consulta proveedores, selecciona fuentes y descarta
   métricas vencidas.
4. `MonitorEngine` normaliza collectors y hardware en `DashboardSnapshot`.
5. La TUI renderiza datos cacheados.

## Ciclos

- Render: `app.refresh_interval_ms`, rango 100ms a 5000ms.
- Light collect: `app.collector_light_interval_ms`, por defecto 250ms, solo sistema rapido.
- Heavy collect: `app.collector_heavy_interval_s`, por defecto 10s.
- Hardware: `providers.hardware.poll_interval_ms`, mínimo y default 1000ms.
- Claude API: deshabilitada por defecto; si se habilita usa `providers.claude.usage_api_interval_s`.
- Winprobe: solo Windows, `app.winprobe_interval_ms`, por defecto 250ms.
- Linux/Ubuntu: `SystemCollector` usa `psutil` para sistema rapido.
- Hardware: worker dedicado; usa el probe AMD incluido (si el SDK oficial ya
  está instalado) y fuentes seguras de plataforma como WMI/ACPI, `nvidia-smi`,
  `amd-smi`, `rocm-smi` o `hwmon`.
- Identidad CPU: se detecta una vez antes de construir proveedores. Sólo se
  registra el adaptador AMD para `AuthenticAMD`; Intel y fabricantes
  desconocidos no cargan binarios de Ryzen Master.

No crear hilos por tick. Usar `PeriodicWorker`.

## Hot path

- `AiMeterTui._render_snapshot`
- `MonitorEngine.run_light_collection`
- `SystemCollector.collect(include_temp=False, include_top=False)`
- `NativeWinProbeSampler.snapshot`
- `_refresh_core_loads_fast`

No agregar IO pesado, SQLite, PowerShell ni escaneos recursivos en ese camino.

## Hardware extensible

- `hardware/models.py`: contrato normalizado `HardwareMetric`.
- `hardware/cpu_identity.py`: detección `amd`, `intel` o `unknown`.
- `hardware/factory.py`: carga condicional de proveedores por fabricante.
- `hardware/providers/`: adaptadores de AMD y plataforma.
- `hardware/registry.py`: prioridad de fuentes, cache y caducidad.
- `tui/hardware.py`: `HardwarePanelSpec` y render genérico por tipo de métrica.

Los proveedores sólo publican observaciones. Los controles que escriban en
hardware no forman parte de este contrato y deben implementarse en una capa
separada, deshabilitada por defecto.

## Persistencia

SQLite solo se usa si `config.app.persist_history=true`.

Tablas:

- `providers`
- `projects`
- `sessions`
- `usage_samples`
- `events`
- `file_offsets`

`retention_days` controla la retencion. `Database.purge_old(retention_days)` se
ejecuta al arrancar `ai-meter run` (cuando `persist_history=true`) y borra
`events`/`usage_samples` anteriores al corte, mas sesiones y proyectos huerfanos.
Tambien puede ejecutarse manualmente con `ai-meter prune [--days N]`.
Un valor `<= 0` desactiva la limpieza.

## Alertas de uso

`app.alert_threshold_pct` (por defecto 80) y `app.alert_enabled` controlan las
alertas: cuando un limite de Claude (API OAuth) o Codex cruza el umbral, el engine
emite un evento `usage_alert` (severidad `warning`) una sola vez por cruce y se
re-arma cuando el uso baja del umbral.

## Datos persistentes fuera de SQLite

El ultimo resultado correcto de la API de uso de Claude se cachea en
`<data_dir>/claude_usage_cache.json`. Al reiniciar (o si el token OAuth expira) la
TUI muestra ese ultimo valor conocido con su antiguedad en vez de `unknown`.
