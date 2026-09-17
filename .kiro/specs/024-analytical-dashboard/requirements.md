# Requerimientos — Spec 024: Dashboard analítico contextual y estimación de próxima cosecha

## Introducción

El Dashboard actual de Tomato Monitor es principalmente un resumen operativo:
cuenta invernaderos, módulos, alertas, último monitoreo, actividades recientes y
estado de sincronización. No permite observar la **evolución real del cultivo**
por invernadero/módulo.

Esta spec transforma el Dashboard en un **dashboard analítico contextual** que
permite al operador analizar, usando únicamente datos ya persistidos por el
sistema:

- cantidad de frutos detectados,
- sanidad,
- madurez,
- evolución entre monitoreos,
- una estimación prudente de la próxima ventana de cosecha.

La spec **no** introduce inferencia, modelos de ML, forecasting complejo, ni
datos que el sistema no mide. La estimación de cosecha es un **indicador
operacional derivado de los monitoreos visuales**, explícitamente comunicado
como estimación (no como pronóstico agronómico), y **sin fingir precisión
estadística**.

### Restricciones no negociables (heredadas del steering)

- **Offline-first:** sin CDN, sin dependencias web que requieran Internet.
- **Aislamiento multiusuario (Spec 022):** el Dashboard solo muestra datos del
  usuario autenticado; nunca invernaderos/módulos/monitorings/métricas de otro
  usuario.
- **Sin cambio de esquema:** SQLite, Supabase, RLS, sync y recovery no se
  modifican. (Ampliar la **API** de un repositorio con lecturas bulk `IN (...)`
  NO es un cambio de esquema y sí está permitido — ver Req 15.)
- **Separación de capas:** los servicios analíticos no acceden a BD; la ruta
  obtiene datos vía repositorios y los pasa al servicio. Los cálculos analíticos
  son testeables en Python puro; la UI solo representa.
- **Raspberry Pi 5 / DSI 800×480:** touch-first, sin operaciones pesadas por
  render, navegación de pestañas inmediata.
- **Lenguaje operativo:** español, sin jerga técnica ni lenguaje robótico.

### Decisiones ya aprobadas (cerradas)

- Cobertura de madurez y **conteos por estado** derivados de `InspectionResult`
  real (`maturity_stage is not None`).
- Querystring para selección de invernadero/módulo.
- `AnalyticsService` separado de `DashboardService`.
- No persistir cobertura; sin cambios de esquema SQLite/Supabase/RLS/sync/recovery.
- CSS/SVG/JS local, sin CDN.
- Escala ordinal de madurez 0.0 → 1.0.
- `MIN_OBS = 3`.
- `MI_TARGET = 0.8` como **nivel medio de madurez equivalente a Light Red**
  (convención operacional), NO como "porcentaje Light Red/Red".
- Evolución en scope "Todos" mediante **series independientes por módulo**.

---

## Glosario y definiciones operativas

- **Monitoreo válido (base):** monitoreo con `status == "completed"` y
  `MonitoringMetrics` asociado. Los `aborted` (métricas parciales/cero) y
  `error`/en curso (sin métricas) **no** son válidos para analítica.
- **Frutos detectados (por monitoreo):** `MonitoringMetrics.total_tomatoes` =
  número de *tracks únicos dentro de esa sesión*. **No** es identidad
  longitudinal entre días.
- **Conteos de madurez (fuente de verdad):** derivados de `InspectionResult` del
  monitoreo contando por `maturity_stage`: `count_green, count_breaker,
  count_turning, count_pink, count_light_red, count_red` y
  `maturity_covered = Σ count_stage` (resultados con `maturity_stage != None`).
  `MonitoringMetrics.pct_*` se usa **solo** como dato existente/fallback de
  visualización, **nunca** para reconstruir conteos cuando existe `InspectionResult`.
- **coverage_ratio (de un monitoreo):** `maturity_covered / total_tomatoes`.
- **Estados de madurez (orden fenológico):** Green → Breaker → Turning → Pink →
  Light Red → Red.
- **Observación utilizable para tendencia/cosecha:** monitoreo `completed`, con
  métricas, con `started_at`, con `maturity_covered > 0` y `total_tomatoes > 0`.
  (No se aplica ningún corte de cobertura mínima arbitrario; la cobertura entra
  como **peso**, ver Req 9.)

