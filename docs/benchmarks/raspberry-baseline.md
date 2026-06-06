# Raspberry Pi 5 Baseline Benchmark

## 1. Objetivo de la prueba

El objetivo de esta prueba fue validar si la aplicación actual de Tomato Monitor puede ejecutarse en una Raspberry Pi 5 con el pipeline existente, sin aplicar todavía optimizaciones ni refactorizaciones orientadas a edge.

Esta prueba busca establecer una primera línea base para identificar los principales cuellos de botella del sistema antes de tomar decisiones sobre optimización, integración de cámara en vivo o rediseño del pipeline.

---

## 2. Contexto del sistema evaluado

Tomato Monitor es una aplicación de monitoreo visual de tomates cherry basada en FastAPI y modelos de visión por computador. El pipeline actual está orientado principalmente al procesamiento offline de video.

El flujo evaluado incluye:

- Aplicación FastAPI.
- Carga de modelos de visión.
- Detector basado en Detectron2/RetinaNet.
- Clasificador de sanidad visual.
- Procesamiento de detecciones.
- Generación de crops.
- Generación de snapshots.
- Anotaciones visuales.
- Reconstrucción o generación de video anotado.
- Persistencia de resultados.

---

## 3. Hardware utilizado

| Componente | Estado |
|---|---|
| Raspberry Pi 5 | Validada |
| Fuente oficial 27W | Disponible |
| Case oficial con ventilador | Disponible |
| Raspberry Pi AI Camera | Validada a nivel de hardware |
| Pantalla táctil DSI 7" | Validada |
| microSD | En uso |

---

## 4. Estado del entorno

| Elemento | Resultado |
|---|---|
| Sistema operativo | Instalado y funcional |
| Entorno Python | Configurado |
| Dependencias base | Instaladas |
| FastAPI | Levanta correctamente |
| PyTorch / Torchvision | Instalados |
| Detectron2 | Instalado correctamente |
| Modelo de inferencia | Probado |
| Aplicación completa | Ejecutada en Raspberry Pi 5 |

---

## 5. Nota sobre instalación de Detectron2

Durante la preparación del entorno, Detectron2 fue instalado desde GitHub usando la opción:

```bash
pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
```

---

## 6. Identificación de la prueba y versiones de software

### 6.1 Identificación

| Campo | Valor |
|---|---|
| Nombre de la prueba | Raspberry Pi 5 Baseline Benchmark |
| Fecha | ⏳ Pendiente de ejecución en RPi 5 |
| Dispositivo | Raspberry Pi 5 (8 GB RAM) |
| Sistema operativo | Raspberry Pi OS / Debian Bookworm 64-bit |
| Versión de Python | ⏳ Pendiente de ejecución en RPi 5 |
| Git commit | ⏳ Pendiente de ejecución en RPi 5 |
| Branch | ⏳ Pendiente de ejecución en RPi 5 |

### 6.2 Versiones de software

| Componente | Versión |
|---|---|
| PyTorch | 2.10.0 |
| TorchVision | 0.25.0 |
| Detectron2 | Instalado desde GitHub (commit no registrado) |
| OpenCV | 4.13.0.92 |
| NumPy | 2.4.3 |
| psutil | ⏳ Pendiente de ejecución en RPi 5 |

> **Nota sobre Detectron2:** Se instaló desde el repositorio de GitHub sin fijar un commit específico (`pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'`). El hash del commit exacto no fue registrado al momento de la instalación. Este es un riesgo de reproducibilidad documentado en `docs/thesis-notes/limitations.md` y `docs/raspberry-setup.md`. Se recomienda registrar el commit hash al ejecutar `pip freeze` en la Fase 7.

---

## 7. Configuración de la prueba

| Parámetro | Valor |
|---|---|
| Tipo de entrada | Video offline (`data/videos/video_02.mp4`) |
| Resolución de entrada | ⏳ Pendiente de ejecución en RPi 5 |
| Número de frames | ⏳ Pendiente de ejecución en RPi 5 |
| Detector habilitado | Sí (Detectron2 RetinaNet R-50-FPN) |
| Clasificador de sanidad habilitado | Sí (ResNet-18) |
| Snapshots habilitados | No (benchmark puro) |
| Crops habilitados | No (benchmark puro) |
| Video anotado habilitado | No (benchmark puro) |
| Refrigeración activa | Sí (ventilador oficial en case RPi 5) |

---

## 8. Resultados

### 8.1 Fase 2 — Carga de modelos (`bench_model_load.py`)

Mide el tiempo de carga en frío (cold start) de cada modelo, sin ejecutar inferencia.

| Métrica | Valor |
|---|------:|
| Tiempo de carga del detector (Detectron2) | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo de carga del modelo de sanidad (ResNet-18) | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo de carga combinado | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura antes de carga del detector | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura después de carga del detector | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura antes de carga del modelo de sanidad | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura después de carga del modelo de sanidad | ⏳ Pendiente de ejecución en RPi 5 |

