# Documento de Requisitos de Bugfix — SPEC 026: Continuidad de Flujo del Tracker

## Introduction

Tomato Monitor ejecuta análisis diferido offline de video en Raspberry Pi 5. El pipeline de visión es: RetinaNet → SimpleTracker → clasificación de sanidad → estimación de madurez → `best_by_track` → métricas finales. El análisis es **disperso (sparse)**: RetinaNet NO se ejecuta en cada frame. En los frames intermedios (donde el detector no corre) se emplea un `OpticalFlowVisualTracker` (Lucas-Kanade) cuya función es **propagar espacialmente** las posiciones de los tracks entre inferencias reales.

**Cadencia dispersa real (perfil EDGE):** la configuración vigente es `sparse_min_frames_between_detections = 1`, `sparse_max_frames_without_detection = 4` y `camera_fps = 5`. Es decir, hay al menos 1 frame disperso entre detecciones y se fuerza una detección tras 4 frames sin ella. (Cualquier referencia previa a una cadencia de "3–7 frames" era incorrecta y queda corregida por estos valores.)

Este documento describe un defecto confirmado por lectura de código y reproducido físicamente en Raspberry Pi (Monitoreo 55): la información espacial que calcula el Optical Flow **nunca se reincorpora** al estado espacial de `SimpleTracker`. En consecuencia, cuando el detector vuelve a ejecutarse tras varios frames dispersos, `SimpleTracker` compara las nuevas detecciones contra posiciones **desactualizadas** (la última posición de detección real), lo que produce **fragmentación de identidad** (un mismo tomate físico recibe distintos `track_id` a lo largo del video) y **sobreconteo** (el mismo tomate se cuenta varias veces mientras otros se pierden).

**Impacto:** la métrica `unique_tomatoes` deja de ser correcta por identidad. En el Monitoreo 55, con ~17 tomates físicos, el sistema reportó `unique_tomatoes=16`, pero ese valor solo se aproxima al real por compensación accidental (pérdidas que cancelan duplicados), no por conteo correcto por identidad. Los snapshots anotados evidenciaron que los mismos tomates físicos recibían `track_id` distintos conforme avanzaba el video.

**Condición del defecto C(X):** la ruptura de continuidad de identidad de los tracks entre inferencias no consecutivas de RetinaNet. Tiene **dos facetas** (ver definición formal): (1) desincronización espacial entre `OpticalFlowVisualTracker` y `SimpleTracker` durante frames dispersos; (2) destrucción del track visual cuando RetinaNet omite un track que sigue vivo en `SimpleTracker`.

**Invariante de coherencia temporal (confirmada en código):** `OpticalFlowVisualTracker` mantiene `self.prev_gray` y cada `VisualTrackState` mantiene `points` + `bbox`. `propagate()` ejecuta `cv2.calcOpticalFlowPyrLK(self.prev_gray, curr_gray, track.points, ...)`, por lo que `track.points` DEBE corresponder espacialmente al mismo frame representado por `self.prev_gray`. Además, `propagate(frame)` actualiza AMBOS: `self.tracks` (con `points`/`bbox` avanzados a `curr_gray`) y `self.prev_gray = curr_gray`. Cualquier corrección que retenga un `VisualTrackState` antiguo sin re-sincronizar `points`/`bbox` al frame donde queda `prev_gray` **rompe** esta invariante y corrompe el siguiente `propagate()`.

### Causa raíz confirmada (verificada en código)

- `src/application/services/video_analysis_service.py`: en la rama dispersa `else` (cuando el detector NO se ejecuta), se invoca `flow_tracker.propagate(frame)` y **se descarta** la lista de resultados propagados que retorna. Los bounding boxes propagados por Optical Flow nunca llegan a `SimpleTracker`.
- `src/infrastructure/vision/tracker_adapter.py`: `SimpleTracker.update()` asocia detecciones contra `track.bbox`, que **solo** se actualiza en el momento de una detección real. `Track.update()` fija `bbox`, incrementa `hits` y resetea `missed`. **No existe** ningún método que actualice la posición espacial de un track sin crear un track nuevo ni incrementar `hits`.
- Efecto: detección real en frame A → `SimpleTracker` guarda `bbox` A → frames dispersos → Optical Flow calcula `bbox` B, C, D… que nunca actualizan `SimpleTracker` → siguiente frame de RetinaNet → `SimpleTracker` compara la nueva detección contra el `bbox` A antiguo → los umbrales (`iou_threshold=0.30`, `center_distance_threshold=120px`, `max_missed=3`) pueden fallar → se genera un `track_id` nuevo para el mismo tomate físico → fragmentación y sobreconteo.

