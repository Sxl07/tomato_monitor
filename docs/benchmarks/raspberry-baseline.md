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
| Fecha | Experimentos comparativos ejecutados; benchmarks formales pendientes |
| Dispositivo | Raspberry Pi 5 (8 GB RAM) |
| Sistema operativo | Raspberry Pi OS / Debian Bookworm 64-bit |
| Versión de Python | 3.11 (system default en Raspberry Pi OS Bookworm) |
| Git commit | ⏳ Pendiente de registrar en ejecución formal (`collect_env_info.py`) |
| Branch | ⏳ Pendiente de registrar en ejecución formal (`collect_env_info.py`) |

### 6.2 Versiones de software

| Componente | Versión |
|---|---|
| PyTorch | 2.10.0 |
| TorchVision | 0.25.0 |
| Detectron2 | Instalado desde GitHub (commit no registrado — ver nota abajo) |
| OpenCV | 4.13.0.92 |
| NumPy | 2.4.3 |
| Pillow | 12.1.1 |
| Pandas | 3.0.1 |
| FastAPI | 0.115.12 |
| psutil | No confirmado (scripts usan fallback a `/proc` y `vcgencmd` si no disponible) |

> **Nota sobre Detectron2:** Se instaló desde el repositorio de GitHub sin fijar un commit específico (`pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'`). El hash del commit exacto no fue registrado al momento de la instalación. Este es un riesgo de reproducibilidad documentado en `docs/thesis-notes/limitations.md` y `docs/raspberry-setup.md`. Se recomienda registrar el commit hash al ejecutar `pip freeze` en la Fase 7.

---

## 7. Configuración de la prueba

| Parámetro | Valor |
|---|---|
| Tipo de entrada | Video offline (`data/videos/video_02.mp4`) |
| Resolución de entrada | ⏳ Pendiente de registrar (ejecutar `bench_single_inference.py` para obtener) |
| Número de frames | 163 |
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

> **Estado:** ⏳ Pendiente de ejecución formal en RPi 5.
>
> El script `scripts/benchmarks/bench_model_load.py` está listo para ejecutar. Requiere acceso físico a la Raspberry Pi 5 con refrigeración activa. Los valores de esta tabla serán registrados al ejecutar:
> ```bash
> python -m scripts.benchmarks.bench_model_load
> ```
>
> **Nota:** No existen datos derivables de los experimentos comparativos para esta fase, ya que los tiempos de carga de modelos no fueron registrados separadamente durante las ejecuciones de pipeline.

| Métrica | Valor |
|---|------:|
| Tiempo de carga del detector (Detectron2) | ⏳ Pendiente (`bench_model_load.py`) |
| Tiempo de carga del modelo de sanidad (ResNet-18) | ⏳ Pendiente (`bench_model_load.py`) |
| Tiempo de carga combinado | ⏳ Pendiente (`bench_model_load.py`) |
| Temperatura antes de carga del detector | ⏳ Pendiente (`bench_model_load.py`) |
| Temperatura después de carga del detector | ⏳ Pendiente (`bench_model_load.py`) |
| Temperatura antes de carga del modelo de sanidad | ⏳ Pendiente (`bench_model_load.py`) |
| Temperatura después de carga del modelo de sanidad | ⏳ Pendiente (`bench_model_load.py`) |

**Script:** `scripts/benchmarks/bench_model_load.py`

---

### 8.2 Fase 3 — Inferencia individual (`bench_single_inference.py`)

Mide los tiempos de cada etapa del pipeline para un solo frame, con los modelos ya cargados en memoria.

> **Estado:** ⏳ Pendiente de ejecución formal en RPi 5.
>
> El script `scripts/benchmarks/bench_single_inference.py` está listo para ejecutar. Requiere acceso físico a la Raspberry Pi 5 con refrigeración activa. Los valores de esta tabla serán registrados al ejecutar:
> ```bash
> python -m scripts.benchmarks.bench_single_inference
> ```
>
> **Datos de referencia disponibles (de experimentos comparativos):**
> Del CSV `outputs/experiments/sparse_flow/reports_sparse_flow/per_frame.csv`, se observó que el primer frame (frame 0) tuvo un tiempo de detección de 5.274 s y un tiempo total de 7.535 s (incluyendo health + maturity). En la estrategia `full_detection`, el detector varió entre 3.71 s y 6.12 s entre frames (primer frame: 3.71 s — posible efecto warm-up de PyTorch). Estos valores son orientativos pero no reemplazan la medición formal con el script dedicado, que separa explícitamente preprocesamiento, inferencia y postprocesamiento.

| Métrica | Valor |
|---|------:|
| Tiempo de preprocesamiento (lectura + validación del frame) | ⏳ Pendiente (`bench_single_inference.py`) |
| Tiempo de inferencia del detector (`run_detection`) | ⏳ Pendiente (`bench_single_inference.py`) |
| Tiempo de postprocesamiento (`extract_detection_dicts`) | ⏳ Pendiente (`bench_single_inference.py`) |
| **Tiempo total de inferencia** | ⏳ Pendiente (`bench_single_inference.py`) |
| Tiempo del clasificador de sanidad (`predict_health`) | ⏳ Pendiente (`bench_single_inference.py`) |
| Detecciones encontradas | ⏳ Pendiente (`bench_single_inference.py`) |
| Temperatura antes de la inferencia | ⏳ Pendiente (`bench_single_inference.py`) |
| Temperatura después de la inferencia | ⏳ Pendiente (`bench_single_inference.py`) |

