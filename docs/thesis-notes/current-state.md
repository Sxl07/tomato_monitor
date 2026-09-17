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

---

## 6. Dashboard analítico contextual (Spec 024)

**Estado:** implementado y verificado por tests automatizados; pendiente de prueba
física en Raspberry Pi.

El dashboard operativo se transformó en un **dashboard analítico contextual** que
permite observar la evolución del cultivo por invernadero/módulo usando únicamente
datos persistidos (sin inferencia ni ML en render).

### 6.1 Capacidades

- **Filtros contextuales:** selector de invernadero + selector de módulo
  (`Todos` / módulo específico) vía querystring (GET), sin fetch.
- **Scope "Todos":** agregación por el último monitoreo válido de cada módulo; las
  series de evolución se muestran como **una serie independiente por módulo** (no
  se inventa un total histórico del invernadero).
- **4 KPIs:** último monitoreo, frutos detectados (último válido, no suma
  histórica), estado sanitario (ponderado por conteos), madurez predominante
  (lista/"Mixto" en empate).
- **Series temporales por módulo:** evolución de frutos, sanidad (`pct_healthy`) y
  maturity index, sobre eje X de **fechas reales** (`started_at`), renderizadas en
  SVG inline local (sin librerías, sin CDN).
- **Semántica de cobertura de madurez:** los conteos por etapa y la cobertura se
  derivan de `InspectionResult`; los `pct_*` de `MonitoringMetrics` solo se usan
  como **fallback visual** cuando no hay `InspectionResult` (y no entran en el
  índice ni en la cosecha).
- **Maturity index** (promedio ordinal 0.0→1.0) y **harvestable_share** como
  magnitudes separadas y rotuladas.
- **Próxima ventana de cosecha:** WLS ponderado por cobertura, banda de residuales
  ponderada, cruce con `MI_TARGET = 0.8`; devuelve una ventana temporal (no una
  fecha exacta) con evidencia objetiva (nº de monitoreos, cobertura media). Ver
  `docs/decisions/ADR-006-maturity-index-and-harvest-window.md`.

### 6.2 Arquitectura

- Servicios puros sin BD: `AnalyticsService`, `dashboard_aggregation`,
  `dashboard_series`, `maturity_stats`, `maturity_index`, `harvest_estimator`,
  `dashboard_scope_builder`. La ruta `/dashboard` obtiene datos vía repositorios y
  los pasa a los servicios.
- **Aislamiento multiusuario (Spec 022):** los invernaderos provienen de
  `get_all_by_owner(user.id)`; módulos/monitoreos se resuelven descendiendo por
  FK. Un `greenhouse_id`/`module_id` ajeno se trata como no encontrado y cae a un
  scope propio seguro; sus ids nunca llegan a los repositorios bulk.
- **Repositorios bulk:** `MonitoringMetricsRepository.get_by_monitoring_ids` e
  `InspectionResultRepository.get_by_monitoring_ids` (SQL `IN (...)`), que
  sustituyen el patrón N+1 (1 query de métricas + 1 de resultados por monitoreo)
  por **2 consultas bulk** por render analítico.
- **Sin cambios de esquema** (SQLite/Supabase/RLS/sync/recovery intactos); solo se
  amplió la API de dos repositorios.

### 6.3 UI (Raspberry Pi DSI 800×480)

- Layout vertical con scroll: selectores → 4 KPIs (2×2) → tabs
  (Evolución/Sanidad/Madurez, una gráfica visible a la vez) → próxima cosecha →
  bloque operativo.
- **Offline:** sin CDN ni dependencias web nuevas; el único fetch sigue siendo
  `/api/sync/status` (badge de sincronización).
- **Preservación operativa:** alertas operativas (con contexto de invernadero),
  último monitoreo con links "Ver reporte"/"Ver ejecución", actividades recientes,
  métricas y badges se conservan dentro de `<details class="analytics-operational">`.
- JS local (`dashboard_analytics.js`): tabs, auto-submit de selectores y render
  SVG (solo geometría de presentación; no recalcula analítica).

### 6.4 Rendimiento (patrón de consultas por render de `/dashboard`)

Con G = invernaderos del usuario, M = módulos totales, M_s = módulos del scope,
C = monitoreos candidatos del scope:

- Operativo (sin cambios): `get_all_by_owner` (1) + `get_by_greenhouse` (G) +
  `get_by_module` (M) + `list_by_module` (M) + `list_all` tipos (1) +
  `list_by_user` exports (1).
- Analítico (Spec 024): `get_by_greenhouse` del scope (1 + 1 selector) +
  `get_by_module` (M_s) + **`get_by_monitoring_ids` métricas (1 bulk)** +
  **`get_by_monitoring_ids` resultados (1 bulk)**.

El N+1 de métricas/resultados por monitoreo quedó eliminado. Los `get_by_module`/
`list_by_module` por módulo del bloque operativo son preexistentes (flujo de
`DashboardService`), no introducidos por Spec 024; se documentan como
optimización futura opcional (deuda menor, sin impacto significativo en el
volumen esperado de un despliegue de invernadero).

### 6.5 Límite de puntos en gráficas SVG

Decisión: **no limitar** el número de puntos por ahora. El render es SVG ligero
sobre datos persistidos, sin inferencia, con un volumen de monitoreos por módulo
esperablemente bajo. Un eventual límite futuro sería una decisión de
**presentación** y no alteraría los cálculos históricos ni la tendencia WLS.

### 6.6 Verificación

- 240 tests dirigidos de Spec 024 en verde (DTOs, maturity stats/index,
  aggregation, series, harvest estimator, repos bulk, analytics service, scope
  builder, UI analítica, dashboard UI, dashboard service, alert service).
- Suite completa: los fallos restantes son preexistentes (tests de rutas basados
  en TestClient, ajenos a Spec 024); Spec 024 no introdujo regresiones y resolvió
  4 fallos de `test_dashboard_ui.py` cuyos mocks estaban obsoletos.
