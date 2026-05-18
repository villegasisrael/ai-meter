# Progress

## Estado Actual

- TUI Textual con layout de CPU history, CPU details, Claude, Codex, System y Live Activity.
- Barras y CPU history usan estilo braille/gradiente.
- Codex detecta plan y limites desde JSONL recientes; SQLite queda como fallback.
- Claude detecta plan desde `.claude/.credentials.json`; backups quedan como fallback.
- Claude API es opt-in, soporta `401` y backoff para `429`.
- La TUI usa workers persistentes para collectors; no crea hilos por tick.
- Render usa throttling por secciones configurables.
- Cores CPU refrescan con delta de `psutil.cpu_times(percpu=True)`.
- Timestamps de actividad se convierten a hora local.
- Historial SQLite es opcional con `persist_history=false` por defecto.
- Licencia Apache-2.0 y archivos base para publicar el repo.

## Pendientes Conocidos

- `retention_days` aun no se aplica a SQLite.
- `doctor` no reporta todavia fuente exacta de plan Codex/Claude.
- Revisar limpieza de artefactos historicos ya trackeados antes del primer release publico.