---

## Requerimiento 1 — Filtros contextuales (invernadero + módulo)

**Historia:** Como operador, quiero seleccionar un invernadero y luego un módulo
(o "Todos"), para analizar el subconjunto de datos que me interesa.

### Criterios de aceptación

1.1. CUANDO se carga el Dashboard, ENTONCES se muestra un **selector de
     invernadero** con los invernaderos del usuario autenticado.

1.2. CUANDO el usuario no tiene invernaderos, ENTONCES el Dashboard muestra un
     **empty state** claro ("No hay invernaderos registrados aún") sin errores.

1.3. CUANDO se selecciona un invernadero, ENTONCES el **selector de módulo** se
     puebla con la opción **"Todos"** más cada módulo perteneciente a ese
     invernadero.

1.4. CUANDO no se ha seleccionado invernadero explícitamente, ENTONCES el sistema
     elige un invernadero **por defecto determinista**: `min(greenhouses,
     key=lambda gh: gh.id)` (NO el orden implícito de `get_all_by_owner()`), con
     módulo = "Todos".

1.5. CUANDO se selecciona un módulo específico, ENTONCES todos los KPIs, pestañas
     y la sección de cosecha reflejan **solo** ese módulo.

1.6. CUANDO se selecciona "Todos", ENTONCES los KPIs y pestañas reflejan el
     invernadero según las reglas de los Requerimientos 4, 5 y 12.

1.7. El cambio de invernadero, de módulo y de pestaña DEBE sentirse inmediato en
     la Raspberry Pi (sin recarga pesada ni bloqueo perceptible).

---

## Requerimiento 2 — Aislamiento multiusuario (Spec 022)

**Historia:** Como operador, quiero estar seguro de que solo veo mis datos.

### Criterios de aceptación

2.1. El Dashboard DEBE obtener invernaderos únicamente vía
     `GreenhouseRepository.get_all_by_owner(user.id)`.

2.2. El Dashboard DEBE resolver módulos, monitoreos, métricas y resultados
     únicamente descendiendo desde los invernaderos del usuario (FK chain), sin
     usar consultas globales (`monitoring_repo.list_all()`, `get_active()` u
     homólogos sin scope). Las lecturas bulk `IN (...)` (Req 15) solo reciben ids
     de monitoreos que ya pertenecen a módulos del usuario.

2.3. CUANDO se solicita un `greenhouse_id` que no pertenece al usuario, ENTONCES
     el sistema lo trata como **no encontrado** y regresa a un scope válido del
     propio usuario.

2.4. CUANDO se solicita un `module_id` que no pertenece a un invernadero del
     usuario, ENTONCES el sistema lo trata como **no encontrado**.

2.5. NUNCA pueden aparecer invernaderos, módulos, monitorings o métricas de otro
     usuario en ningún KPI, gráfica o sección.

---

## Requerimiento 3 — KPIs contextuales principales

**Historia:** Como operador, quiero ver los indicadores clave del scope
seleccionado sin saturación visual.

### Criterios de aceptación

3.1. El Dashboard analítico DEBE mostrar cuatro KPIs principales:
     (1) Último monitoreo, (2) Frutos detectados, (3) Estado sanitario,
     (4) Madurez predominante.

3.2. **Frutos detectados — módulo específico:** el KPI DEBE basarse en el
     `total_tomatoes` del **último monitoreo válido** del módulo. NO DEBE sumar
     `total_tomatoes` de todos los monitoreos históricos.

3.3. **Frutos detectados — "Todos":** el KPI DEBE agregarse sumando el
     `total_tomatoes` del **último monitoreo válido de cada módulo** del
     invernadero, NO la suma histórica.

3.4. **Estado sanitario:** DEBE derivarse de `healthy_count`/`unhealthy_count`
     (conteos), y en "Todos" DEBE ponderarse por conteos reales, no por promedio
     ingenuo de porcentajes entre módulos.

3.5. **Madurez predominante:** DEBE ser el estado con mayor **conteo real**
     (`count_stage` derivado de `InspectionResult`) dentro del scope; si no hay
     madurez clasificable, DEBE indicarse "Sin datos de madurez".

3.6. Puede mostrarse información secundaria (cambio vs monitoreo anterior;
     `harvestable_share`) SIN convertir el Dashboard en una cuadrícula de 10–15
     KPIs.