**Script:** `scripts/benchmarks/bench_model_load.py`

---

### 8.2 Fase 3 — Inferencia individual (`bench_single_inference.py`)

Mide los tiempos de cada etapa del pipeline para un solo frame, con los modelos ya cargados en memoria.

| Métrica | Valor |
|---|------:|
| Tiempo de preprocesamiento (lectura + validación del frame) | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo de inferencia del detector (`run_detection`) | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo de postprocesamiento (`extract_detection_dicts`) | ⏳ Pendiente de ejecución en RPi 5 |
| **Tiempo total de inferencia** | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo del clasificador de sanidad (`predict_health`) | ⏳ Pendiente de ejecución en RPi 5 |
| Detecciones encontradas | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura antes de la inferencia | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura después de la inferencia | ⏳ Pendiente de ejecución en RPi 5 |

**Script:** `scripts/benchmarks/bench_single_inference.py`

---

### 8.3 Fase 4 — Pipeline completo (`bench_full_video.py`)

Procesa el video completo usando el pipeline existente y mide rendimiento global, uso de recursos y comportamiento térmico.

| Métrica | Valor |
|---|------:|
| Video procesado | ⏳ Pendiente de ejecución en RPi 5 |
| Total de frames procesados | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo total de ejecución | ⏳ Pendiente de ejecución en RPi 5 |
| FPS promedio (wall-clock) | ⏳ Pendiente de ejecución en RPi 5 |
| FPS efectivo (pipeline) | ⏳ Pendiente de ejecución en RPi 5 |
| RAM pico | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura inicial | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura pico | ⏳ Pendiente de ejecución en RPi 5 |
| Temperatura final | ⏳ Pendiente de ejecución en RPi 5 |
| Ejecuciones del detector | ⏳ Pendiente de ejecución en RPi 5 |
| Frames omitidos (optical flow) | ⏳ Pendiente de ejecución en RPi 5 |
| Ratio de ejecución del detector | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo promedio por frame con detección | ⏳ Pendiente de ejecución en RPi 5 |
| Tiempo promedio por frame omitido | ⏳ Pendiente de ejecución en RPi 5 |
| Tracks únicos detectados | ⏳ Pendiente de ejecución en RPi 5 |
| Video anotado guardado | No |
| Snapshots guardados | No |

**Desglose de razones del Scene Gate:**

| Razón | Conteo |
|---|------:|
| first_frame | ⏳ Pendiente de ejecución en RPi 5 |
| scene_gate | ⏳ Pendiente de ejecución en RPi 5 |
| scene_gate_blocked | ⏳ Pendiente de ejecución en RPi 5 |
| max_gap_force | ⏳ Pendiente de ejecución en RPi 5 |
| min_gap_ready | ⏳ Pendiente de ejecución en RPi 5 |
| cooldown | ⏳ Pendiente de ejecución en RPi 5 |
| full_detection | ⏳ Pendiente de ejecución en RPi 5 |

**Script:** `scripts/benchmarks/bench_full_video.py`

---

## 9. Observaciones y conclusiones

> **Nota:** Las observaciones y conclusiones de esta sección se basan en los datos medidos durante los experimentos comparativos ejecutados en Raspberry Pi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa, procesando `data/videos/video_02.mp4` (163 frames). Los resultados provienen de `outputs/experiments/comparison_summary.csv`. Los benchmarks formales de carga de modelo (`bench_model_load.py`), inferencia individual (`bench_single_inference.py`) y pipeline completo (`bench_full_video.py`) permanecen pendientes de ejecución.

### 9.1 Observaciones

- El tiempo de inferencia del detector Detectron2 RetinaNet en CPU domina el costo total de procesamiento. En los frames donde se ejecutó el detector, el tiempo promedio osciló entre 5.32 s y 6.42 s según la estrategia y la ejecución.
- La estrategia `full_detection` (detección en cada frame) alcanzó un FPS efectivo de 0.17, con un tiempo promedio por frame de 5.82 s. Esto confirma que el sistema está muy lejos de operación en tiempo real cuando se ejecuta el detector en cada frame.
- El mecanismo Scene Gate redujo las invocaciones del detector del 100% al ~8% de los frames (13 de 163), lo que constituye una reducción de ~92% en ejecuciones costosas.
- Las estrategias sparse (`sparse_honest` y `sparse_flow`) lograron un FPS efectivo de 2.01 y 1.80 respectivamente, representando una ganancia de aproximadamente 10–12× respecto a `full_detection`.
- La propagación por Optical Flow Lucas-Kanade añade un costo marginal: 0.048 s por frame omitido (estrategia `sparse_flow`) vs. 0.019 s en la estrategia `sparse_honest` sin propagación. El overhead del flujo óptico es de ~0.026 s por frame propagado.
- La configuración candidata `sparse_flow_candidate` (min5_max12_gate1_flow1) alcanzó el FPS efectivo más alto medido: 2.10 FPS, con un tiempo promedio de detección de 5.32 s por frame de detección.
- El conteo de tracks únicos detectados disminuyó de 29 (estrategia `full_detection`) a 16 (estrategias sparse). Esto indica que el muestreo sparse pierde ~45% de los tracks detectables al no ejecutar el detector en todos los frames.
- Las ejecuciones de clasificación de sanidad y estimación de madurez se redujeron proporcionalmente al número de tracks: 28/26 en `full_detection` vs. 16/13 en las estrategias sparse.
- El pico de RAM observado durante la ejecución del pipeline completo fue de ~2.1 GB sobre 8 GB disponibles. La RAM no constituye un factor limitante.
- El desglose de razones del Scene Gate en la configuración candidata fue: `first_frame=1`, `scene_gate_blocked=85`, `max_gap_force=12`, `cooldown=65`. Esto indica que la mayoría de frames fueron bloqueados correctamente por el gate (85 frames) o por el cooldown (65 frames).

