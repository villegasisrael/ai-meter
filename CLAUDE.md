# CLAUDE.md — ai-meter

Monitor TUI Windows estilo btop con tracking de uso de IA (Claude/Codex). Todo local, sin llamadas a modelos.

## Comandos rápidos

```powershell
python -m ai_meter.main run             # TUI
python -m ai_meter.main doctor          # diagnóstico
python -m ai_meter.main install-service # instalar tarea de temp (admin, una vez)
.\scripts\build_native.ps1              # compilar C# y C++
# Después de compilar, siempre desbloquear:
Unblock-File src\ai_meter\bin\win-x64\*.exe
```

## Arquitectura de archivos

```
src/ai_meter/
├── main.py                      # CLI: run, doctor, install-service, uninstall-service, paths, export
├── app.py                       # MonitorEngine + DashboardSnapshot
├── config.py                    # AppConfig pydantic + serializer TOML propio
├── models.py                    # ProviderStatus, EventRecord, UsageRecord, SystemSnapshot
├── paths.py                     # AppPaths — ~/.claude, ~/.codex, config/data dirs
├── security.py                  # redact_sensitive (bloquea tokens en metadata)
├── runtime_store.py             # MemoryOffsetStore (offsets JSONL para lectura incremental)
├── collectors/
│   ├── base.py                  # Collector ABC, CollectBatch
│   ├── claude.py                # Lee ~/.claude/projects/**/*.jsonl y stats-cache.json
│   ├── claude_api_usage.py      # OAuth Usage API — límites 5h/semanal (cache 60s)
│   ├── codex.py                 # Lee ~/.codex/ (JSONL, SQLite, config.toml)
│   └── system.py                # winprobe + sensor probe + WMI fallback
├── tui/
│   ├── app.py                   # AiMeterTui (Textual): 4 timers, render
│   └── widgets.py               # render_bar, render_mini_bar, render_core_grid, render_sparkline
├── storage/db.py                # SQLite — solo export/historial, nunca hot-path
└── bin/win-x64/                 # Binarios precompilados
    ├── ai-meter-winprobe.exe    # C++ — CPU%, RAM, disco, red (stdout JSON streaming)
    ├── ai-meter-sensor-probe.exe# C# LHM — temp CPU/GPU, 16 cores (--once / --loop-to)
    └── LibreHardwareMonitorLib.dll

native/
├── winprobe/ai_meter_winprobe.cpp          # fuente C++ winprobe
└── AiMeter.SensorProbe/Program.cs          # fuente C# sensor probe
```

## Hardware objetivo

- CPU: AMD Ryzen 7 4800H (8 cores / 16 threads lógicos)
- GPU discreta: NVIDIA GeForce GTX 1650 Ti
- RAM: ~15.8 GB / OS: Windows 11 + Memory Integrity (HVCI) activo

## Temperatura CPU — por qué necesita privilegios

Ryzen lee temperatura via **MSR** (Model Specific Register). LHM necesita cargar
`WinRing0x64.sys` — driver de kernel — que requiere SYSTEM o Administrator.

Con HVCI activo, el driver solo carga si el proceso tiene privilegios suficientes.

**Solución implementada — tarea de fondo como SYSTEM:**

```
install-service (admin, una vez)
    → Registra tarea "ai-meter-sensor" en Task Scheduler
    → Corre: ai-meter-sensor-probe.exe --loop-to C:\ProgramData\ai-meter\sensor.json 5000
    → Escribe JSON cada 5s como NT AUTHORITY\SYSTEM

system.py._refresh_sensor_probe()
    → Primero intenta _read_service_file()  (sin admin, lee C:\ProgramData\ai-meter\sensor.json)
    → Si no existe o tiene >30s → _run_sensor_probe() (el proceso Python puede no tener admin)
    → Si el probe retorna needs_admin=True → _read_wmi_temp_fallback() (funciona si corre como admin)
```

## Modos del sensor probe (C#)