3.7. CUANDO el scope no tiene ningún monitoreo válido, ENTONCES los KPIs muestran
     un empty state ("Sin monitoreos válidos aún") en lugar de ceros engañosos.

3.8. El Dashboard NO DEBE presentar métricas agronómicas no soportadas
     (rendimiento, kilogramos, diagnóstico de enfermedades, déficit hídrico,
     riego recomendado, plaga específica).

3.9. **Semántica de "Último monitoreo" en scope "Todos":** DEBE definirse como el
     monitoreo válido de **fecha `started_at` más reciente entre todos los
     módulos** del invernadero (el más nuevo de cualquier módulo), no como una
     agregación. En scope módulo es el último válido de ese módulo.

---

## Requerimiento 4 — Reglas de agregación para "Todos" (KPIs)

**Historia:** Como operador, cuando miro el invernadero completo quiero un
agregado semánticamente correcto, no una suma engañosa.

### Criterios de aceptación

4.1. La agregación de "frutos detectados" en "Todos" DEBE sumar el
     `total_tomatoes` del **último monitoreo válido de cada módulo**.

4.2. La agregación de sanidad en "Todos" DEBE sumar `healthy_count` y
     `unhealthy_count` de esos últimos monitoreos válidos y recalcular los
     porcentajes desde los conteos agregados (ponderación real).

4.3. La agregación de madurez en "Todos" DEBE sumar los **conteos reales por
     estado** (`count_stage` de `InspectionResult`) de esos últimos monitoreos
     válidos y recalcular porcentajes/predominante desde los conteos agregados.
     NO DEBE promediar porcentajes ni reconstruir conteos desde `pct_*`.

4.4. La agregación DEBE excluir módulos sin monitoreo válido, y DEBE reportar
     `contributing_modules` (cuántos módulos aportaron).

4.5. Las reglas de agregación DEBEN implementarse en Python (servicio) y estar
     cubiertas por tests deterministas; NO DEBEN duplicarse en Jinja/JavaScript.

---

## Requerimiento 5 — Pestaña Evolución (series por módulo en "Todos")

**Historia:** Como operador, quiero ver cómo evolucionan los monitoreos en el
tiempo dentro del scope, sin totales históricos artificiales del invernadero.

### Criterios de aceptación

5.1. La pestaña Evolución DEBE mostrar series temporales de **monitoreos reales**
     usando sus fechas (`started_at`), ordenadas cronológicamente.

5.2. **Scope módulo:** una sola serie con un punto por monitoreo válido del
     módulo, valor = `total_tomatoes` de ese monitoreo.

5.3. **Scope "Todos":** una sola gráfica con **una serie independiente por
     módulo**. Cada módulo conserva sus **fechas reales**. NO se crean puntos
     históricos agregados mezclando monitoreos de módulos en fechas distintas, ni
     un "total histórico del invernadero".

5.4. La misma regla de series-por-módulo aplica a la evolución de sanidad
     (`pct_healthy` por monitoreo) y a la evolución del maturity index.

5.5. La UI DEBE dejar claro que en "Todos" son **series por módulo** (leyenda /
     etiqueta por módulo).

5.6. La pestaña DEBE permitir observar la **tendencia** y el **cambio frente al
     monitoreo anterior** dentro de cada serie cuando exista más de un punto.

5.7. Ningún punto DEBE presentarse como conteo de frutos únicos persistentes
     entre monitoreos (el tracker identifica frutos dentro de una sesión, no
     entre días). El significado de cada punto DEBE documentarse y reflejarse en
     la UI.

5.8. CUANDO una serie tiene 0 puntos, ENTONCES empty state; con 1 punto, se
     muestra el único punto sin tendencia.

5.9. Cada gráfica DEBE ser una sola visualización principal apta para 800×480,
     renderizada sin librerías externas ni red.

---

## Requerimiento 6 — Pestaña Sanidad

**Historia:** Como operador, quiero interpretar la sanidad del cultivo.

### Criterios de aceptación

6.1. La pestaña Sanidad DEBE usar datos reales persistidos: `healthy_count`,
     `unhealthy_count`, `pct_healthy`, `pct_unhealthy`.

6.2. DEBE mostrar el estado sanitario del **monitoreo válido más reciente** del
     scope.

