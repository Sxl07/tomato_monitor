# Estado actual del proyecto

**Última actualización:** junio 2026

---

## 1. Contexto general

Tomato Monitor es un sistema de monitoreo visual de tomates cherry orientado a despliegue edge en Raspberry Pi 5. El sistema integra un pipeline de visión por computador (detección, tracking, clasificación de sanidad y estimación de madurez) con una interfaz web construida en FastAPI. El proyecto se desarrolla en el marco de una tesis de pregrado/posgrado y tiene como objetivo evaluar la viabilidad técnica del procesamiento visual en hardware edge de bajo costo.

---

## 2. Estado de la infraestructura de software

### 2.1 Aplicación web

- La aplicación FastAPI levanta correctamente tanto en PC (x86-64) como en Raspberry Pi 5 (ARM64/aarch64).
- El servidor se inicia mediante `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- La interfaz web permite configurar y ejecutar el pipeline, visualizar sesiones previas, revisar snapshots anotados y descargar reportes CSV.
- El proyecto está versionado en GitHub y sincronizado con el entorno de desarrollo.

### 2.2 Pipeline de visión

El pipeline procesa video offline frame a frame e integra los siguientes componentes en cadena:

| Componente | Tecnología | Estado |
|---|---|---|
| Detección de frutos | Detectron2 / RetinaNet R-50-FPN | Funcional en RPi |
| Tracking multi-objeto | SimpleTracker (IoU + distancia centroide) | Funcional |
| Propagación entre frames | Optical Flow Lucas-Kanade | Funcional |
| Clasificación de sanidad | ResNet-18 fine-tuned (sano/enfermo) | Funcional |
| Estimación de madurez | GrabCut + colorimetría HSV/CIELab (escala USDA) | Funcional |
| Compuerta de escena | Scene Gate (ORB features + histograma HSV) | Funcional |
| Persistencia | CSV + sistema de archivos local | Funcional |

### 2.3 Arquitectura de software

El proyecto sigue Clean Architecture con cuatro capas: dominio, aplicación, infraestructura y presentación. El código fuente activo reside en `src/`. El código anterior del pipeline fue migrado íntegramente a `src/infrastructure/vision/` y se mantiene en `legacy/` únicamente como referencia histórica.

---

## 3. Estado del hardware

### 3.1 Hardware disponible y validado

| Componente | Estado |
|---|---|
| Raspberry Pi 5 | Armada, configurada y validada |
| Fuente oficial 27W | Instalada |
| Case oficial con ventilador activo | Instalado — refrigeración activa obligatoria |
| Raspberry Pi AI Camera | Conectada y funcional |
| Pantalla táctil DSI 7" | Funcional, resolución configurada |
| microSD | Sistema operativo instalado |
| Chasis robótico | Ensamblado (motores y estructura) |

### 3.2 Entorno de software en Raspberry Pi

- Sistema operativo: Raspberry Pi OS / Debian Bookworm (64-bit)
- Python: entorno virtual creado con `python3-venv`
- Dependencias del sistema instaladas: `build-essential`, `cmake`, `libgl1`, `libglib2.0-0`, `libjpeg-dev`, `libpng-dev`, `libopenblas-dev`, entre otras
- Detectron2 instalado desde GitHub con `pip install --no-build-isolation`
- Modelos de detección y sanidad cargados y probados exitosamente

---

## 4. Resultados medidos en Raspberry Pi (Spec 001 — Benchmark de línea base)

### 4.1 Rendimiento del pipeline completo

Datos medidos en RPi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa, procesando `data/videos/video_02.mp4` (163 frames). Fuente: `outputs/experiments/comparison_summary.csv` y `docs/benchmarks/raspberry-baseline.md`.

| Métrica | Valor medido |
|---|---|
| FPS efectivo — `full_detection` (detector en cada frame) | 0.17 FPS |
| FPS efectivo — `sparse_flow` (Scene Gate + Optical Flow) | 1.80 FPS |
| FPS efectivo — `sparse_flow_candidate` (min5/max12 + flow) | 2.10 FPS |
| Tiempo promedio de inferencia del detector por frame | 5.32–6.42 s |
| Tiempo promedio por frame omitido (Optical Flow) | ~0.048 s |
| Reducción de invocaciones del detector (Scene Gate) | ~92% (13 de 163 frames) |
| RAM pico durante pipeline completo | ~2.1 GB de 8 GB disponibles |
| Tracks únicos — `full_detection` | 29 |
| Tracks únicos — estrategias sparse | 16 |
| Cuello de botella principal | Latencia de inferencia del detector en CPU |

### 4.2 Conclusiones del benchmark

- El sistema es funcional en RPi 5 con el pipeline completo (detección + sanidad + madurez + video anotado).
- La Raspberry Pi 5 cuenta con RAM suficiente (~2.1 GB de 8 GB disponibles); la RAM no es factor limitante.
- El principal factor limitante es la latencia de inferencia de Detectron2 en CPU (~5.3–6.4 s por frame).
- El Scene Gate reduce las invocaciones del detector en ~92%, logrando una mejora de ~10–12× en FPS efectivo.
- La refrigeración activa (ventilador del case oficial) es condición necesaria para pruebas extendidas.
- Las estrategias sparse pierden cobertura de detección (~45% menos tracks únicos respecto a detección completa).
- Los valores de temperatura no fueron registrados formalmente en estos experimentos; quedan pendientes para una ejecución dedicada con `bench_full_video.py`.

---

## 5. Deuda técnica identificada

- `requirements-raspberry.txt` está vacío: no existe un conjunto de dependencias pinadas y validadas para ARM64.
- Los modelos no tienen metadatos documentados (fecha de entrenamiento, dataset, métricas de validación).
- La instancia de `PipelineService` se crea en cada request HTTP, lo que implica recarga de modelos en memoria ante cada ejecución desde la UI.
- No existe suite de tests automatizados; solo hay smoke tests individuales por componente.
- Varios documentos clave están vacíos: ADRs, benchmarks, próximos pasos.
