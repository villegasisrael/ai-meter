# ai-meter

Monitor TUI Windows estilo **btop** con uso de IA (Claude y Codex) en tiempo real.
Sin llamadas a modelos. Todo local.

```
view=1 | model-calls=off | usage-api=on | refresh=1000ms | 13:01:40
┌─ CLAUDE ──────────────────────┐  ┌─ SYSTEM winprobe_cpp ──────────────┐
│ plan: claude_pro               │  │ cpu  ████████████░░░░░  62.1%      │
│ 5h     ██████░░░░  47% reset 8m│  │  C1 ███░  39%  C2 █░   18%        │
│ weekly ████████░░  86% reset 4d│  │  C3 ███░  37%  C4 ██░  27%  ...   │
│ api fetched 19:01:27           │  │ ram  ████████████████░░  88.0%     │
│ tokens (last): 133             │  │ disk █████████████░░░░░  54.8%     │
├─ CODEX ───────────────────────┤  │ temp 72.0°C (lhm:Core Tctl/Tdie)  │
│ plan: plus                     │  │ gpu  65.0°C (GTX 1650 Ti)          │
│ 5h     █░░░░░░░░░  14% reset 4h│  │ net  ↑16419MB  ↓18271MB            │
│ weekly █░░░░░░░░░  12% reset 5d│  └────────────────────────────────────┘
└────────────────────────────────┘  ┌─ LIVE ACTIVITY ──────────────────┐
                                    │ 18:18:39 claude user claude-vscode│
                                    └────────────────────────────────────┘
```

---

## Instalación

```powershell
pip install -e "c:\xampp\htdocs\clawdex"
```

---

## Primer uso

```powershell
python -m ai_meter.main run
```

Esto ya muestra CPU%, RAM, disco, red, GPU temp, límites Codex y actividad live de Claude.

---

## Temperatura CPU (Ryzen/Intel) — configuración única

La temperatura del CPU requiere acceso al kernel de Windows. Se resuelve instalando
una tarea de fondo que corre una sola vez como administrador.

### Paso 1 — Abrir PowerShell como Administrador

Click derecho en el ícono de PowerShell → **"Ejecutar como administrador"**

### Paso 2 — Instalar la tarea de fondo

```powershell
cd c:\xampp\htdocs\clawdex
python -m ai_meter.main install-service
```

Salida esperada:
```
Installed! Sensor task registered as SYSTEM.
Output file: C:\ProgramData\ai-meter\sensor.json
CPU temperature will now be available without admin.
Start the monitor normally: python -m ai_meter.main run
```

### Paso 3 — Cerrar esa ventana de admin y usar normal

```powershell
# Ventana normal, sin admin
python -m ai_meter.main run
```

La temperatura CPU (`lhm:Core Tctl/Tdie`) aparece automáticamente.
La tarea se inicia sola en cada arranque de Windows. No hay que repetir estos pasos.

### Desinstalar la tarea

```powershell
# Como Administrador
python -m ai_meter.main uninstall-service
```

---

## Límites de uso Claude (5h y semanal)

Crear un archivo `.env` en la raíz del proyecto:

```
TOKEN = Bearer sk-ant-oat01--TU_TOKEN_AQUI
```

El token es el mismo que usa Claude Code internamente. Con él, el monitor consulta
`https://api.anthropic.com/api/oauth/usage` — **no consume tokens de modelo** —
y muestra los porcentajes reales con countdown de reset.

La API se consulta **una vez cada 60 segundos**. Sin el token, los límites muestran `unknown`.

---

## Cómo funciona la temperatura (para entender el por qué)

En procesadores AMD Ryzen (y la mayoría de Intel modernos), la temperatura real se lee
desde registros del procesador (MSR) que solo son accesibles con privilegios de kernel.

La tarea instalada (`ai-meter-sensor`) corre como `NT AUTHORITY\SYSTEM` — que tiene esos
privilegios — y escribe los datos a `C:\ProgramData\ai-meter\sensor.json` cada 5 segundos.
El monitor lee ese archivo sin necesitar privilegios propios.

```
[Tarea SYSTEM, en background]                [Monitor, usuario normal]
ai-meter-sensor-probe.exe --loop-to  →  C:\ProgramData\ai-meter\sensor.json  →  ai-meter run
```

Si la tarea no está instalada, el monitor sigue funcionando pero muestra
`temp: unknown (lhm:needs_admin)` y sugiere correr `install-service`.

---

## Diagnóstico

```powershell
python -m ai_meter.main doctor
```

Muestra el estado de cada fuente de datos. Ejemplo:

```
cpu temp          │ 72.0 C (lhm:Core (Tctl/Tdie))
gpu temp          │ 65.0 C (NVIDIA GeForce GTX 1650 Ti)
cpu cores tracked │ 16
sensor probe      │ from_service=True, needs_admin=False
```

Si `from_service=True` → la tarea está corriendo y la temperatura es real sin admin.
Si `needs_admin=True` → falta instalar la tarea (`install-service`).

---

## Controles del monitor

| Tecla | Acción |
|-------|--------|
| `+` / `=` | Refresh más lento (+100ms) |
| `-` | Refresh más rápido (-100ms) |
| `r` | Actualizar ahora |
| `p` | Pausar / reanudar |
| `h` | Ayuda |
| `q` | Salir |

Rango de refresh: 100ms – 5000ms (default 1000ms).

---

## Comandos

```powershell
python -m ai_meter.main run                # Monitor TUI
python -m ai_meter.main doctor             # Diagnóstico
python -m ai_meter.main install-service    # Instalar tarea temp (admin, una vez)
python -m ai_meter.main uninstall-service  # Desinstalar tarea temp (admin)
python -m ai_meter.main paths              # Ver rutas de datos locales
python -m ai_meter.main export --format json --out datos.json
python -m ai_meter.main export --format csv  --out datos.csv
```

---

## Build de binarios nativos

Los binarios precompilados ya están en `src/ai_meter/bin/win-x64/`.
Para reconstruirlos (requiere .NET 6 SDK y g++/MinGW):

```powershell
.\scripts\build_native.ps1
```

El script compila y hace `Unblock-File` automáticamente para que Windows
no bloquee los ejecutables recién compilados.

---

## Fuentes de datos

| Dato | Fuente | Requiere |
|------|--------|---------|
| CPU%, RAM, disco, red | `ai-meter-winprobe.exe` (C++ Win32) | Nada |
| Temp CPU, carga por core | `ai-meter-sensor-probe.exe` via tarea SYSTEM | `install-service` (una vez) |
| Temp GPU (NVIDIA/AMD) | `ai-meter-sensor-probe.exe` | Nada (funciona sin admin) |
| Límites Codex 5h/semanal | `~/.codex/config.toml` | Solo lectura |
| Tokens Claude observados | `~/.claude/projects/**/*.jsonl` | Solo lectura |
| Límites Claude 5h/semanal | OAuth API (no consume tokens) | Token en `.env` |

---

## Privacidad

- **`model-calls=off`** siempre. Nada de texto o prompts sale hacia un modelo.
- La OAuth API solo recibe el token de auth, no datos de sesión ni prompts.
- Los archivos `~/.claude` y `~/.codex` se leen localmente y nunca se transmiten.
- Secrets en metadata se redactan antes de mostrar (`security.redact_sensitive`).