### Definición formal del defecto

**F**: función de análisis actual (sin corregir), donde `flow_tracker.propagate(frame)` se llama y su retorno se descarta.
**F'**: función de análisis corregida, donde la posición propagada por Optical Flow se reincorpora al estado espacial de `SimpleTracker` antes de la siguiente asociación de detecciones.

La condición del defecto es la **unión** de dos sub-condiciones, una por faceta:

```pascal
// Faceta 1 — desincronización espacial (retorno de propagate descartado)
FUNCTION isSpatialDesyncCondition(X)
  INPUT: X = secuencia de frames analizada con detección dispersa
  OUTPUT: boolean

  // Verdadero cuando un mismo tomate físico persiste a través de un ciclo
  // detección_real → frames_dispersos_con_movimiento → detección_real,
  // Optical Flow lo siguió, pero el estado espacial de SimpleTracker sigue
  // congelado en la bbox de la detección anterior (el retorno de propagate()
  // se descartó) → la asociación falla → fragmentación.
  RETURN existe_track_real_en_frame_A(X)
     AND hay_frames_dispersos_con_movimiento_entre_A_y_B(X)
     AND optical_flow_siguio_el_track(X)
     AND simpleTracker_conserva_bbox_de_A_en_frame_B(X)
END FUNCTION
```

```pascal
// Faceta 2 — continuidad ante omisión del detector
FUNCTION isDetectorMissContinuityCondition(X)
  INPUT: X = secuencia de frames analizada con detección dispersa
  OUTPUT: boolean

  // Verdadero cuando un track vivo en SimpleTracker (missed <= max_missed) es
  // OMITIDO por RetinaNet en un frame de detección programado; el track visual
  // se destruye (self.tracks = new_tracks) y deja de propagarse; cuando RetinaNet
  // lo vuelve a detectar, la asociación falla → fragmentación.
  RETURN track_vivo_en_simpleTracker(X)          // missed <= max_missed
     AND retinaNet_omite_el_track_en_frame_deteccion(X)
     AND track_visual_destruido_y_sin_propagacion(X)
     AND reasociacion_falla_al_reaparecer(X)
END FUNCTION
```

```pascal
// Condición combinada del defecto (unión de ambas facetas)
FUNCTION isBugCondition(X)
  RETURN isSpatialDesyncCondition(X)
      OR isDetectorMissContinuityCondition(X)
END FUNCTION
```

```pascal
// Propiedad: Fix Checking — continuidad de identidad
FOR ALL X WHERE isBugCondition(X) DO
  result ← F'(X)
  ASSERT el_mismo_tomate_fisico_conserva_su_track_id(result)
     AND best_by_track_tiene_una_sola_entrada_por_tomate(result)
     AND unique_tomatoes_cuenta_cada_tomate_una_sola_vez(result)
END FOR
```

