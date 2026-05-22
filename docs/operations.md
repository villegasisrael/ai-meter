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
- homes de Codex/Claude
- fuente de metricas sistema
- temperatura CPU/GPU como valor real o `unknown`

Comandos utiles si GPU aparece `unknown`:

```powershell
nvidia-smi --query-gpu=name,temperature.gpu --format=csv,noheader,nounits
amd-smi monitor --temperature
rocm-smi --showtemp --json
```

## Limpieza legacy

`install-service` esta deshabilitado. No instalar servicios ni drivers para temperatura.

Si existe una instalacion anterior del sensor:

```powershell
python -m ai_meter.main uninstall-service
```

Ejecutar como administrador. El comando elimina la tarea `ai-meter-sensor`, el servicio `WinRing0_1_2_0` y archivos legacy conocidos.

## Build

```powershell
.\scripts\build_native.ps1
.\scripts\build_exe.ps1
```

`build_native.ps1` recompila `ai-meter-winprobe.exe` si hay `g++`.

## Pruebas

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python -m compileall src\ai_meter
```
