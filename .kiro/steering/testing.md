# Testing Steering

## Objetivo

El proyecto debe incorporar pruebas y validaciones progresivas sin bloquear el desarrollo ni sobrecargar la Raspberry Pi.

## Tipos de pruebas

- Pruebas de importación.
- Pruebas unitarias para lógica pura.
- Pruebas de configuración.
- Pruebas de carga de modelos.
- Pruebas de inferencia individual.
- Pruebas de pipeline completo con video corto.
- Pruebas manuales documentadas en Raspberry Pi.

## Reglas

- No ejecutar pruebas pesadas automáticamente.
- No ejecutar benchmarks largos desde hooks.
- Mantener scripts de benchmark separados del pipeline principal.
- Los scripts de benchmark deben vivir en `scripts/benchmarks/`.
- Los resultados deben documentarse en `docs/benchmarks/`.
- Las pruebas de lógica pura no deben depender de modelos pesados.
- Las pruebas de endpoints no deben cargar Detectron2 si no es necesario.
- Los benchmarks deben registrar commit, fecha, dispositivo y configuración.

## Reglas para Kiro

- Al crear nueva lógica pura, sugerir prueba unitaria.
- Al modificar pipeline de visión, sugerir prueba manual o benchmark.
- Al modificar configuración, sugerir prueba de importación.
- Al modificar FastAPI, sugerir prueba de endpoint.