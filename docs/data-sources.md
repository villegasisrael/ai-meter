# Fuentes de datos

## Codex

Principal:

- `~/.codex/sessions/**/*.jsonl`
- `~/.codex/archived_sessions/*.jsonl`
- Campo: `payload.rate_limits` o `payload.info`/`payload.rate_limits` en eventos `token_count`
- Ventanas: se normalizan por duracion (`300` min = 5h, `10080` min = semanal)

Fallback:

- `~/.codex/logs_2.sqlite`
- Evento `codex.rate_limits`

Limitacion: Codex expone porcentaje usado y reset, no un limite absoluto en tokens/unidades.

## Claude

Plan:

- `~/.claude/.credentials.json`
- Campo: `claudeAiOauth.subscriptionType`
- Fallback: `~/.claude/backups/.claude.json.backup.*`

Uso observado:

- `~/.claude/projects/**/*.jsonl`
- `~/.claude/stats-cache.json`

Limites reales:

- Via `https://api.anthropic.com/api/oauth/usage` (mismo endpoint que usa `/usage` de Claude Code).
- NO es la API de facturacion: no consume tokens de modelo ni genera cargos. Usa el token OAuth
  de la sesion (`sk-ant-oat01-...`), no una API key `sk-ant-api...`.
- Habilitado por defecto con `providers.claude.usage_api_enabled=true`.
- Token: se auto-carga desde `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`).
  Un `TOKEN` explicito en `.env` (con prefijo `Bearer `) tiene prioridad como override manual.
- Se respeta `claudeAiOauth.expiresAt`. Si el token de disco esta vencido se intenta primero un
  refresh automatico usando el `refreshToken` (cliente publico PKCE, sin secretos); el token nuevo
  se persiste de vuelta en `.credentials.json` para que Claude Code lo siga usando.
- Si el refresh tambien falla (refresh token vencido), la TUI muestra `relogin in Claude Code` y se
  puede pulsar `l` para hacer un re-login OAuth completo desde la propia app: abre el navegador,
  pegas el codigo `code#state` y se reescribe `~/.claude/.credentials.json`. El collector detecta el
  cambio de mtime y reanuda solo.
- Soporta ventanas `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_cowork`,
  `seven_day_design`, `seven_day_routines` y `extra_usage` cuando la API las entrega.
- Si no esta habilitado, el token falta/vence, o la llamada falla, mostrar `unknown`.

## Sistema

Metricas rapidas:

- Windows: `src/ai_meter/bin/win-x64/ai-meter-winprobe.exe`.
- Linux/Ubuntu: `psutil`.
- Entrega CPU total, RAM, disk y red.
- Fallback Windows: `psutil`.

Cores:

- Delta de `psutil.cpu_times(percpu=True)`.

Temperatura:

- Windows: WMI confiable si ya existe en el sistema y la sesion ya esta elevada.
- Windows: WMI ACPI puede usarse como fallback debil solo en sesion elevada; sin permisos se informa `unknown`.
- Linux/Ubuntu CPU: `psutil.sensors_temperatures()` o `/sys/class/hwmon` si el kernel expone sensores (`coretemp`, `k10temp`, `zenpower`, etc.).
- GPU NVIDIA: `nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader,nounits`.
- GPU AMD: `amd-smi`/`rocm-smi` si estan instalados; en Windows `amd-smi` debe estar en una ruta ROCm acotada o en `AI_METER_AMD_SMI`; en Linux tambien `/sys/class/hwmon` via `amdgpu`.
- Si no hay fuente confiable, mostrar `unknown`.

## Driver legacy

No instalar ni usar LibreHardwareMonitor/WinRing0. Versiones anteriores podian generar `ai-meter-sensor-probe.sys`; si aparece Anti-Tampering, ejecutar la limpieza de `docs/troubleshooting.md`.
