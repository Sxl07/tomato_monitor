# ADR-001: Uso de Detectron2 en Raspberry Pi como línea base

## Estado

Aceptada — vigente para la fase de benchmarking inicial.

---

## Contexto

El proyecto utiliza Detectron2 con arquitectura RetinaNet R-50-FPN como detector principal de tomates cherry. El detector fue entrenado con un dataset personalizado y produce un modelo `.pth` que se carga en CPU.

Al iniciar las pruebas en Raspberry Pi 5, se planteó la pregunta: ¿se debe reemplazar Detectron2 por un detector más liviano antes de hacer las primeras pruebas, o conviene medir primero el rendimiento del sistema tal como está?

Detectron2 fue diseñado para entornos con GPU y hardware de escritorio. Su uso en hardware edge (ARM64, CPU solamente) presenta desafíos conocidos:

- Instalación compleja: no hay wheels precompilados para ARM64; debe compilarse desde fuente.
- Alto consumo de CPU durante inferencia.
- Dependencias pesadas (PyTorch, fvcore, pycocotools).

Sin embargo, reemplazar el detector antes de medir implica perder la referencia comparativa con los resultados obtenidos en PC y arriesgar introducir errores de regresión en el pipeline.

---

## Decisión

Se mantiene Detectron2 para la fase de benchmarking inicial en Raspberry Pi 5. No se reemplazará hasta contar con métricas cuantitativas de rendimiento (FPS, CPU, temperatura) que justifiquen la migración.

La instalación se realizó mediante:

```bash
pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
```

Esta forma de instalación compila Detectron2 directamente desde el código fuente en la Raspberry Pi, evitando el problema de ausencia de wheels para ARM64.

---

## Consecuencias

### Positivas

- Permite comparar el rendimiento entre PC (x86-64) y Raspberry Pi (ARM64) con el mismo modelo y pipeline, generando evidencia directamente utilizable en la tesis.
- Evita refactorización prematura antes de tener métricas que la justifiquen (principio benchmark-first, ver ADR-003).
- El pipeline existente no requiere modificaciones para ejecutar en RPi.
- La instalación fue exitosa y el modelo ejecuta correctamente en CPU.

### Negativas

- Proceso de instalación lento (~30-60 minutos de compilación en RPi 5) y no reproducible sin conexión a internet.
- Alto uso de CPU durante inferencia con calentamiento significativo del SoC.
- FPS esperados bajos para el pipeline completo.
- La versión instalada desde GitHub puede diferir entre instalaciones si no se fija el commit.

### Riesgos

- Si la temperatura supera el umbral de throttling del SoC, el rendimiento medido puede no ser representativo del rendimiento nominal. Requiere refrigeración activa obligatoria durante todas las pruebas.
- `requirements-raspberry.txt` aún no documenta las versiones exactas de PyTorch y Detectron2 utilizadas, lo que reduce la reproducibilidad del entorno.

---

## Evidencia

- Detectron2 instalado exitosamente con `--no-build-isolation` en Raspberry Pi OS 64-bit.
- Pipeline completo ejecuta (detección, crops, sanidad, madurez, snapshots, video anotado).
- RAM máxima observada durante pipeline completo: ~2.1 GB.
- CPU con carga alta sostenida durante inferencia.
- Temperatura con aumento significativo; refrigeración activa obligatoria para pruebas extendidas.
- FPS reales del pipeline completo en RPi: **pendiente de medición formal** (ver `docs/benchmarks/raspberry-baseline.md`).

---

## Alternativas consideradas

| Alternativa | Razón de descarte |
|---|---|
| Reemplazar por YOLOv8n antes del benchmark | Introduce cambios antes de tener línea base; impide comparación directa |
| Usar modelo ONNX exportado | Requiere re-entrenamiento o conversión; riesgo de diferencias en precisión |
| Ejecutar solo en PC y no en RPi | Contradice el objetivo principal del proyecto (edge deployment) |