```pascal
// Propiedad: Preservation Checking — no regresión
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Nota de alcance:** el defecto NO se resuelve cambiando el umbral de RetinaNet (`detection_score_threshold=0.50` en perfil EDGE fue calibrado experimentalmente y NO debe cambiar). Tampoco debe enmascararse aumentando `center_distance_threshold`, `max_missed` ni `iou_threshold`, salvo evidencia específica y pruebas que demuestren que es indispensable.

## Bug Analysis

### Current Behavior (Defect)

Lo que ocurre actualmente cuando se dispara la condición del defecto:

1.1 WHEN el detector ejecuta en un frame real y luego siguen varios frames dispersos, THEN el sistema llama a `flow_tracker.propagate(frame)` y descarta los resultados propagados, dejando el estado espacial de `SimpleTracker` congelado en la última posición de detección real.

1.2 WHEN un tomate físico se desplaza (por movimiento de cámara) más de 120px respecto a su posición de detección previa durante los frames dispersos, y RetinaNet vuelve a detectarlo, THEN `SimpleTracker` compara la nueva detección contra el `bbox` antiguo, la asociación por IoU/distancia de centroide falla y se genera un `track_id` nuevo para el mismo tomate físico.

1.3 WHEN un mismo tomate físico atraviesa varios ciclos detección→dispersos→detección, THEN el sistema le asigna múltiples `track_id`, crea múltiples entradas en `best_by_track` e incrementa `unique_tomatoes` más de una vez para el mismo tomate.

1.4 WHEN la fragmentación de identidad ocurre en una escena con varios tomates, THEN el conteo final resulta engañoso: algunos tomates se pierden y otros se cuentan de más, de modo que `unique_tomatoes` solo se aproxima al conteo real por compensación accidental, no por conteo correcto por identidad.

1.5 WHEN no existe ningún mecanismo para actualizar la posición espacial de un track sin crear track nuevo ni incrementar `hits`, THEN la información de movimiento calculada por Optical Flow entre inferencias no puede reflejarse nunca en el estado de asociación de `SimpleTracker`.

1.6 WHEN RetinaNet omite (no detecta) un track que sigue vivo en `SimpleTracker` en un frame de detección programado, THEN `OpticalFlowVisualTracker.update_from_detection_result()` reconstruye `self.tracks` únicamente a partir de las detecciones recibidas (`self.tracks = new_tracks`) y **destruye** el track visual de ese tomate, de modo que en los frames dispersos siguientes ese track ya no se propaga; cuando RetinaNet vuelve a detectar el mismo tomate, `SimpleTracker` puede haberlo mantenido vivo por `max_missed`, pero al no existir propagación intermedia la asociación puede fallar y reaparecer la fragmentación de identidad.

### Expected Behavior (Correct)

Lo que debería ocurrir para cada condición del defecto:

2.1 WHEN el detector ejecuta en un frame real y luego siguen varios frames dispersos, THEN el sistema SHALL reincorporar las posiciones propagadas por Optical Flow (`track_id` + `bbox`) al estado espacial de `SimpleTracker`, actualizando únicamente la posición de los tracks ya existentes.

2.2 WHEN un tomate físico se desplaza más de 120px respecto a su posición de detección previa durante los frames dispersos, y Optical Flow lo siguió correctamente, y RetinaNet vuelve a detectarlo, THEN `SimpleTracker` SHALL comparar la nueva detección contra la posición actual/predicha del track (actualizada vía propagación) y asociarla al track existente, SIN crear un `track_id` nuevo.

2.3 WHEN un mismo tomate físico atraviesa varios ciclos detección→dispersos→detección, THEN el sistema SHALL conservar un único `track_id` para ese tomate, mantener una sola entrada en `best_by_track` e incrementar `unique_tomatoes` una sola vez.

2.4 WHEN se reincorpora la posición propagada al `SimpleTracker`, THEN la operación SHALL actualizar SOLO la posición espacial de tracks ya existentes y NO SHALL: crear tracks nuevos, tratar la propagación como una detección real, incrementar `unique_tomatoes`, incrementar `hits`, modificar `best_area`, reemplazar `last_health`, reemplazar `last_maturity`, modificar `has_been_processed`, generar un nuevo `InspectionResult`, ni generar snapshots adicionales.

2.5 WHEN una propagación referencia un `track_id` que ya no existe en `SimpleTracker` (p. ej. eliminado por `max_missed`), THEN el sistema SHALL ignorar ese `track_id` propagado de forma segura, sin lanzar excepción y sin crear un track nuevo.

2.6 WHEN Optical Flow no puede propagar un track (retorna lista vacía o pierde ese track), THEN el análisis SHALL continuar sin excepción y `SimpleTracker` SHALL mantener un comportamiento de reserva seguro (el track conserva su última posición conocida hasta que `max_missed` lo elimine, según la política vigente).

2.7 WHEN RetinaNet omite (no detecta) en un frame de detección programado un track que sigue **vivo** en `SimpleTracker` (dentro de `max_missed`), THEN `OpticalFlowVisualTracker` SHALL conservar y seguir propagando ese track visual en los frames dispersos siguientes, de modo que cuando RetinaNet lo detecte nuevamente el tomate retenga el mismo `track_id` (p. ej. el ID 7 sigue siendo ID 7). Esta propagación de continuidad SHALL: NO crear ningún `InspectionResult`, NO incrementar `unique_tracks` ni `unique_tomatoes`, NO modificar `hits`, y SHALL preservar la semántica de `max_missed` sin alterarla.

2.8 WHEN un track está realmente perdido (RetinaNet no lo detecta y Optical Flow tampoco puede seguirlo más allá de `max_missed`), THEN el sistema SHALL dejar que ese track expire normalmente según `max_missed`, sin mantenerlo vivo indefinidamente por efecto de la propagación.

2.9 WHEN el flujo corregido procesa un frame de detección (prediciendo Optical Flow hasta el frame de detección actual con `propagate(frame_detector)` ANTES de ejecutar la detección, y luego re-anclando las detecciones reales y reteniendo los tracks vivos omitidos), THEN todo track visual conservado en `OpticalFlowVisualTracker` (tanto los re-anclados desde detecciones como los retenidos por omisión) SHALL tener `points` y `bbox` correspondientes al **frame de detección actual**, y `self.prev_gray` SHALL corresponder a **ese mismo frame**, de modo que la invariante de coherencia temporal (`track.points` ↔ `self.prev_gray`) se cumpla y el siguiente `propagate()` pueda continuar correctamente desde ese estado. La corrección NO SHALL retener un `VisualTrackState` cuyos `points`/`bbox` pertenezcan a un frame distinto del representado por `self.prev_gray`.

### Unchanged Behavior (Regression Prevention)

Comportamiento existente que debe preservarse (¬C(X)):

3.1 WHEN `enable_flow_propagation=False`, THEN el pipeline SHALL CONTINUE TO comportarse exactamente igual que antes de esta corrección y NO SHALL depender del nuevo mecanismo de sincronización.

3.2 WHEN el detector ejecuta en frames consecutivos (sin frames dispersos intermedios, o con movimiento pequeño dentro de los umbrales actuales), THEN `SimpleTracker` SHALL CONTINUE TO asociar detecciones a tracks existentes con la misma lógica de IoU (`0.30`) y distancia de centroide (`120px`) y los mismos umbrales actuales.

3.3 WHEN se ejecuta el análisis, THEN el sistema SHALL CONTINUE TO usar el umbral de detección de RetinaNet `detection_score_threshold=0.50` (perfil EDGE) sin modificarlo, y SHALL CONTINUE TO usar `center_distance_threshold=120`, `max_missed=3` e `iou_threshold=0.30` sin modificarlos.

3.4 WHEN se procesan detecciones reales de RetinaNet, THEN el sistema SHALL CONTINUE TO ejecutar sanidad y madurez, poblar `best_by_track` por área máxima, generar `InspectionResult` (≤1 por track), generar crops y snapshots anotados exactamente como antes.

3.5 WHEN las propagaciones de Optical Flow ocurren en frames dispersos, THEN el sistema SHALL CONTINUE TO NO contarlas como detecciones de RetinaNet, NO ejecutar sanidad, NO ejecutar madurez, NO crear nuevas entradas de `best_by_track`, NO agregar filas a `inspection_results` y NO incrementar `snapshots_with_detections`.

3.6 WHEN se ejecutan múltiples monitoreos o múltiples ejecuciones de `VideoAnalysisService`, THEN el estado de tracking SHALL CONTINUE TO NO filtrarse entre monitoreos ni entre ejecuciones distintas (cada `run()` parte de un estado limpio).

3.7 WHEN se respeta la arquitectura del proyecto, THEN el sistema SHALL CONTINUE TO mantener las capas de Clean Architecture: `src/domain` y `src/application` NO SHALL importar `cv2`, `torch` ni `detectron2`; la ejecución SHALL CONTINUE TO ser CPU-only.

3.8 WHEN se corrige la continuidad ante omisiones del detector (facet 1.6/2.7/2.8), THEN el sistema SHALL CONTINUE TO usar los mismos umbrales actuales sin aumentarlos arbitrariamente: `max_missed=3`, `center_distance_threshold=120`, `iou_threshold=0.30` y `detection_score_threshold=0.50` (perfil EDGE).

---

## Trazabilidad con criterios de aceptación (CA)

| CA | Descripción | Cláusulas relacionadas |
|---|---|---|
| CA-01 | La propagación actualiza posición sin crear identidad | 1.1, 1.5, 2.1, 2.4 |
| CA-02 | Continuidad tras movimiento mayor a 120px (reproduce el bug real) | 1.2, 2.2 |
| CA-03 | Dos tracks independientes no se fusionan | 2.2, 2.3, 3.2 |
| CA-04 | Optical Flow pierde un track → fallback seguro sin excepción | 2.5, 2.6 |
| CA-05 | La propagación no dispara inferencia adicional | 2.4, 3.5 |
| CA-06 | `unique_tomatoes` cuenta cada tomate una sola vez | 1.3, 1.4, 2.3, 3.4 |
| CA-07 | Compatibilidad con `enable_flow_propagation=False` | 3.1 |
| CA-08 | Sin fuga de estado entre monitoreos/ejecuciones | 3.6 |
| CA-09 | Continuidad ante omisión del detector (miss): un tomate detectado como ID 1 (detector A), no devuelto por un frame de detección programado posterior (detector B) y tras más frames dispersos vuelto a detectar (detector C), SHALL conservar el ID 1 mientras no expire `max_missed`; las propagaciones NO crean `InspectionResult`, NO incrementan `unique_tracks`, NO modifican `hits`, y un track realmente perdido SHALL expirar según `max_missed` | 1.6, 2.7, 2.8, 3.8 |
| CA-10 | Coherencia temporal del estado visual: dado un track visual en frame A y un frame B donde RetinaNet debe ejecutarse pero OMITE ese track, tras procesar B el track visual retenido SHALL tener `points` y `bbox` correspondientes a B, `self.prev_gray` SHALL corresponder a B, y el siguiente `propagate(frame C)` SHALL poder continuar desde ese estado sin desincronización (invariante `track.points` ↔ `self.prev_gray`) | 1.6, 2.7, 2.9 |

## Archivos relevantes (contexto, no exhaustivo)

- `src/application/services/video_analysis_service.py` — rama dispersa `else` que descarta el retorno de `propagate`.
- `src/infrastructure/vision/tracker_adapter.py` — `SimpleTracker` sin método para actualizar posición sin crear track.
- `src/infrastructure/vision/visual_tracker.py` — `OpticalFlowVisualTracker.propagate()` que ya retorna `track_id` + `bbox` propagados. **Tercer archivo de producción a modificar** (cambio pequeño y bien acotado en `update_from_detection_result()` para preservar los tracks visuales que siguen vivos en `SimpleTracker` cuando RetinaNet los omite en un frame de detección, en lugar de reconstruir `self.tracks` solo a partir de las detecciones recibidas). Ver facet 1.6/2.7/2.8 y CA-09. **Requisito de coherencia temporal (CA-10):** la corrección de continuidad ante omisiones exige **predecir Optical Flow en todo frame de detector** (`propagate(frame_detector)`) ANTES de ejecutar la detección, de modo que los tracks omitidos que se retengan ya tengan `points`/`bbox` correspondientes al frame de detección actual (mismo frame que quedará en `self.prev_gray`). Sin este `propagate` previo, retener un `VisualTrackState` antiguo rompería la invariante `track.points` ↔ `self.prev_gray` y corrompería el siguiente `propagate()`. Ver design.md (Decisión A: adoptada).
- `src/infrastructure/vision/pipeline_orchestrator.py` — orquestación por frame.
- `src/domain/services/deduplication_policy.py` — política de deduplicación.

## Pruebas existentes a preservar

- `tests/unit/test_video_analysis_service_sparse.py`
- `tests/unit/test_pipeline_orchestrator_dedup.py`

## Fuera de alcance (NO)

No se implementará: ByteTrack, DeepSORT, SORT externo, ReID neuronal, modelos adicionales, nueva base de datos, nuevas tablas, migraciones, cambios de API, cambios de UI, cambios en el umbral de detección, cambios en el modelo RetinaNet, cambios en sanidad, cambios en madurez, cambios en el Dashboard, cambios en Exportación, corrección de alertas operativas, ni corrección visual de cobertura de madurez. No se resolverá el problema aumentando `center_distance_threshold`, `max_missed` o `iou_threshold` para ocultarlo.
