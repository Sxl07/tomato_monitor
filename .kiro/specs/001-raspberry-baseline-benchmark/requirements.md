# Requirements - Raspberry Baseline Benchmark

## Objetivo

Medir el rendimiento real del pipeline actual de Tomato Monitor en Raspberry Pi 5 sin refactorización funcional inicial.

## Requisitos funcionales

### RF-001: Medición de carga de modelos

WHEN el usuario ejecute el benchmark de carga
THE SYSTEM SHALL medir el tiempo de carga del detector y del clasificador de sanidad.

### RF-002: Medición de inferencia individual

WHEN el usuario ejecute una inferencia sobre una imagen o frame
THE SYSTEM SHALL reportar tiempo de preprocesamiento, inferencia, postprocesamiento y tiempo total.

### RF-003: Medición de pipeline completo

WHEN el usuario ejecute el pipeline sobre un video corto
THE SYSTEM SHALL registrar duración total, FPS aproximado, uso de RAM, temperatura y errores.

### RF-004: Registro de resultados

WHEN finalice una prueba
THE SYSTEM SHALL guardar los resultados en un archivo documentable para la tesis.

## Requisitos no funcionales

### RNF-001: No refactorización inicial

THE SYSTEM SHALL medir el pipeline actual antes de proponer cambios estructurales.

### RNF-002: Compatibilidad Raspberry Pi

THE SYSTEM SHALL ejecutarse en Raspberry Pi 5 usando CPU.

### RNF-003: Monitoreo térmico

THE SYSTEM SHALL permitir registrar temperatura durante pruebas largas.

## Criterios de aceptación

- Existe al menos un script de benchmark de carga de modelos.
- Existe al menos un script de benchmark de inferencia individual.
- Existe al menos un formato de reporte.
- Los resultados pueden copiarse a `docs/benchmarks/raspberry-baseline.md`.

---

## Nota de alcance y transición

Este spec mide el pipeline de video que funcionó como prototipo de validación técnica del sistema. Sus resultados constituyen evidencia académica de referencia para la tesis, estableciendo la línea base de rendimiento contra la cual se evaluarán las decisiones posteriores.

A partir de la conclusión de este spec, el sistema evoluciona hacia un flujo operacional basado en cámara en tiempo real, captura de snapshots por detección de cambios e inferencia sobre imágenes individuales. El pipeline de video se conserva exclusivamente como herramienta de benchmark y pruebas.

Las siguientes specs implementan esa evolución:

| Spec | Propósito |
|---|---|
| 006 | Modelo de datos agrícola — persistencia SQLite con jerarquía Invernadero → Módulo → Monitoreo |
| 007 | Flujo de ejecución de monitoreo — cámara live, snapshots, inferencia por imagen |
| 008 | Rediseño de UI agrícola — interfaz táctil para el agricultor |
| 009 | Métricas agregadas y reportes — cálculos y visualización de resultados por monitoreo |