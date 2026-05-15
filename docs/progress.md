# Progress

## Estado Actual

- TUI Textual con layout de CPU history, CPU details, Claude, Codex, System y Live Activity.
- Barras y CPU history usan estilo braille/gradiente.
- Codex detecta plan y limites desde JSONL recientes; SQLite queda como fallback.
- Claude detecta plan desde `.claude/.credentials.json`; backups quedan como fallback.
- Claude API soporta `401` y backoff para `429`.
- Cores CPU refrescan con delta de `psutil.cpu_times(percpu=True)`.
- Timestamps de actividad se convierten a hora local.
- Historial SQLite es opcional con `persist_history=false` por defecto.

## Pendientes Conocidos

- `retention_days` aun no se aplica a SQLite.
- `winprobe` usa intervalo fijo de 250ms.
- `doctor` no reporta todavia fuente exacta de plan Codex/Claude.
- Pruebas actuales cubren DB, paths y redaccion; faltan tests de collectors.

