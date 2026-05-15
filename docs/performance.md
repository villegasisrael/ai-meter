# Performance

## Objetivo

La TUI debe seguir fluida a 100ms sin bloquear por IO, subprocess o parseo grande.

## Costos conocidos

- `winprobe` corre a 250ms fijo, independiente de `refresh_ms`.
- `light collect` puede correr a 100ms.
- `heavy collect` reescanea archivos Codex/Claude cada 20s y parsea lineas nuevas.
- Claude API corre cada 60s y aplica backoff en 429.
- `cpu_freq()` esta cacheado por 2s para no penalizar el hot path.
- Top processes esta cacheado por 3s.
- Sensor probe esta rate-limited a 10s.

## Mantener ligero

- No agregar lecturas recursivas al light collect.
- No abrir SQLite en el light collect.
- No llamar PowerShell desde el render ni el light collect.
- No parsear JSONL completo; usar offsets o tail limitado.
- Mantener `persist_history=false` por defecto.

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

- Hacer dinamico el intervalo de `winprobe` segun `refresh_ms`.
- Implementar retencion real usando `retention_days`.
- Considerar worker persistente para light/heavy collection en vez de crear thread por tick.
- Reducir `--collect-all` en PyInstaller si el EXE queda demasiado grande.

