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

- Registro: `HardwareRegistry` conserva la mejor métrica por `id` según
  `providers.hardware.source_priority` y elimina valores vencidos.
- Fabricante CPU: Windows usa `VendorIdentifier` del registro
  (`AuthenticAMD`/`GenuineIntel`); Linux usa `vendor_id` de `/proc/cpuinfo`.
  La detección ocurre una vez antes de cargar adaptadores específicos.
- Windows AMD Ryzen: `ai-meter-amd-probe.exe`, incluido en el paquete y construido
  contra AMD Ryzen Master Monitoring SDK. Se autodetecta; `amd_probe_path` o
  `AI_METER_AMD_PROBE` sólo permiten reemplazarlo.
- Windows Intel: actualmente usa únicamente el fallback seguro WMI/ACPI. No se
  carga el probe AMD. Si el firmware no publica una lectura confiable, informa
  `unknown`; no se instala un driver MSR.
- Windows: WMI confiable si ya existe en el sistema y la sesion ya esta elevada.
- Windows: WMI ACPI puede usarse como fallback debil solo en sesion elevada; sin permisos se informa `unknown`.
- Linux/Ubuntu CPU: `psutil.sensors_temperatures()` o `/sys/class/hwmon` si el kernel expone sensores (`coretemp`, `k10temp`, `zenpower`, etc.).
- GPU NVIDIA: `nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader,nounits`.
- GPU AMD: `amd-smi`/`rocm-smi` si estan instalados; en Windows `amd-smi` debe estar en una ruta ROCm acotada o en `AI_METER_AMD_SMI`; en Linux tambien `/sys/class/hwmon` via `amdgpu`.
- Si no hay fuente confiable, mostrar `unknown`.

Contrato del sidecar AMD:

```json
{"cpu_temp_c":62.5,"cpu_power_w":88.0,"ppt_percent":54.0}
```

También puede entregar `metrics` como una lista de objetos normalizados. Debe
aceptar `--once`, escribir un único objeto JSON en stdout y ser de sólo lectura.
El probe consulta temperatura, potencia, voltaje, frecuencia y límites PPT/TDC/EDC.
No instala ni inicia `AMDRyzenMasterDriverV32`; sólo lee cuando el SDK, el servicio
y una sesión elevada ya están disponibles. `ai-meter` no redistribuye el SDK de AMD.

## Driver legacy

No instalar ni usar LibreHardwareMonitor/WinRing0. Versiones anteriores podian generar `ai-meter-sensor-probe.sys`; si aparece Anti-Tampering, ejecutar la limpieza de `docs/troubleshooting.md`.
