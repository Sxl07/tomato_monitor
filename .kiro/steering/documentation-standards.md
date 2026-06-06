# Documentation Standards Steering

## Objetivo

La documentación debe servir tanto para desarrollo técnico como para sustentar la tesis.

## Reglas de documentación

- Documentar decisiones importantes en `docs/decisions/` usando ADRs.
- Documentar benchmarks en `docs/benchmarks/`.
- Documentar configuración de Raspberry en `docs/raspberry-setup.md`.
- Documentar hardware en `docs/hardware.md`.
- Documentar arquitectura en `docs/architecture.md`.
- No inventar resultados no medidos.
- Separar observaciones, resultados y conclusiones.
- Usar lenguaje técnico claro y profesional.
- Indicar limitaciones cuando existan.
- Mantener trazabilidad entre spec, implementación, benchmark y decisión.

## Estructura mínima para ADR

Cada ADR debe incluir:

- Estado.
- Contexto.
- Decisión.
- Consecuencias positivas.
- Consecuencias negativas.
- Evidencia.
- Fecha.
- Relación con specs.

## Reglas para Kiro

- Si propone una decisión técnica relevante, debe sugerir un ADR.
- Si propone una optimización, debe sugerir una métrica.
- Si crea scripts de benchmark, debe actualizar la plantilla de documentación.
- No mezclar documentación académica con comentarios innecesarios en código.