```powershell
ai-meter-sensor-probe.exe --once              # lee una vez, imprime JSON a stdout, exit
ai-meter-sensor-probe.exe --doctor            # como --once pero incluye lista completa de sensores
ai-meter-sensor-probe.exe --loop-to <file> [ms]  # loop infinito, escribe JSON cada N ms
```

**Output JSON de --once / --loop-to:**
```json
{
  "available": false,          // true si cpu_temp_c tiene valor válido
  "needs_admin": true,         // true si sensor existe pero lee 0 (MSR bloqueado)
  "cpu_temp_c": null,          // número si admin/servicio, null si sin permiso
  "gpu_temp_c": 65.0,          // funciona SIN admin (NVIDIA y AMD discretas)
  "gpu_name": "NVIDIA GeForce GTX 1650 Ti",
  "cpu_total_load": 44.5,      // funciona SIN admin
  "core_loads": [              // 16 entradas, funciona SIN admin
    {"name": "CPU Core #1", "load_percent": 55.1}, ...
  ],
  "source": "lhm:needs_admin", // o "lhm:Core (Tctl/Tdie)" cuando hay temp real
  "sensor_count": 89
}
```

Cuando corre como SYSTEM (vía tarea), `available: true` y `cpu_temp_c: 72.0`.

## Timers del TUI

| Timer | Intervalo | Qué hace |
|-------|-----------|---------|
| `_render_timer` | `refresh_ms` (100–5000ms) | Solo renderiza (no recolecta) |
| `_light_collect_timer` | `refresh_ms` | `system.collect(temp=False, top=False)` → CPU%, RAM, red |
| `_heavy_collect_timer` | `collector_heavy_interval_s` (10s) | Temp, cores, GPU + Claude/Codex local |
| `_api_collect_timer` | **60s fijo** | Claude OAuth usage API |

## Flujo de colección (MonitorEngine)

```
run_light_collection()      → system.collect(temp=False, top=False)
run_heavy_collection()      → system.collect(temp=True, top=True)
                            → codex_collector.collect()
                            → claude_collector.collect()
run_claude_api_collection() → claude_api.fetch_if_due()  [si TOKEN en .env]
```

Todos corren en threads daemon para no bloquear el render.

## Claude OAuth Usage API

- Endpoint: `GET https://api.anthropic.com/api/oauth/usage`
- No consume tokens de modelo
- Token en `.env`: `TOKEN = Bearer sk-ant-oat01--...`
- Cache: 60 segundos. Timer completamente independiente del loop de render.
- Response: `five_hour.utilization` (0–100%), `seven_day.utilization`, `resets_at` (ISO)
- Collector: `collectors/claude_api_usage.py → ClaudeApiUsageCollector`

## Reglas duras

1. `model-calls=off` siempre — cero texto/prompts hacia APIs de modelos
2. La OAuth API es la única excepción permitida (metadata de límites, no inferencia)
3. Toda metadata pasa por `security.redact_sensitive()` antes de mostrar o persistir
4. SQLite nunca en hot-path — solo export/historial explícito
5. Temperatura `0°C` o fuera de `[5.0, 130.0]` → siempre descartada como inválida
6. Si un dato no es confiable → `unknown` + razón exacta, nunca inventar

## Problemas comunes

**`PermissionError` al lanzar sensor probe desde subprocess:**
```powershell
Unblock-File src\ai_meter\bin\win-x64\ai-meter-sensor-probe.exe
```
Windows marca ejecutables recién compilados como zona no confiable. `build_native.ps1` lo hace automáticamente.

**Temperatura sigue como `unknown` después de `install-service`:**
```powershell
python -m ai_meter.main doctor   # verificar from_service=True
Get-ScheduledTask -TaskName "ai-meter-sensor"  # verificar que existe
Start-ScheduledTask -TaskName "ai-meter-sensor"  # forzar arranque si está detenida
```

**Temperatura WMI (`wmi:acpi`) en vez de LHM:**
El probe retornó `needs_admin` y el proceso Python corre como admin → usa WMI fallback.
Con la tarea instalada no debería pasar; si pasa, verificar que `C:\ProgramData\ai-meter\sensor.json` existe y tiene menos de 30s de antigüedad.