### 9.2 Errores o anomalías

- Se observó variación en el tiempo promedio de inferencia del detector entre estrategias: 5.82 s (`full_detection`), 6.04 s (`sparse_honest`), 6.42 s (`sparse_flow`) y 5.32 s (`sparse_flow_candidate`). La diferencia máxima es de ~1.1 s entre ejecuciones. Esta variación podría explicarse por condiciones térmicas diferentes entre las ejecuciones (throttling térmico del SoC) o por diferencias en la complejidad visual de los 13 frames seleccionados por el Scene Gate en cada run.
- No se registraron errores de ejecución, fallos de modelos ni interrupciones durante los experimentos.
- Los valores de temperatura no fueron registrados en estos experimentos comparativos. Los datos térmicos formales quedan pendientes para los benchmarks dedicados (`bench_model_load.py`, `bench_single_inference.py`, `bench_full_video.py`).

### 9.3 Conclusiones

- **Detectron2 RetinaNet en RPi 5 CPU no es viable para uso en tiempo real.** Con un FPS efectivo de 0.17 en detección completa y un máximo de 2.10 con Scene Gate activo, el sistema opera muy por debajo de los requisitos de tiempo real (≥15–30 FPS).
- **El Scene Gate es un mecanismo crítico para la operación práctica.** Reduce las ejecuciones del detector en ~92% y mejora el rendimiento en un factor de 10–12×. Sin este mecanismo, el procesamiento de un video de 163 frames tomaría ~15.8 minutos; con Scene Gate, se reduce a ~1.4 minutos.
- **La RAM no es una restricción operativa.** El consumo observado (~2.1 GB de 8 GB) deja amplio margen. El cuello de botella dominante es la latencia de inferencia en CPU.
- **El flujo óptico aporta continuidad de tracking con costo marginal** (~0.026 s por frame propagado), pero no mejora el FPS general de forma significativa respecto al skip simple.
- **Existe un trade-off entre cobertura de detección y rendimiento.** Las estrategias sparse pierden ~45% de los tracks detectables. La selección de parámetros del Scene Gate determina el equilibrio entre exhaustividad y velocidad.
- **Se requiere reemplazo del modelo o aceleración por hardware para alcanzar operación en tiempo real.** Las alternativas incluyen: modelos más livianos (YOLO, MobileNet-SSD), cuantización, o uso del NPU de la AI Camera (IMX500). Cualquier decisión de reemplazo debe estar respaldada por evidencia cuantitativa comparativa (ver ADR-003).
- **Los datos medidos en estos experimentos proporcionan justificación cuantitativa para las decisiones de optimización** documentadas en ADR-003. Sin embargo, los benchmarks formales individuales (carga de modelos, inferencia single-frame, pipeline completo con métricas térmicas) permanecen pendientes de ejecución con los scripts dedicados.

---

## 10. Próximas acciones

| # | Acción | Dependencia |
|---|---|---|
| 1 | Ejecutar los tres scripts de benchmark en RPi 5 con refrigeración activa | Hardware disponible |
| 2 | Registrar valores reales en las tablas de la sección 8 | Ejecución completada |
| 3 | Redactar observaciones y conclusiones basadas en métricas medidas | Resultados disponibles |
| 4 | Actualizar `requirements-raspberry.txt` con versiones exactas (`pip freeze`) | Entorno validado |
| 5 | Actualizar ADR-001 y ADR-003 con evidencia cuantitativa del benchmark | Conclusiones redactadas |
| 6 | Evaluar viabilidad de Detectron2 para uso en tiempo real según FPS medido | Conclusiones redactadas |

---

## Referencias

- Plantilla de benchmark: `docs/benchmarks/benchmark-template.md`
- ADR-001 — Detectron2 en Raspberry Pi: `docs/decisions/ADR-001-detectron2-on-raspberry.md`
- ADR-003 — Benchmark-first: `docs/decisions/ADR-003-edge-benchmark-first.md`
- Scripts de benchmark: `scripts/benchmarks/`
