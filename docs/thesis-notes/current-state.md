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

## 4. Resultados de pruebas iniciales en Raspberry Pi

### 4.1 Observaciones de rendimiento

| Métrica | Observación |
|---|---|
| RAM máxima durante pipeline completo | ~2.1 GB |
| CPU | Carga alta sostenida durante inferencia |
| Temperatura | Aumento significativo; refrigeración activa obligatoria |
| Cuello de botella principal | CPU / temperatura (no RAM) |

### 4.2 Conclusiones preliminares

- El sistema es funcional en RPi 5 con el pipeline completo (detección + sanidad + madurez + video anotado).
- La Raspberry Pi 5 cuenta con RAM suficiente para ejecutar el pipeline (~2.1 GB de 8 GB disponibles).
- El principal factor limitante es la carga de CPU sostenida y el calor resultante, no la memoria.
- La refrigeración activa (ventilador del case oficial) es condición necesaria para pruebas extendidas.
- No se han medido aún FPS reales del pipeline completo en RPi; este dato es el objetivo del benchmark de línea base.

---

## 5. Deuda técnica identificada

- `requirements-raspberry.txt` está vacío: no existe un conjunto de dependencias pinadas y validadas para ARM64.
- Los modelos no tienen metadatos documentados (fecha de entrenamiento, dataset, métricas de validación).
- La instancia de `PipelineService` se crea en cada request HTTP, lo que implica recarga de modelos en memoria ante cada ejecución desde la UI.
- No existe suite de tests automatizados; solo hay smoke tests individuales por componente.
- Varios documentos clave están vacíos: ADRs, benchmarks, próximos pasos.
