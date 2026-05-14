# Plan V2: ai-meter Windows (sin dependencias manuales del usuario)

## 1. Objetivo
Construir `ai-meter` para Windows como monitor TUI rapido tipo btop, con:
1. Uso Claude/Codex (incluyendo 5h y semanal cuando exista fuente local valida).
2. Hardware en tiempo real (CPU/RAM/disco/red/procesos).
3. Temperatura CPU confiable en Ryzen laptop.
4. Cero llamadas a APIs de modelos (`external_calls=disabled`).

## 2. Restricciones duras
1. Runtime sin red para modelos ni servicios externos.
2. Usuario no instala herramientas extra por separado.
3. Todo backend necesario va embebido en el proyecto/artefacto.
4. Si un dato no es confiable: mostrar `unknown` + causa exacta.

## 3. Decisiones de arquitectura
1. Mantener TUI en Python/Textual solo para render y UX.
2. Mover recoleccion hardware critica a backend nativo Windows.
3. Separar dos probes nativos:
   - `usage/local collectors` (Python, archivos locales).
   - `hw probe` (nativo, stream JSON local).
4. Para temperatura CPU: usar componente nativo con acceso low-level integrado en el paquete.
5. SQLite solo opcional para historico/export, nunca hot-path.

## 4. Fase A: Diagnostico reproducible
1. Crear comando `ai-meter doctor --full` con salida estructurada JSON.
2. Reportar:
   - backend activo
   - permisos/elevacion
   - driver cargado/no cargado
   - fuente de temperatura usada
   - razon exacta de `unknown`
3. Criterio de aceptacion:
   - En cualquier equipo Windows, `doctor --full` explica por que no hay temperatura sin ambiguedad.

## 5. Fase B: Backend de temperatura robusto (Windows)
1. Implementar backend nativo embebido para sensores CPU Ryzen.
2. Soportar modo servicio local o helper elevado bajo demanda.
3. Definir prioridad de fuentes:
   - `native_low_level` (principal)
   - `embedded_lhm` (secundaria)
   - `wmi_acpi` (terciaria)
4. Rechazar valores invalidos (`0`, `NaN`, fuera de rango) con motivo.
5. Criterio de aceptacion:
   - En el equipo objetivo Ryzen 7 4800H, mostrar temperatura real estable.
   - Si falla, diagnostico exacto con recomendacion automatica.

## 6. Fase C: Rendimiento tipo monitor fluido
1. Recoleccion en hilos/procesos separados del render.
2. Stream de metricas nativas a intervalos fijos (100–5000 ms).
3. Evitar bloqueos de UI por collectors pesados.
4. Criterios de aceptacion:
   - `1000ms`: CPU app < 5%, RAM < 200MB.
   - `100ms`: controles `+/-` reflejan cambio real de latencia.
   - No congelamientos visibles en TUI.

## 7. Fase D: Limites de uso Claude/Codex
1. Codex: parseo local de `rate_limits` con 5h/semanal y reset.
2. Claude: extraer 5h/semanal desde artefactos locales disponibles.
3. Si Claude no expone localmente esos limites en esa version: `unknown` + `reason`.
4. Criterio de aceptacion:
   - Panels muestran `% usado`, `% restante`, `reset in`.
   - Nunca inventar limites.

## 8. Fase E: UX/TUI
1. Mejorar tema/colores y legibilidad tipo monitor.
2. `LIVE ACTIVITY` solo eventos utiles, no payloads crudos.
3. Header con estado claro: refresh, backend, temp source, external calls.
4. Criterio de aceptacion:
   - UI fluida, legible y con feedback inmediato en controles.

## 9. Fase F: Empaquetado e instalacion simple
1. Comando unico de build: `.\scripts\build_all.ps1`.
2. Entrega:
   - `ai-meter.exe`
   - componentes nativos requeridos embebidos
3. Comando de uso final:
   - `ai-meter run`
4. Criterio de aceptacion:
   - Instalacion limpia en Windows nuevo sin instalaciones manuales adicionales.

## 10. Matriz de pruebas obligatoria
1. Windows 11 + Ryzen laptop (objetivo principal).
2. Sin admin inicial, luego con elevacion controlada.
3. Con VBS/Memory Integrity activado.
4. Pruebas de corrupcion JSONL/archivos faltantes.
5. Smoke test de cada subcomando CLI.

## 11. Entregables
1. Codigo backend nativo + integracion Python.
2. `doctor --full` con JSON diagnostico.
3. Benchmarks de rendimiento.
4. Guia de operacion y troubleshooting.
5. Lista de limites conocidos por hardware/OEM.

## 12. Definicion de listo (DoD)
1. Temperatura CPU real visible en equipo objetivo Ryzen.
2. Monitor fluido con refresh efectivo 100ms–5000ms.
3. Limites 5h/semanal mostrados cuando hay fuente local valida.
4. Cero llamadas a modelos/APIs externas en runtime.
5. Instalacion/ejecucion con comando simple sin dependencia manual del usuario.
