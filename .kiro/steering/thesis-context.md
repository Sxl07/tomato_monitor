# Thesis Context Steering

## Naturaleza del proyecto

Proyecto de grado orientado al despliegue de visión por computador en hardware edge. Las decisiones técnicas deben poder justificarse en documentación académica con evidencia reproducible.

## Objetivo de la tesis

Desarrollar y evaluar un sistema de monitoreo visual de tomates cherry ejecutable en Raspberry Pi 5, que detecte frutos, clasifique su sanidad visual y estime su grado de madurez.

## Fase actual del proyecto

Validación funcional del pipeline completo en RPi 5. El siguiente hito es el benchmark de línea base (ver `docs/benchmarks/raspberry-baseline.md`).

## Specs en curso

| Spec | Directorio | Estado |
|---|---|---|
| 001 — Benchmark de línea base | `.kiro/specs/001-raspberry-baseline-benchmark/` | Fases 1-3 completadas |
| 002 — Integración cámara live | `.kiro/specs/002-camera-live-integration/` | Absorbida por Spec 007 |
| 003 — Optimización edge | `.kiro/specs/003-edge-pipeline-optimization/` | Planificada (depende de 001, 007) |
| 004 — Documentación tesis | `.kiro/specs/004-thesis-documentation/` | Parcialmente completada |
| 005 — Calidad y seguridad | `.kiro/specs/005-code-quality-and-security-hardening/` | Planificada |
| 006 — Modelo de datos agrícola | `.kiro/specs/006-agricultural-data-model/` | **Próxima a crear** |
| 007 — Flujo de monitoreo live | `.kiro/specs/007-monitoring-execution-flow/` | Planificada (depende de 001, 006) |
| 008 — UI agrícola | `.kiro/specs/008-agricultural-ui-redesign/` | Planificada (depende de 006, 007) |
| 009 — Métricas y reportes | `.kiro/specs/009-aggregated-metrics-reports/` | Planificada (depende de 006, 007) |

## Criterios académicos aplicables

- **Reproducibilidad:** toda prueba debe poder repetirse con los mismos parámetros y obtener resultados comparables
- **Evidencia cuantitativa:** las afirmaciones de rendimiento deben estar respaldadas por métricas medidas, no estimaciones
- **Comparación de alternativas:** cuando se toma una decisión técnica relevante, documentar las alternativas descartadas y la razón (formato ADR en `docs/decisions/`)
- **Reconocimiento de limitaciones:** documentar explícitamente las limitaciones del sistema y sus causas
- **Trazabilidad:** cada decisión técnica debe tener su ADR; cada resultado experimental debe tener su benchmark

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
