# Requirements - Code Quality and Security Hardening

## Objetivo

Mejorar la calidad, mantenibilidad y seguridad básica de Tomato Monitor sin romper el comportamiento existente y respetando las restricciones de Raspberry Pi.

## Requisitos funcionales

### RF-001: Auditoría de calidad

WHEN se ejecute la auditoría de calidad
THE SYSTEM SHALL identificar problemas de Clean Code, SOLID, acoplamiento, cohesión y manejo de errores.

### RF-002: Validación de entradas

WHEN la aplicación reciba rutas, archivos o parámetros
THE SYSTEM SHALL validar que sean seguros, existentes y compatibles con el flujo esperado.

### RF-003: Manejo de errores

WHEN ocurra un error de carga de modelo, archivo, cámara o pipeline
THE SYSTEM SHALL reportar el error de manera clara sin ocultar información técnica relevante para depuración.

### RF-004: Configuración segura

WHEN el sistema use rutas, parámetros o banderas de ejecución
THE SYSTEM SHALL obtenerlos desde configuración centralizada o variables controladas, no desde valores hardcodeados innecesarios.

## Requisitos no funcionales

### RNF-001: No romper comportamiento existente

THE SYSTEM SHALL preserve current behavior unless a change is explicitly approved.

### RNF-002: Compatibilidad Raspberry Pi

THE SYSTEM SHALL remain compatible with CPU execution on Raspberry Pi 5.

### RNF-003: Seguridad CIA

THE SYSTEM SHALL consider confidentiality, integrity and availability in design decisions.

### RNF-004: Cambios pequeños

THE SYSTEM SHALL implement improvements in small, reviewable commits.

## Criterios de aceptación

- Existe una auditoría documentada.
- Las mejoras están priorizadas.
- No se cambia el pipeline principal sin spec.
- Se agregan pruebas ligeras donde aplique.
- Las decisiones relevantes se documentan en ADR.