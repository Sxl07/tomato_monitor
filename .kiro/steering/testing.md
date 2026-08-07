# Testing Steering

## Objetivo

El proyecto debe incorporar pruebas y validaciones progresivas sin bloquear el desarrollo ni sobrecargar la Raspberry Pi.

## Estado actual

- Suite pytest automatizada con ~806 tests (unitarios, dominio, infraestructura, properties).
- Tests de importabilidad por capa (`tests/test_imports.py`).
- Tests de boundaries arquitectónicas (`tests/unit/test_architecture_boundaries.py`).
- Tests property-based con Hypothesis (`tests/properties/`).
- Ejecutar suite completa: `python -m pytest -q`
- Ejecutar solo unitarios: `python -m pytest tests/unit -q`
- La suite completa NO requiere cámara, GPIO ni Raspberry Pi.

## Tipos de pruebas

- Pruebas de importación (verifican que módulos se importan sin hardware).
- Pruebas de boundaries arquitectónicas (verifican que capas no importen módulos prohibidos).
- Pruebas unitarias para lógica pura.
- Pruebas property-based (Hypothesis) para invariantes.
- Pruebas de configuración y perfiles.
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
- Las pruebas que requieren Raspberry Pi se marcan con `@pytest.mark.raspberry`.
- Las pruebas que requieren hardware específico (cámara, GPIO) se marcan con `@pytest.mark.hardware`.
- La suite completa (`python -m pytest`) no debe requerir cámara, GPIO ni hardware especial.
- Tests de boundaries verifican estáticamente (lectura de archivos) que las reglas de arquitectura se cumplen.

## Reglas para Kiro

- Al crear nueva lógica pura, sugerir prueba unitaria.
- Al modificar pipeline de visión, sugerir prueba manual o benchmark.
- Al modificar configuración, sugerir prueba de importación.
- Al modificar FastAPI, sugerir prueba de endpoint.
- Al agregar reglas de arquitectura en steering, agregar test de boundary correspondiente.
