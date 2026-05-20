# ai-meter

Monitor TUI local para Windows y Linux. Muestra uso de Codex/Claude, actividad reciente y metricas del sistema sin llamadas a modelos.

## Uso

```powershell
$env:PYTHONPATH='src'
python -m ai_meter.main run
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

En Ubuntu/Linux:

```bash
PYTHONPATH=src python -m ai_meter.main run
PYTHONPATH=src python -m ai_meter.main doctor
```

Si esta instalado como entrypoint:

```powershell
ai-meter run
ai-meter doctor
ai-meter paths
```

## Datos

| Dato | Fuente principal | Fallback |
| --- | --- | --- |
| Codex plan/limites | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/*.jsonl` (`payload.rate_limits`) | `~/.codex/logs_2.sqlite` |
| Codex tokens/eventos | `~/.codex/sessions/**/*.jsonl`, `~/.codex/archived_sessions/*.jsonl` | `logs_2.sqlite` |
| Claude plan | `~/.claude/.credentials.json` | `.claude/backups/*` |
| Claude tokens/eventos | `~/.claude/projects/**/*.jsonl` | `stats-cache.json` |
| Claude limites 5h/semanal | OAuth usage API opcional | `unknown` |
| CPU/RAM/disk/net | Windows: `ai-meter-winprobe.exe`; Linux: `psutil` | `psutil` |
| CPU cores | `psutil.cpu_times(percpu=True)` por delta | `unknown` |
| CPU temp | Windows: WMI confiable si existe; Linux: `psutil.sensors_temperatures()` | `unknown` |
| GPU temp | No soportado por defecto | `unknown` |

La temperatura puede quedar en `unknown`. No se instala ningun driver kernel para leer sensores.

## Anti-Tampering / driver legacy

Versiones anteriores podian usar `ai-meter-sensor-probe` con LibreHardwareMonitor/WinRing0 para temperatura. Ese camino esta deshabilitado porque Windows/EDR puede bloquearlo como controlador vulnerable.

Si aparece un aviso sobre `ai-meter-sensor-probe.sys`, limpiar restos legacy como administrador:

```powershell
python -m ai_meter.main uninstall-service
Remove-Item "$env:ProgramData\ai-meter\sensor.json" -Force -ErrorAction SilentlyContinue
Remove-Item ".\src\ai_meter\bin\win-x64\ai-meter-sensor-probe.sys" -Force -ErrorAction SilentlyContinue
```

No desactivar Memory Integrity/HVCI para ai-meter.

## Configuracion

El archivo real se ve con:

```powershell
python -m ai_meter.main paths
```

Opciones habituales:

```toml
[providers.codex]
enabled = true

[providers.claude]
enabled = true
usage_api_enabled = false
usage_api_interval_s = 900

[app]
persist_history = false
```

## Build y pruebas

```powershell
.\scripts\build_native.ps1
.\scripts\build_exe.ps1
$env:PYTHONPATH='src'; python -m unittest discover -s tests
python -m compileall src\ai_meter
```

## Documentacion

- `AGENTS.md`: reglas del repo para agentes.
- `docs/architecture.md`: flujo interno y ciclos.
- `docs/data-sources.md`: fuentes y confiabilidad.
- `docs/operations.md`: comandos operativos.
- `docs/performance.md`: hot path y costos.
- `docs/troubleshooting.md`: diagnostico puntual.

## Privacidad

- No hay llamadas a modelos.
- Los archivos locales de Codex/Claude se leen en el equipo.
- Secrets se redactan antes de guardar o mostrar metadata.
- SQLite solo se usa si `persist_history=true`.

Licencia: Apache-2.0. Ver `LICENSE`.
