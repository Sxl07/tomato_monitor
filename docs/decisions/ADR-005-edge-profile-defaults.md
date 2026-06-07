# ADR-005: Perfiles de ejecución edge vs full

## Estado
Aceptado

## Fecha
Junio 2026

## Contexto
El sistema Tomato Monitor ejecuta inferencia de visión por computador (Detectron2 + ResNet-18 + estimación de madurez) en Raspberry Pi 5 con CPU únicamente. La inferencia sostenida causa carga alta de CPU y calentamiento del SoC, llevando a throttling térmico durante sesiones de monitoreo extendidas.

## Decisión
Se definen dos perfiles de ejecución configurables:

- **edge**: Valores conservadores para operación sostenida en RPi 5 (resolución reducida, skip madurez, mayor cooldown de Scene Gate, umbrales térmicos más conservadores)
- **full**: Valores completos para desarrollo en PC (resolución estándar, todas las etapas de inferencia habilitadas)

El perfil "edge" es el predeterminado en el entorno de producción (RPi).

## Consecuencias positivas
- Operación sostenida sin throttling térmico
- Menor tiempo de inferencia por snapshot (~30% menos sin madurez)
- Menor frecuencia de captura (mayor cooldown) reduce carga total
- Todos los parámetros son configurables — no se pierde funcionalidad

## Consecuencias negativas
- En modo edge, no se estima madurez (etapa USDA no disponible en reportes)
- Mayor threshold de detección (0.85 vs 0.80) puede perder algunos tomates de baja confianza
- Mayor cooldown del Scene Gate puede perder cambios de escena relevantes

## Evidencia
PENDIENTE: Completar con resultados del benchmark `scripts/benchmarks/benchmark_edge_profiles.py` ejecutado en RPi 5.

## Relación con specs
- Spec 003: Edge Pipeline Optimization (este ADR)
- Spec 001: Benchmark de línea base (referencia de comparación)
- Spec 007: Flujo de monitoreo (usa el perfil activo para configurar el worker)
