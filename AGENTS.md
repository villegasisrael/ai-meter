# AGENTS.md

Instrucciones para agentes que trabajen en este repo.

## Estilo de respuesta

Responde en espanol, sobrio y concreto. Prioriza resultados, comandos utiles y referencias a archivos. Evita explicaciones largas salvo que el usuario las pida.

## Proyecto

`ai-meter` es un monitor TUI local para Windows. Muestra uso de Codex/Claude, eventos recientes y metricas del sistema con estilo tipo btop.

Principios del proyecto:

- No hacer llamadas a modelos.
- Leer archivos locales de Codex/Claude cuando sea posible.
- Mostrar `unknown` cuando un dato no sea confiable.
- Mantener el hot path ligero: render y metricas rapidas no deben depender de collectors pesados.
- No guardar historial salvo que `persist_history=true`.

## Comandos

Usar estos comandos desde la raiz:

```powershell
python -m ai_meter.main run
python -m ai_meter.main doctor
python -m ai_meter.main paths
python -m ai_meter.main install-service
python -m ai_meter.main uninstall-service
python -m compileall src\ai_meter
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

Notas:

- La `.venv` local puede no tener `pytest`; las pruebas existentes son `unittest`.
- Si se usa el Python global, asegurar `PYTHONPATH=src`.
- `install-service` y `uninstall-service` requieren PowerShell como administrador.

## Arquitectura Rapida

- `src/ai_meter/main.py`: CLI Typer, comandos operativos.
- `src/ai_meter/app.py`: `MonitorEngine`, estado compartido y orquestacion de collectors.
- `src/ai_meter/tui/app.py`: UI Textual y timers.
- `src/ai_meter/tui/widgets.py`: render de barras, graficas braille y helpers visuales.
- `src/ai_meter/collectors/system.py`: CPU/RAM/disk/net/temp/GPU/cores.
- `src/ai_meter/collectors/codex.py`: eventos, tokens y limites Codex.
- `src/ai_meter/collectors/claude.py`: eventos, tokens y metadata local Claude.
- `src/ai_meter/collectors/claude_api_usage.py`: limites Claude via OAuth usage API, con backoff.
- `src/ai_meter/storage/db.py`: SQLite opcional para historial/export.
- `native/winprobe/`: probe C++ para metricas rapidas Windows.
- `native/AiMeter.SensorProbe/`: probe .NET/LibreHardwareMonitor para temp/GPU/cores.

## Fuentes de Datos

Codex:

- Plan y limites actuales: preferir `~/.codex/sessions/**/*.jsonl`, `payload.rate_limits`.
- Fallback: `~/.codex/logs_2.sqlite`, eventos `codex.rate_limits`; puede quedar stale.
- Uso/tokens: JSONL de sesiones y fallback SQLite.
- No leer ni mostrar secretos de `auth.json`.

Claude:

- Plan actual: `~/.claude/.credentials.json`, campo `claudeAiOauth.subscriptionType`.
- Fallback: `~/.claude/backups/.claude.json.backup.*`.
- Uso observado: `~/.claude/projects/**/*.jsonl` y `stats-cache.json`.
- Limites 5h/semanal reales: API OAuth de Anthropic si hay `TOKEN` en `.env`; puede devolver 401/429.

Sistema:

- CPU/RAM/disk/net: `ai-meter-winprobe.exe --stream 250`.
- Cores rapidos: `psutil.cpu_times(percpu=True)` por delta.
- Temp/GPU: `ai-meter-sensor-probe.exe`, preferentemente desde `C:\ProgramData\ai-meter\sensor.json`.
- WMI ACPI es fallback de baja confianza; usarlo solo como diagnostico si no hay mejor fuente.

## Reglas de Edicion

- No revertir cambios ajenos ni artefactos generados si no son parte de la tarea.
- No editar binarios ni `native/**/bin` / `native/**/obj` salvo que la tarea sea build nativo.
- Preferir cambios pequenos y verificables.
- Para UI, mantener estilo btop/braille y evitar layouts decorativos innecesarios.
- Si se cambia un collector, validar con un snippet directo del collector y `doctor`.

## Documentacion

Antes de cambios grandes, leer:

- `docs/architecture.md`
- `docs/data-sources.md`
- `docs/operations.md`
- `docs/performance.md`
- `docs/troubleshooting.md`

Actualizar esos documentos cuando cambien fuentes de datos, comandos, timers, servicio nativo o decisiones de performance.

