# Thesis Context Steering

## Naturaleza del proyecto

Proyecto de grado orientado al despliegue de visión por computador en hardware edge y trazabilidad agrícola. Las decisiones técnicas deben poder justificarse en documentación académica con evidencia reproducible.

## Objetivo de la tesis

Desarrollar y evaluar un sistema portátil embebido de monitoreo visual y trazabilidad agrícola para tomates cherry en invernadero, ejecutable en Raspberry Pi 5, que detecte frutos, clasifique su sanidad visual, estime su grado de madurez y permita registrar, exportar y sincronizar evidencia agrícola.

## Ajuste de alcance

El proyecto pasó de un enfoque robótico/autónomo (robot con chasis, motores y navegación automática) a una plataforma portátil operada manualmente. Este cambio es una **decisión deliberada de producto**, no un fallo de implementación. La ausencia de locomoción autónoma se documenta como ajuste de alcance justificado por:

- Complejidad mecánica fuera del foco de la tesis (visión por computador)
- Tiempo disponible para el proyecto de grado
- Valor demostrable con monitoreo manual + procesamiento local + trazabilidad

Los objetivos de tesis se alinean con: dispositivo portátil embebido, visión por computador, trazabilidad agrícola, procesamiento local y exportación/sincronización de datos.

## Fase actual del proyecto

Pipeline capture-first completamente validado en Raspberry Pi 5. El flujo Greenhouse → Module → Monitoring → preview → capture → finalize-capture → analyzing → report funciona end-to-end. El siguiente hito es Spec 015 — Portable Monitoring and Crop Traceability.

## Specs en curso

Estado basado en `tasks.md` y validación manual reportada.

| Spec | Directorio | Estado |
|---|---|---|
| 001 — Benchmark de línea base | `.kiro/specs/001-raspberry-baseline-benchmark/` | Parcialmente completada (fases 1-3 done, benchmark formal pendiente) |
| 002 — Integración cámara live | `.kiro/specs/002-camera-live-integration/` | Absorbida por Spec 007/009 |
| 003 — Optimización edge | `.kiro/specs/003-edge-pipeline-optimization/` | Planificada (depende de benchmark formal) |
| 004 — Documentación tesis | `.kiro/specs/004-thesis-documentation/` | Parcialmente completada |
| 005 — Calidad y seguridad | `.kiro/specs/005-code-quality-and-security-hardening/` | Parcial (tests automatizados activos, hardening formal pendiente) |
| 006 — Modelo de datos agrícola | `.kiro/specs/006-agricultural-data-model/` | **Completada** |
| 007 — Flujo de monitoreo live | `.kiro/specs/007-monitoring-execution-flow/` | Core completado, superseded por spec 009 para flujo capture-first |
| 008 — UI agrícola | `.kiro/specs/008-agricultural-ui-redesign/` | Planificada |
| 008 — Hybrid monitoring pipeline | `.kiro/specs/008-lightweight-hybrid-monitoring-pipeline/` | **Completada** + validada en RPi |
| 009 — Capture-first final analysis | `.kiro/specs/009-capture-first-final-analysis-pipeline/` | **Completada** + validada en RPi (20 snapshots, analyzing OK, reporte OK, ~75.7°C) |
| 010 — Monitoring UX enhancement | `.kiro/specs/010-monitoring-ux-enhancement/` | Core completado (tests opcionales pendientes) |
| 015 — Portable monitoring and crop traceability | `.kiro/specs/015-portable-monitoring-and-crop-traceability/` | **En curso** — alcance portátil + trazabilidad |
| camera-live-fix | `.kiro/specs/camera-live-fix/` | Bugfix completado |
| monitoring-session-flow-fix | `.kiro/specs/monitoring-session-flow-fix/` | Bugfix completado |

## Criterios académicos aplicables

- **Reproducibilidad:** toda prueba debe poder repetirse con los mismos parámetros y obtener resultados comparables
- **Evidencia cuantitativa:** las afirmaciones de rendimiento deben estar respaldadas por métricas medidas, no estimaciones
- **Comparación de alternativas:** cuando se toma una decisión técnica relevante, documentar las alternativas descartadas y la razón (formato ADR en `docs/decisions/`)
- **Reconocimiento de limitaciones:** documentar explícitamente las limitaciones del sistema y sus causas
- **Trazabilidad:** cada decisión técnica debe tener su ADR; cada resultado experimental debe tener su benchmark
- **Ajuste de alcance:** documentar cambios de alcance como decisiones justificadas, no como fallos

## Documentación académica existente

- `docs/project-overview.md` — descripción general y restricción principal
- `docs/hardware.md` — hardware disponible y estado de validación
- `docs/raspberry-setup.md` — procedimiento de instalación en RPi
- `docs/architecture.md` — arquitectura formal del sistema (Clean Architecture, capas, componentes)
- `docs/thesis-notes/current-state.md` — estado técnico detallado con deuda identificada
- `docs/thesis-notes/next-steps.md` — plan de trabajo con orden de specs
- `docs/thesis-notes/limitations.md` — limitaciones conocidas del sistema
- `docs/benchmarks/benchmark-template.md` — plantilla estándar de reporte de benchmark
- `docs/benchmarks/raspberry-baseline.md` — resultados del benchmark de línea base (pendiente de ejecución)
- `docs/decisions/ADR-001` — Detectron2 en RPi (completo)
- `docs/decisions/ADR-002` — estrategia AI Camera (completo)
- `docs/decisions/ADR-003` — benchmark-first (completo)

## Estilo de escritura para tesis

- Lenguaje técnico, impersonal, claro y preciso
- No usar afirmaciones sin respaldo cuantitativo
- Indicar siempre la fuente o evidencia de una conclusión
- Distinguir entre observaciones informales ("se observó") y mediciones formales ("se midió con X herramienta")
- Describir el sistema como plataforma portátil embebida de monitoreo visual y trazabilidad agrícola
- Documentar la ausencia de locomoción autónoma como ajuste deliberado de alcance
