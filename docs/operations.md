# Operacion

## Ejecucion

```powershell
$env:PYTHONPATH='src'
python -m ai_meter.main run
```

Con entrypoint instalado:

```powershell
ai-meter run
```

## Diagnostico

```powershell
python -m ai_meter.main doctor
python -m ai_meter.main paths
```

`doctor` debe mostrar:

- config/data paths
- proveedores habilitados
- estado de Claude usage API
- estado de la integración de cuenta Codex, ejecutable detectado e intervalo
- homes de Codex/Claude
- fuente de metricas sistema
- estado de cada proveedor de hardware
- temperatura CPU/GPU como valor real o `unknown`

El diagnóstico de Codex muestra configuración y disponibilidad del ejecutable;
la consulta autenticada se ejecuta en la TUI. En CODEX, `r` solicita estadísticas
actualizadas incluso estando pausado. `p` detiene la recolección automática.
La sección de cuenta muestra hoy, ayer, acumulado y antigüedad; debajo continúa
la actividad local. No requiere cambiar `persist_history=false`.

```toml
[providers.codex]
usage_api_enabled = true
usage_api_interval_s = 900
app_server_path = ""
```

La ruta opcional corresponde al ejecutable Codex, sin argumentos. En Windows
se prefiere la instalación de escritorio; como alternativa se usa el CLI del
PATH. No se inicia sesión automáticamente ni se modifican sus credenciales.

Comandos utiles si GPU aparece `unknown`:

```powershell
nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader,nounits
amd-smi monitor --temperature
rocm-smi --showtemp --json
```

Con AMD Ryzen Master Monitoring SDK instalado, compilar y diagnosticar desde una
PowerShell como administrador:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\.venv\Scripts\python.exe -m ai_meter.main doctor
```

`doctor` debe mostrar `amd_ryzen_master.available=true`, la ruta autodetectada y
una temperatura CPU. El probe acepta `--once`, emite JSON y nunca crea ni inicia
el driver de AMD.

## Limpieza legacy

`install-service` esta deshabilitado. No instalar servicios ni drivers para temperatura.

Si existe una instalacion anterior del sensor:

```powershell
python -m ai_meter.main uninstall-service
```

Ejecutar como administrador. El comando elimina la tarea `ai-meter-sensor`, el servicio `WinRing0_1_2_0` y archivos legacy conocidos.

## Build

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_native.ps1
.\scripts\build_exe.ps1
```

`build_native.ps1` recompila `ai-meter-winprobe.exe` si hay `g++` y
`ai-meter-amd-probe.exe` si existen Visual Studio 2022 C++ Build Tools y el SDK AMD.

## Pruebas

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python -m compileall src\ai_meter
```