6.3. DEBE mostrar la **evolución** de la proporción sano/no sano entre monitoreos
     válidos (series por módulo en "Todos", Req 5.4).

6.4. La pestaña DEBE usar una sola visualización principal apta para 800×480.

6.5. La pestaña NO DEBE inventar diagnósticos de enfermedades: el sistema solo
     clasifica healthy/unhealthy.

6.6. CUANDO `pct_healthy + pct_unhealthy < 100` (existen etiquetas "unknown"),
     ENTONCES la UI NO DEBE presentar una suma incorrecta como 100%; el resto se
     comunica como "otros N%".

6.7. Los casos 0/100 y 100/0 DEBEN representarse correctamente.

---

## Requerimiento 7 — Pestaña Madurez (conteos reales)

**Historia:** Como operador, quiero ver la distribución y evolución de madurez.

### Criterios de aceptación

7.1. La distribución de madurez DEBE construirse a partir de los **conteos reales
     por estado** derivados de `InspectionResult` (`count_green..count_red`,
     `maturity_covered`). El uso de `MonitoringMetrics.pct_*` está limitado al
     fallback definido en el Req 8.5.

7.2. DEBE indicar el **estado predominante** (mayor `count_stage`).

7.3. DEBE mostrar la **evolución temporal** del maturity index entre monitoreos
     válidos (series por módulo en "Todos", Req 5.4).