**Script:** `scripts/benchmarks/bench_single_inference.py`

---

### 8.3 Fase 4 — Pipeline completo (`bench_full_video.py`)

Procesa el video completo usando el pipeline existente y mide rendimiento global, uso de recursos y comportamiento térmico.

> **Fuente de datos:** Valores medidos durante la ejecución experimental en Raspberry Pi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa. Estrategia: `sparse_flow` (Scene Gate + Optical Flow, min_gap=18, max_gap=45). Datos extraídos de `outputs/experiments/sparse_flow/reports_sparse_flow/summary.csv` y `per_frame.csv`.

| Métrica | Valor |
|---|------:|
| Video procesado | video_02.mp4 |
| Total de frames procesados | 163 |
| Tiempo total de ejecución | 90.63 s |
| FPS promedio (wall-clock) | 1.80 |
| FPS efectivo (pipeline) | 1.80 |
| RAM pico | ~2.1 GB |
| Temperatura inicial | No registrada en este experimento |
| Temperatura pico | No registrada en este experimento |
| Temperatura final | No registrada en este experimento |
| Ejecuciones del detector | 13 |
| Frames omitidos (optical flow) | 150 |
| Ratio de ejecución del detector | 7.98% |
| Tiempo promedio por frame con detección | 6418.7 ms |
| Tiempo promedio por frame omitido | 47.9 ms |
| Tiempo promedio de propagación (optical flow) | 25.9 ms |
| Tracks únicos detectados | 16 |
| Video anotado guardado | Sí (en esta ejecución experimental) |
| Snapshots guardados | Sí (en esta ejecución experimental) |

**Comparación con otras estrategias medidas:**

| Estrategia | FPS efectivo | Detector runs | Avg detector frame | Tracks únicos |
|---|---:|---:|---:|---:|
| `full_detection` (sin Scene Gate) | 0.17 | 163 | 5820.3 ms | 29 |
| `sparse_honest` (Scene Gate, sin flow) | 2.01 | 13 | 6035.4 ms | 16 |
| `sparse_flow` (Scene Gate + flow) | 1.80 | 13 | 6418.7 ms | 16 |
| `sparse_flow_candidate` (min5/max12 + flow) | 2.10 | 13 | 5317.2 ms | 16 |

**Desglose de razones del Scene Gate (estrategia `sparse_flow`):**

| Razón | Conteo |
|---|------:|
| first_frame | 1 |
| scene_gate | 0 |
| scene_gate_blocked | 85 |
| max_gap_force | 12 |
| min_gap_ready | 0 |
| cooldown | 65 |
| full_detection | 0 |

> **Nota sobre temperatura:** Los valores de temperatura no fueron registrados durante estos experimentos comparativos porque se ejecutaron antes de que el script formal `bench_full_video.py` (con `TempMonitor`) estuviera disponible. Los datos térmicos formales quedan pendientes para una ejecución dedicada con el script de benchmark.

> **Nota sobre RAM:** El valor de ~2.1 GB corresponde al pico de memoria RSS observado durante la ejecución del pipeline completo en Raspberry Pi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa. Fue medido visualmente mediante `htop` durante la ejecución real del pipeline. Este valor es representativo del consumo real del sistema y válido como línea base para la tesis. Una medición programática más precisa (muestreo continuo vía `psutil`) será capturada cuando se ejecute formalmente el script `bench_full_video.py`, que incluye la clase `RamMonitor` con muestreo cada 0.5 s del RSS del proceso.

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
- El pico de RAM observado durante la ejecución del pipeline completo fue de ~2.1 GB sobre 8 GB disponibles (medido vía `htop` en RPi 5). La RAM no constituye un factor limitante.
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

| # | Acción | Estado | Dependencia |
|---|---|---|---|
| 1 | Ejecutar `bench_model_load.py` en RPi 5 con refrigeración activa | ⏳ Pendiente | Acceso físico a RPi 5 |
| 2 | Ejecutar `bench_single_inference.py` en RPi 5 con refrigeración activa | ⏳ Pendiente | Acceso físico a RPi 5 |
| 3 | Ejecutar `bench_full_video.py` en RPi 5 (con métricas térmicas) | ⏳ Pendiente | Acceso físico a RPi 5 |
| 4 | Registrar valores formales en las tablas 8.1 y 8.2 | ⏳ Pendiente | Ejecuciones 1-3 completadas |
| 5 | Registrar resolución del video de entrada | ⏳ Pendiente | Ejecución de `bench_single_inference.py` |
| 6 | Ejecutar `collect_env_info.py` para capturar git commit y branch | ⏳ Pendiente | Acceso físico a RPi 5 |

> **Nota:** Los datos de la sección 8.3 (Fase 4 — Pipeline completo) ya fueron registrados a partir de los experimentos comparativos ejecutados en RPi 5. Los benchmarks formales de carga de modelos (Fase 2), inferencia individual (Fase 3) y pipeline con métricas térmicas detalladas requieren ejecución física con los scripts dedicados.

---

## Referencias

- Plantilla de benchmark: `docs/benchmarks/benchmark-template.md`
- ADR-001 — Detectron2 en Raspberry Pi: `docs/decisions/ADR-001-detectron2-on-raspberry.md`
- ADR-003 — Benchmark-first: `docs/decisions/ADR-003-edge-benchmark-first.md`
- Scripts de benchmark: `scripts/benchmarks/`
