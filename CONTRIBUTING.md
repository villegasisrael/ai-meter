# Contribuir

Gracias por mejorar `ai-meter`. El proyecto prioriza datos locales, bajo consumo y una TUI fluida.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

## Validacion

Antes de abrir un cambio:

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests
python -m compileall src\ai_meter
python -m ai_meter.main doctor
```

## Principios

- No hacer llamadas a modelos.
- Preferir archivos locales de Codex/Claude.
- Mostrar `unknown` si un dato no es confiable.
- Mantener `persist_history=false` por defecto.
- Mantener collectors pesados fuera del hot path.
- No leer ni mostrar secretos de `auth.json`, `.credentials.json` o `.env`.

## Performance

- No crear hilos por tick; usar workers persistentes.
- No escanear JSONL completo en ciclos frecuentes.
- No llamar subprocess, SQLite o PowerShell desde render ni light collect.
- Si se toca UI, probar ancho reducido y proveedores deshabilitados.