7.4. La distribución de madurez DEBE comunicar la **cobertura real**
     (`maturity_covered` de `total_tomatoes`, p. ej. "10 de 16 frutos con madurez
     clasificable"), sin afirmar que los porcentajes describen el 100% de
     `total_tomatoes`.

7.5. CUANDO ningún fruto del scope tiene madurez clasificable
     (`maturity_covered == 0`), ENTONCES la pestaña muestra "Sin datos de
     madurez" y NO calcula índice ni predominante.

7.6. El **maturity index** (índice ordinal normalizado Green→Red, promedio
     ordinal) DEBE calcularse según la fórmula del design.md y NO DEBE
     presentarse como una medida agronómica universal, sino como indicador
     operacional. NO DEBE afirmarse que `MI ≥ 0.8` equivale al porcentaje Light
     Red/Red.

7.7. Además del índice, DEBE mostrarse el **harvestable_share** =
     `(count_light_red + count_red) / maturity_covered` como información
     complementaria real, claramente distinta del maturity index. La UI DEBE
     rotularlo **siempre con su denominador explícito**: "X % de los frutos **con
     madurez clasificable** están en Light Red/Red" — NUNCA "X % de los frutos"
     (el denominador es `maturity_covered`, no `total_tomatoes`).

7.9. **Predominante con empate:** CUANDO dos o más estados comparten el conteo
     máximo, ENTONCES el predominante DEBE ser la **lista** de estados empatados
     en orden fenológico (determinista), y la UI DEBE mostrar "Mixto — Estado1 /
     Estado2". No se elige uno arbitrariamente por orden del diccionario.

7.8. La pestaña DEBE usar una sola visualización principal apta para 800×480, sin
     librerías externas ni red.

---

## Requerimiento 8 — Cobertura de madurez (semántica correcta)

**Historia:** Como equipo de tesis, no quiero que el Dashboard produzca
conclusiones falsas por ignorar cobertura parcial de madurez.

### Criterios de aceptación

8.1. El sistema DEBE tratar la distribución de madurez como conteos/porcentajes
     **sobre el subconjunto con madurez clasificable** (`maturity_covered`), no
     sobre `total_tomatoes`.

8.2. La cobertura y los conteos por estado DEBEN derivarse de `InspectionResult`
     (`maturity_stage != None`) SIN modificar el esquema de BD.

8.3. La derivación DEBE respetar el aislamiento multiusuario (solo monitoreos de
     módulos del usuario) y DEBE usar lectura **bulk** (Req 15) para evitar N+1.

8.4. El fix visual del reporte individual sobre cobertura de madurez está
     **pospuesto** (después de Spec 025) y NO forma parte de esta spec.

8.5. **Semántica cerrada del fallback de madurez.** El contexto DEBE representar
     tres estados por observación, sin que la UI infiera reglas de negocio:
     - Si existen `InspectionResult`: conteos reales, cobertura, distribución,
       predominante, maturity index, harvestable_share y participación en
       tendencia/cosecha (`coverage_known = True`).
     - Si NO existen `InspectionResult` pero SÍ `pct_*` persistidos: se permite
       **solo** distribución **visual**; la UI rotula **"Cobertura no
       disponible"** (`coverage_known = False`); NO se reconstruyen conteos, NO
       se calcula cobertura, NO se calcula maturity index ni harvestable_share, y
       la observación **NO** participa en el maturity index histórico, la WLS ni
       la cosecha.
     - Si no hay ni `InspectionResult` ni `pct_*` útiles: "Sin datos de madurez".

---

## Requerimiento 9 — Próxima cosecha (estimación por módulo, ponderada por cobertura)

**Historia:** Como operador, quiero una estimación prudente de cuándo un módulo
estará próximo a cosecha, basada en la evolución observada de madurez y en la
calidad real de los datos.

### Criterios de aceptación

9.1. La estimación DEBE calcularse **por módulo** usando únicamente
     **observaciones utilizables** del módulo (ver Glosario), ordenadas
     temporalmente.

9.2. La estimación DEBE fundamentarse en la progresión Green → … → Red mediante
     el maturity index y una **tendencia ponderada por cobertura**
     (`weight_i = coverage_ratio_i`), de forma determinista y derivada de los
     datos, **sin cortes de cobertura mínima arbitrarios**.

9.3. La proyección del cruce de `MI_TARGET` DEBE calcularse **sobre la recta
     ajustada** (`t_target = (MI_TARGET − intercept) / slope`), no mezclando el
     valor observado crudo con la pendiente de regresión.

9.4. La estimación DEBE devolver una **ventana temporal** cuya amplitud provenga
     de la **variabilidad real** observada (dispersión ponderada de residuales),
     NO de una constante arbitraria (±25%). NO DEBE llamarse "intervalo de
     confianza estadístico".

9.5. La estimación DEBE comunicar la **suficiencia/soporte de datos** con
     **evidencia objetiva** (p. ej. "Estimación basada en 4 monitoreos",
     "Cobertura media de madurez: 81 %"), NO con categorías tipo
     "confianza alta/media/baja".

9.6. La estimación DEBE actualizarse cuando aparece un nuevo monitoreo válido.

9.7. CUANDO existen **menos de `MIN_OBS = 3`** observaciones utilizables,
     ENTONCES el resultado DEBE ser **"Datos insuficientes"**.

9.8. La estimación DEBE devolver **"No estimable"** cuando: no exista dispersión
     temporal (`Sxx_w = 0`); o `MI_last < MI_TARGET` y `slope ≤ 0` (sin
     progresión que proyectar); o `MI_last < MI_TARGET`, `slope > 0` y el cruce
     ajustado quedó en el pasado de forma inconsistente (`t_target ≤ x_last` y
     `t_high ≤ x_last`), reportando la causa (el ajuste histórico proyectaba el
     objetivo en el pasado pero el último dato observado sigue por debajo).

9.9. **`target_reached` se decide EXCLUSIVAMENTE por `MI_last ≥ MI_TARGET`.**
     `days_until ≤ 0` por sí solo NO implica objetivo alcanzado (el cruce de la
     recta puede quedar en el pasado con `MI_last < MI_TARGET`). CUANDO
     `MI_last < MI_TARGET`, `slope > 0`, `t_target ≤ x_last` y `t_high > x_last`,
     ENTONCES el estado es **"window"** con inicio "ahora" (fecha del último
     monitoreo) y fin en `t_high`. El estado `target_reached` se comunica como
     **"Nivel medio de madurez objetivo alcanzado"** (NO "cosechable ahora", NO
     sinónimo de MI≥0.8 como porcentaje); `harvestable_share` se muestra aparte.

9.13. CUANDO la dispersión de residuales es nula (`s = 0`, `t_low = t_high`),
      ENTONCES se acepta la **ventana degenerada** (fecha puntual), pero la UI NO
      la presenta como fecha garantizada: usa "Estimación central: alrededor del
      DD/MM/YYYY" + "Sin dispersión observada en los monitoreos utilizados", con
      el disclaimer general.

9.10. El criterio "cosechable" DEBE documentarse como **convención operativa** del
      prototipo: `MI_TARGET = 0.8` = nivel medio de madurez equivalente a Light
      Red; y `harvestable_share` como señal complementaria. No es ley agronómica
      universal.

9.11. La estimación NO DEBE estimar kilogramos, rendimiento, ni fecha exacta
      garantizada, ni usar variables no medidas (meteorología, etc.).

9.12. Las funciones numéricas (índice, coverage_ratio, tendencia ponderada,
      banda) DEBEN ser deterministas y reproducibles, con tests de ejemplos
      numéricos conocidos.

---

## Requerimiento 10 — "Todos" y cosecha

**Historia:** Como operador, cuando miro el invernadero completo no quiero una
fecha de cosecha inventada para todo el invernadero.

### Criterios de aceptación

10.1. CUANDO el scope es "Todos", ENTONCES la sección de próxima cosecha NO DEBE
      producir una única fecha del invernadero agregando módulos distintos.

10.2. CUANDO el scope es "Todos", ENTONCES la UI DEBE **resumir por módulo**
      (qué módulos alcanzaron nivel objetivo, cuáles tienen ventana estimada,
      cuáles "No estimable"/"Datos insuficientes").

10.3. La estimación por módulo DEBE seguir siendo la unidad de cálculo.

---

## Requerimiento 11 — Frescura del agregado "Todos"

**Historia:** Como operador, cuando veo el invernadero completo quiero entender
que los datos de cada módulo pueden ser de fechas distintas.

### Criterios de aceptación

11.1. El KPI agregado en "Todos" DEBE comunicarse como **"último dato disponible
      de cada módulo"**, no como si todos los módulos se hubieran medido
      simultáneamente.

11.2. La UI DEBE mostrar `contributing_modules` y, opcionalmente, el **rango de
      fechas** (mínimo–máximo `started_at`) de esos últimos datos.

11.3. El KPI "Último monitoreo" en "Todos" sigue la semántica del Req 3.9 (el
      monitoreo válido más reciente de cualquier módulo).

---

## Requerimiento 12 — Preservación de funcionalidad operativa

**Historia:** Como operador, no quiero perder las funciones operativas actuales
del Dashboard.

### Criterios de aceptación

12.1. El Dashboard DEBE conservar: alertas operativas, contexto de invernadero en
      las alertas (fix reciente), último monitoreo, actividades recientes, estado
      de sincronización e información operativa.

12.2. El fix `include greenhouse context in operational alerts` DEBE preservarse
      (los tests correspondientes deben seguir pasando).

12.3. La información operativa PUEDE reorganizarse debajo del bloque analítico o
      simplificarse, pero NO DEBE eliminarse ninguna funcionalidad sin
      justificación y aprobación explícita.

---

## Requerimiento 13 — Offline, sin dependencias de red y rendimiento en RPi

### Criterios de aceptación

13.1. El Dashboard NO DEBE cargar recursos desde CDN ni requerir Internet.

13.2. Las gráficas DEBEN renderizarse con recursos locales (CSS/SVG/JS propios),
      sin librerías externas nuevas que requieran red.

13.3. El Dashboard NO DEBE cargar modelos ML, ejecutar inferencia, reprocesar
      videos ni procesar imágenes para construir estadísticas.

13.4. El Dashboard DEBE trabajar sobre datos persistidos y evitar operaciones
      pesadas por render; la carga de datos DEBE usar lectura bulk (Req 15) para
      evitar N+1 evitable.

13.5. Un test DEBE verificar la ausencia de referencias a red/CDN en el HTML del
      Dashboard.

13.6. Si por presentación/performance se decidiera limitar el número de puntos
      **representados** en una gráfica, ESO DEBE documentarse como decisión de
      **presentación** y NO como regla de cálculo de cosecha. El **historial
      disponible para cálculo** no se recorta por una constante arbitraria; el
      cálculo usa todas las observaciones utilizables.

---

## Requerimiento 14 — Arquitectura y testabilidad

### Criterios de aceptación

14.1. Los servicios analíticos NO DEBEN acceder a BD; reciben datos ya cargados.

14.2. La ruta/UI DEBE obtener los datos vía repositorios y pasarlos al servicio.

14.3. Las reglas de agregación, el maturity index, la tendencia y la ventana
      DEBEN estar en Python puro y ser testeables sin hardware ni BD.

14.4. NO DEBE haber SQL en templates, ni repositorios dentro de domain services,
      ni reglas de agregación duplicadas en Jinja/JS.

14.5. NO DEBE modificarse el esquema de BD (SQLite/Supabase/RLS/sync/recovery).
      Ampliar la API de repositorios con lecturas bulk `IN (...)` está permitido.

---

## Requerimiento 15 — Lectura bulk para evitar N+1

**Historia:** Como sistema en Raspberry Pi, quiero cargar métricas y resultados
de varios monitoreos sin ejecutar una query por monitoreo.

### Criterios de aceptación

15.1. `MonitoringMetricsRepository` DEBE ofrecer
      `get_by_monitoring_ids(ids: list[int]) -> dict[int, MonitoringMetrics]`
      implementado con `IN (...)`.

15.2. `InspectionResultRepository` DEBE ofrecer
      `get_by_monitoring_ids(ids: list[int]) -> dict[int, list[DetectionInspectionResult]]`
      implementado con un JOIN a `snapshots` y `IN (...)`.

15.3. Estas ampliaciones de API NO DEBEN cambiar el esquema, ni el comportamiento
      de los métodos existentes.

15.4. Los métodos bulk DEBEN manejar lista vacía (→ dict vacío) y ids
      inexistentes (ausentes del dict), y DEBEN tener tests dedicados.

15.5. La ruta DEBE usar estos métodos bulk pasando solo ids de monitoreos de
      módulos del usuario (aislamiento intacto).

---

## Requerimiento 16 — Cobertura de pruebas

La spec DEBE incluir pruebas para:

16.1. selección de invernadero; selección de módulo; opción "Todos".
16.2. default determinista de invernadero (`min` por id).
16.3. aislamiento multiusuario (no fuga de datos entre usuarios).
16.4. empty state (sin invernaderos / sin monitoreos válidos).
16.5. un solo monitoreo; dos monitoreos; ≥3 monitoreos.
16.6. ausencia de `MonitoringMetrics`.
16.7. health 0/100, 100/0 y combinaciones; suma <100 por "unknown".
16.8. maturity con distribución normal; cobertura parcial; totalmente no
      clasificable — usando **conteos reales de InspectionResult**.
16.9. maturity index y harvestable_share como magnitudes separadas.
16.10. cambio vs monitoreo anterior.
16.11. no sumar históricos como frutos únicos (KPI usa último válido).
16.12. agregación ponderada en "Todos" (conteos reales, no promedio de %).
16.13. evolución "Todos" = series independientes por módulo con fechas reales.
16.14. harvest: datos suficientes; <3 observaciones; sin dispersión temporal;
       pendiente ≤ 0 sin alcanzar target; gaps temporales irregulares.
16.15. tendencia ponderada por cobertura (observación de baja cobertura influye
       menos que una de alta cobertura).
16.16. cruce sobre la recta ajustada (no `(MI_TARGET − MI_last)/slope`).
16.17. ventana derivada de dispersión de residuales (ejemplo numérico conocido).
16.22. `target_reached` SOLO por `MI_last ≥ MI_TARGET` (test con `days_until ≤ 0`
       pero `MI_last < MI_TARGET` que NO debe dar target_reached).
16.23. cruce ajustado en el pasado: `t_target ≤ x_last` con `t_high > x_last`
       → "window" desde "ahora"; con `t_high ≤ x_last` → "not_estimable"
       (inconsistencia).
16.24. `s == 0` → ventana degenerada marcada (`is_degenerate_window`), no
       presentada como garantía.
16.25. empate de madurez predominante → lista en orden fenológico ("Mixto").
16.26. fallback de madurez: sin `InspectionResult` con `pct_*` →
       `coverage_known = False`, distribución visual, sin cobertura/índice/
       harvestable_share, no participa en cosecha.
16.27. harvestable_share rotulado con denominador `maturity_covered` (no
       `total_tomatoes`).
16.18. frescura del agregado "Todos" (contributing_modules, rango de fechas).
16.19. estructura/touch de UI (800×480); ausencia de dependencias de red/CDN.
16.20. preservación de alertas operativas y del contexto de invernadero.
16.21. repositorios bulk (`get_by_monitoring_ids`): normal, lista vacía, ids
       inexistentes.
