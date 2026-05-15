# ai-meter

Monitor TUI local para Windows con uso de Codex/Claude y metricas del sistema en tiempo real.

El objetivo es mostrar datos reales sin llamadas a modelos: plan, limites visibles, tokens observados, actividad local, CPU/RAM/disk/net, cores, GPU y temperatura cuando el equipo lo permite.

## Uso Rapido

```powershell
pip install -e "c:\xampp\htdocs\clawdex"
python -m ai_meter.main run
```

Diagnostico:

```powershell
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

## Que Muestra

- Codex: plan actual, uso 5h/semanal, reset y tokens observados.
- Claude: plan local, tokens observados y limites 5h/semanal si hay token OAuth.
- System: CPU history, CPU total, cores, RAM, disk, red, temperatura CPU/GPU y procesos.
- Live Activity: eventos recientes de Codex/Claude con hora local.

## Fuentes Principales

| Dato | Fuente principal | Fallback |
| --- | --- | --- |
| Codex plan/limites | `~/.codex/sessions/**/*.jsonl` (`payload.rate_limits`) | `~/.codex/logs_2.sqlite` |
| Codex tokens/eventos | `~/.codex/sessions/**/*.jsonl` | `logs_2.sqlite` |
| Claude plan | `~/.claude/.credentials.json` | `.claude/backups/*` |
| Claude tokens/eventos | `~/.claude/projects/**/*.jsonl` | `stats-cache.json` |
| Claude limites 5h/semanal | OAuth usage API con `TOKEN` en `.env` | `unknown` |
| CPU/RAM/disk/net | `ai-meter-winprobe.exe` | `psutil` |
| CPU cores | `psutil.cpu_times(percpu=True)` por delta | sensor probe |
| CPU/GPU temp | `C:\ProgramData\ai-meter\sensor.json` | probe directo / WMI |

Codex no expone el limite absoluto en tokens/unidades; expone porcentaje usado, ventana y reset. Por eso la UI muestra `% usado` y `reset`, no un numero absoluto inventado.

## Temperatura CPU

La temperatura CPU puede requerir acceso a driver/kernel. Instalar la tarea de fondo una vez desde PowerShell como administrador:

```powershell
cd c:\xampp\htdocs\clawdex
python -m ai_meter.main install-service
```

Esto registra `ai-meter-sensor` y escribe:

```text
C:\ProgramData\ai-meter\sensor.json
```

Desinstalar:

```powershell
python -m ai_meter.main uninstall-service
```

## Claude API

Para limites Claude 5h/semanal, crear `.env` en la raiz:

```text
TOKEN = Bearer sk-ant-oat01--TU_TOKEN_AQUI
```

La consulta va a `https://api.anthropic.com/api/oauth/usage`. No consume tokens de modelo. Si responde `401`, el token expiro o no sirve. Si responde `429`, la app aplica backoff.

## Controles

| Tecla | Accion |
| --- | --- |
| `+` / `=` | Refresh mas lento |
| `-` | Refresh mas rapido |
| `r` | Actualizar ahora |
| `p` | Pausar / reanudar |
| `h` | Ayuda |
| `q` | Salir |

Rango de refresh: 100ms a 5000ms.

## Build

Binarios nativos:

```powershell
.\scripts\build_native.ps1
```

EXE:

```powershell
.\scripts\build_exe.ps1
```

## Pruebas

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python -m compileall src\ai_meter
```

## Documentacion

- `AGENTS.md`: instrucciones para agentes.
- `docs/architecture.md`: arquitectura y ciclos.
- `docs/data-sources.md`: fuentes de datos y confiabilidad.
- `docs/operations.md`: comandos operativos.
- `docs/performance.md`: hot path y costos.
- `docs/troubleshooting.md`: problemas comunes.
- `docs/progress.md`: estado actual y pendientes.

## Privacidad

- `model-calls=off` siempre.
- Los archivos locales de Codex/Claude se leen en el equipo.
- Secrets se redactan antes de guardar/mostrar metadata.
- La API Claude solo recibe el token OAuth cuando `TOKEN` existe.

