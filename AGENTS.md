# AGENTS.md

Instrucciones para agentes que trabajen en este repo.

## Estilo de respuesta

Responde en espanol, sobrio y concreto. Prioriza resultados, comandos utiles y referencias a archivos. Evita explicaciones largas salvo que el usuario las pida.

## Proyecto

`ai-meter` es un monitor TUI local para Windows. Muestra uso de Codex/Claude, eventos recientes y metricas del sistema con estilo tipo btop.

Principios:

- No hacer llamadas a modelos.
- Leer archivos locales de Codex/Claude cuando sea posible.
- Mostrar `unknown` cuando un dato no sea confiable.
- Mantener ligero el hot path.
- No guardar historial salvo que `persist_history=true`.
- No instalar drivers kernel para sensores.

## Comandos

Usar desde la raiz:

```powershell
python -m ai_meter.main run
python -m ai_meter.main doctor
python -m ai_meter.main paths
python -m ai_meter.main uninstall-service
python -m compileall src\ai_meter
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

Notas:

- Las pruebas existentes son `unittest`; la `.venv` local puede no tener `pytest`.
- Si se usa Python global, asegurar `PYTHONPATH=src`.
- `uninstall-service` solo existe para limpiar el sensor legacy y requiere PowerShell como administrador.

## Arquitectura rapida

- `src/ai_meter/main.py`: CLI Typer.
- `src/ai_meter/app.py`: `MonitorEngine`, estado compartido y orquestacion.
- `src/ai_meter/tui/app.py`: UI Textual y timers.
- `src/ai_meter/tui/widgets.py`: barras, graficas braille y helpers visuales.
- `src/ai_meter/collectors/system.py`: CPU/RAM/disk/net/cores y temperatura solo si hay fuente segura.
- `src/ai_meter/collectors/codex.py`: eventos, tokens y limites Codex.
- `src/ai_meter/collectors/claude.py`: eventos, tokens y metadata local Claude.
- `src/ai_meter/collectors/claude_api_usage.py`: limites Claude via OAuth usage API opcional.
- `src/ai_meter/storage/db.py`: SQLite opcional para historial/export.
- `native/winprobe/`: probe C++ de usuario para metricas rapidas Windows.

## Fuentes de datos

Codex:

- Plan y limites: `~/.codex/sessions/**/*.jsonl`, `payload.rate_limits`.
- Fallback: `~/.codex/logs_2.sqlite`.
- No leer ni mostrar secretos de `auth.json`.

Claude:

- Plan: `~/.claude/.credentials.json`, campo `claudeAiOauth.subscriptionType`.
- Fallback: `~/.claude/backups/.claude.json.backup.*`.
- Uso observado: `~/.claude/projects/**/*.jsonl` y `stats-cache.json`.
- Limites reales: API OAuth de Anthropic solo si esta habilitada y hay `TOKEN`.

Sistema:

- CPU/RAM/disk/net: `ai-meter-winprobe.exe --stream 250`.
- Cores: `psutil.cpu_times(percpu=True)` por delta.
- Temp/GPU: WMI confiable si existe; si no, `unknown`.
- Sensor legacy LibreHardwareMonitor/WinRing0: no instalar, no documentar como ruta soportada.

## Reglas de edicion

- No revertir cambios ajenos ni artefactos generados si no son parte de la tarea.
- No editar binarios ni `native/**/bin` / `native/**/obj` salvo que la tarea sea build nativo.
- Preferir cambios pequenos y verificables.
- Para UI, mantener estilo btop/braille y evitar layouts decorativos.
- Si se cambia un collector, validar con snippet directo, `doctor`, `unittest` y `compileall` cuando aplique.

## Documentacion

Mantener solo documentacion operativa necesaria:

- `README.md`
- `docs/architecture.md`
- `docs/data-sources.md`
- `docs/operations.md`
- `docs/performance.md`
- `docs/troubleshooting.md`

Actualizarla cuando cambien fuentes de datos, comandos, timers, servicio legacy o decisiones de performance.

## Imported Claude Cowork project instructions

ai-meter
