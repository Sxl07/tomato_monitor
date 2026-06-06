# Design - Raspberry Baseline Benchmark

## Enfoque

Crear scripts independientes de benchmark para no alterar el pipeline principal. Los scripts deben importar componentes existentes y medir tiempos sin modificar comportamiento funcional.

## Componentes

- `scripts/benchmarks/bench_model_load.py`
- `scripts/benchmarks/bench_single_inference.py`
- `scripts/benchmarks/bench_full_video.py`
- `scripts/benchmarks/system_monitor.py`
- `docs/benchmarks/raspberry-baseline.md`

## Métricas

- Tiempo de carga del detector.
- Tiempo de carga del clasificador.
- Tiempo de inferencia.
- Tiempo total por frame.
- FPS aproximado.
- RAM máxima.
- Temperatura inicial, máxima y final.
- Errores.

## Restricciones

- No cambiar lógica del pipeline.
- No eliminar artefactos actuales.
- No integrar cámara en vivo todavía.