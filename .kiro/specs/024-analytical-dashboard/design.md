# Diseño — Spec 024: Dashboard analítico contextual y estimación de próxima cosecha

## 1. Alcance del diseño

Define **cómo** se implementará el Dashboard analítico sin modificar el esquema
de BD, respetando aislamiento multiusuario (Spec 022), funcionamiento offline y
la separación de capas. Incluye las fórmulas finales (maturity index,
harvestable_share, coverage_ratio, tendencia ponderada, ventana derivada de
datos) para revisión **antes** de implementar.

> Estado: PROPUESTA PARA REVISIÓN. No se ha modificado código productivo.
>
> Decisiones aprobadas incorporadas: conteos de madurez desde `InspectionResult`
> real; querystring; `AnalyticsService` separado; no persistir cobertura; sin
> cambios de esquema; CSS/SVG/JS local; ordinal 0.0→1.0; `MIN_OBS=3`;
> `MI_TARGET=0.8` como nivel medio ≡ Light Red; evolución "Todos" en series por
> módulo; lecturas bulk `IN (...)`.

---

## 2. Semántica de datos existente (auditada)

| Dato | Fuente | Semántica confirmada |
|---|---|---|
| `MonitoringMetrics.total_tomatoes` | métricas persistidas | Tracks únicos **dentro de la sesión**. Sin identidad entre días. |
| `healthy_count` / `unhealthy_count` | conteo sobre tracks | Denominador de sanidad = `total_tomatoes`. Puede sumar <100 (etiquetas `unknown`). |
| `count_green..count_red`, `maturity_covered` | **`InspectionResult` (fuente de verdad)** | Conteo de resultados con `maturity_stage` no nulo por estado. |
| `MonitoringMetrics.pct_*` | porcentajes sobre cobertura | **Solo** fallback de visualización; NO se usa para reconstruir conteos si hay `InspectionResult`. |
| Monitoreo válido | `status == "completed"` con métricas | `aborted`=parcial/cero; `error`/en curso=sin métricas. |

**Consecuencia clave:** sanidad y madurez tienen **denominadores distintos**. La
UI etiqueta cobertura y denominador; no los mezcla.

### 2.1. Repositorios (estado actual + ampliaciones bulk)

Existentes (sin cambios de comportamiento):
- `GreenhouseRepository.get_all_by_owner(owner_user_id)`, `get_by_id_for_owner(id, owner)`
- `ModuleRepository.get_by_greenhouse(greenhouse_id)`, `get_by_id(id)`
- `MonitoringRepository.get_by_module(module_id)`, `get_by_id(id)`
- `MonitoringMetricsRepository.get_by_monitoring(monitoring_id)`
- `InspectionResultRepository.get_by_monitoring(monitoring_id)`, `get_by_snapshot(snapshot_id)`

Ampliaciones de API (NO esquema) — ver §12:
- `MonitoringMetricsRepository.get_by_monitoring_ids(ids) -> dict[int, MonitoringMetrics]`
- `InspectionResultRepository.get_by_monitoring_ids(ids) -> dict[int, list[DetectionInspectionResult]]`

---

## 3. Arquitectura

### 3.1. Capas

```
Presentación (app/)             Aplicación (src/application/)          Dominio (src/domain/)
────────────────────            ─────────────────────────────         ─────────────────────
agricultural_ui.py              DashboardService (existente)           entidades existentes
  /dashboard (ruta)               build_context(...)  → bloque operativo
  - resuelve scope              AnalyticsService (NUEVO)               (Monitoring, Metrics, ...)
  - carga BULK vía repos          build_analytics(scope_data)
  - pasa datos al servicio      HarvestEstimator (NUEVO, puro)
  - render Jinja                  estimate(observations)
                                MaturityStats (NUEVO, puro)
dashboard.html + partials         from InspectionResult counts
  (CSS/SVG/JS local)
```

- **Regla mantenida:** los servicios analíticos **no acceden a BD**. La ruta
  carga todo (con lecturas bulk) y lo pasa como argumentos.
- Nuevos módulos en `src/application/`:
  - `analytics_service.py` — `AnalyticsService`.
  - `harvest_estimator.py` — lógica pura de tendencia y ventana.
  - `maturity_stats.py` — conteos, distribución, predominante, maturity index,
    harvestable_share (todo desde conteos reales).
  - DTOs en `dashboard_analytics_dtos.py` (a confirmar en tasks).

### 3.2. Data flow (request)

```
1. GET /dashboard?greenhouse_id=&module_id=   (module_id="all" o entero)
2. user = require_current_user_html
3. greenhouses = gh_repo.get_all_by_owner(user.id)
4. Resolver scope determinista:
     - greenhouse: el de la querystring si es propio; si no → min(greenhouses, key=id)
     - modules = module_repo.get_by_greenhouse(gh.id)
     - module scope: "all" (default) o module_id que pertenezca a `modules`
5. Recolectar CANDIDATOS (aún NO "válidos"):
     - Para cada módulo del scope: monitorings = monitoring_repo.get_by_module(m.id)
     - candidatos = [m for m in monitorings if m.status == "completed" and m.started_at is not None]
     - candidate_ids = [m.id for m in candidatos]
6. Bulk de métricas sobre CANDIDATOS:
     - metrics_by_id = metrics_repo.get_by_monitoring_ids(candidate_ids)
7. VÁLIDOS = candidatos cuyo id EXISTE en metrics_by_id
     (valid monitoring = completed + started_at + MonitoringMetrics existente).
     - valid_ids = [m.id for m in candidatos if m.id in metrics_by_id]
8. Bulk de InspectionResult SOLO para valid_ids:
     - results_by_id = inspection_repo.get_by_monitoring_ids(valid_ids)
9. Derivar por monitoreo válido: conteos de madurez y coverage desde results_by_id
     (si no hay results para un válido con pct_* → fallback visual, §4.1).
10. Construir ScopeData (módulos → observaciones válidas con métricas + conteos/fallback).
11. analytics = AnalyticsService().build_analytics(scope_data, today=...)
12. operativo = DashboardService().build_context(...)   (bloque existente)
13. render dashboard.html con {**operativo, **analytics, selectores, scope}
```

> Orden obligatorio: **NO** se etiqueta un monitoreo como "válido" antes de
> confirmar que tiene `MonitoringMetrics`. Por eso el bulk de métricas se hace
> sobre *candidatos* y los válidos se derivan de la presencia en `metrics_by_id`.
> El bulk de `InspectionResult` se hace **solo** sobre `valid_ids`.

### 3.3. Estructuras / context objects

```python
@dataclass(frozen=True)
class MaturityCounts:
    green: int; breaker: int; turning: int; pink: int; light_red: int; red: int
    @property
    def covered(self) -> int:
        return self.green+self.breaker+self.turning+self.pink+self.light_red+self.red

@dataclass(frozen=True)
class MaturityDistributionFallback:
    """Distribución SOLO para visualización cuando NO hay InspectionResult pero
    sí hay MonitoringMetrics.pct_* persistidos. coverage_known = False.
    NO representa conteos ni cobertura reales."""
    pct_green: float; pct_breaker: float; pct_turning: float
    pct_pink: float; pct_light_red: float; pct_red: float

@dataclass
class ModuleValidMonitoring:
    monitoring_id: int
    started_at: datetime
    total_tomatoes: int
    healthy_count: int
    unhealthy_count: int
    # Fuente de verdad de madurez: conteos reales de InspectionResult.
    maturity_counts: Optional[MaturityCounts] = None
    # Fallback SOLO visual cuando no hay InspectionResult (coverage_known=False).
    maturity_fallback: Optional[MaturityDistributionFallback] = None

    @property
    def coverage_known(self) -> bool:
        return self.maturity_counts is not None

    @property
    def coverage_ratio(self) -> Optional[float]:
        """maturity_covered / total_tomatoes; None si la cobertura es desconocida
        (solo hay fallback) — así esta observación NO entra en WLS/cosecha."""
        if self.maturity_counts is None or not self.total_tomatoes:
            return None
        return self.maturity_counts.covered / self.total_tomatoes

@dataclass
class ModuleScopeData:
    module_id: int
    module_name: str
    valid_monitorings: list[ModuleValidMonitoring]   # asc por started_at

@dataclass
class ScopeData:
    greenhouse_id: int
    greenhouse_name: str
    scope_kind: str                 # "module" | "all"
    selected_module_id: Optional[int]
    modules: list[ModuleScopeData]  # 1 si module; N si "all"

# --- Series por módulo (soporta "Todos") ---
@dataclass
class SeriesPoint:
    started_at: datetime
    label: str
    value: float
    note: Optional[str] = None      # p.ej. cobertura parcial

@dataclass
class ModuleSeries:
    module_id: int
    module_name: str
    points: list[SeriesPoint]

# --- KPIs ---
@dataclass
class KpiBlock:
    last_monitoring_date: Optional[datetime]
    fruits_detected: Optional[int]
    fruits_delta: Optional[int]            # solo scope módulo
    health_pct_healthy: Optional[float]
    health_pct_unhealthy: Optional[float]
    health_other_pct: Optional[float]      # resto si suma <100
    predominant_maturity: list[str]        # 1 estado, o >1 si empate (Mixto)
    maturity_covered: Optional[int]
    maturity_total: Optional[int]          # total_tomatoes del scope actual
    harvestable_share: Optional[float]     # (light_red+red)/maturity_covered
    coverage_known: bool = True            # False si distribución es fallback pct_*
    contributing_modules: Optional[int]    # solo "all"
    freshness_from: Optional[datetime]     # rango de fechas de últimos datos (all)
    freshness_to: Optional[datetime]

# --- Cosecha ---
@dataclass
class HarvestResult:
    status: str                # "target_reached"|"window"|"insufficient"|"not_estimable"
    window_start: Optional[date]
    window_end: Optional[date]
    is_degenerate_window: bool = False     # s==0: ventana puntual (no garantía)
    n_observations: int = 0
    mean_coverage_ratio: Optional[float] = None   # evidencia objetiva
    mi_last: Optional[float] = None
    slope_per_day: Optional[float] = None
    reason: Optional[str] = None           # subcaso de inconsistencia (§9.4-C)
    message: str = ""          # español, con evidencia objetiva (no "confianza X")

@dataclass
class HarvestSummaryAll:
    target_reached: list[str]
    upcoming: list[tuple[str, date, date]]   # módulo + ventana
    insufficient: list[str]
    not_estimable: list[str]

@dataclass
class AnalyticsContext:
    kpis: KpiBlock
    evolution_series: list[ModuleSeries]     # frutos por monitoreo
    health_series: list[ModuleSeries]        # pct_healthy por monitoreo
    maturity_current: MaturityCounts         # distribución del scope (último/agregado)
    maturity_index_series: list[ModuleSeries]
    harvest: "HarvestResult | HarvestSummaryAll"
```

En scope módulo, cada `*_series` contiene **una** `ModuleSeries`. En "Todos",
contiene **N** `ModuleSeries` (una por módulo), cada una con sus fechas reales.

---

## 4. Conteos de madurez desde InspectionResult (fuente de verdad)

Para un monitoreo, con `results = inspection_repo` (bulk):

```
count[stage] = nº de results con r.maturity_stage == stage      (stage ∈ 6 estados)
maturity_covered = Σ count[stage]        (= results con maturity_stage != None)
```

- Distribución, predominante, maturity index y agregación "Todos" usan **estos
  conteos exactos**.

### 4.1. Semántica de fallback de madurez (cerrada)

Tres estados posibles por observación:

1. **Hay `InspectionResult`** → `maturity_counts` reales (`coverage_known=True`):
   conteos, cobertura, distribución, predominante, maturity index,
   harvestable_share, y participa en tendencia/cosecha (WLS).

2. **No hay `InspectionResult` pero sí `pct_*` persistidos** →
   `maturity_fallback` (`coverage_known=False`):
   - se permite **solo** distribución **visual** (barra de porcentajes);
   - la UI rotula **"Cobertura no disponible"**;
   - NO se reconstruyen conteos; NO se calcula cobertura;
   - NO se calcula harvestable_share (requiere conteos reales);
   - NO se calcula maturity index para esa observación;
   - la observación **NO** entra en el maturity index histórico, ni en WLS, ni en
     la estimación de cosecha (su `coverage_ratio` es `None`).

3. **Ni `InspectionResult` ni `pct_*` útiles** → "Sin datos de madurez".

Así Jinja no infiere reglas de negocio: el servicio entrega `coverage_known`,
la distribución (real o fallback) y las magnitudes derivadas ya resueltas
(None cuando no aplican).

`MonitoringMetrics.pct_*` NUNCA se usa para reconstruir conteos cuando existe
`InspectionResult`.

---

## 5. Reglas de agregación

### 5.1. Último monitoreo válido

Módulo: entre `completed` con métricas, el de `started_at` máximo.

### 5.2. KPI "Frutos detectados"

- **Módulo:** `fruits_detected = last_valid.total_tomatoes`;
  `fruits_delta = last_valid.total_tomatoes − prev_valid.total_tomatoes` (o None).
- **"Todos":** `Σ last_valid_m.total_tomatoes`; `contributing_modules` = módulos
  con último válido; **sin delta agregado**.

### 5.3. Sanidad agregada (ponderada por conteos)

```
H = Σ healthy_count ; U = Σ unhealthy_count ; T = Σ total_tomatoes
pct_healthy   = 100*H/T ; pct_unhealthy = 100*U/T          (T>0)
health_other_pct = max(0, 100 - pct_healthy - pct_unhealthy)   # si >0.05, se muestra
```

### 5.4. Madurez agregada (conteos reales sumados)

```
count_stage[s] = Σ_m count_stage_m[s]          # conteos reales de InspectionResult
Csum = Σ_m maturity_covered_m
pct_stage[s] = 100*count_stage[s]/Csum         (Csum>0)
predominant = predominant_stages(count_stage)  # ver §5.4.1 (lista; puede empatar)
harvestable_share = (count_stage[light_red]+count_stage[red]) / Csum
```

Solo participan en la agregación las observaciones con `coverage_known=True`
(conteos reales). Las observaciones con solo fallback no aportan conteos ni
cobertura al agregado. Nunca se reconstruyen conteos desde `pct_*` ni se
promedian porcentajes.

#### 5.4.1. Predominante determinista con empate

```
max_count = max(count_stage.values())
predominant_stages = [s for s in STAGE_ORDER if count_stage[s] == max_count and max_count > 0]
# STAGE_ORDER = [green, breaker, turning, pink, light_red, red]  (orden fenológico fijo)
```

- Si hay **un** estado con el máximo → lista de un elemento; UI:
  "Madurez predominante: Turning".
- Si hay **empate** (≥2 con el mismo conteo máximo) → lista con todos, en orden
  fenológico determinista; UI: "Madurez predominante: Mixto — Turning / Pink".
- Si `max_count == 0` (sin cobertura) → lista vacía; UI: "Sin datos de madurez".

No se elige arbitrariamente por orden del diccionario.

### 5.5. "Último monitoreo" en "Todos"

El monitoreo válido con `started_at` máximo **entre todos los módulos** (el más
nuevo de cualquier módulo). No es una agregación.

### 5.6. Frescura del agregado "Todos"

`freshness_from/to` = mínimo/máximo `started_at` de los últimos monitoreos
válidos usados en el KPI. La UI lo rotula "último dato disponible de cada módulo".

---

## 6. Maturity index (promedio ordinal)

### 6.1. Escala ordinal Green→Red

| Estado | v |
|---|---|
| green | 0.0 |
| breaker | 0.2 |
| turning | 0.4 |
| pink | 0.6 |
| light_red | 0.8 |
| red | 1.0 |

### 6.2. Fórmula (por monitoreo o por scope agregado)

```
MI = ( Σ_s count[s] * v(s) ) / maturity_covered        si maturity_covered > 0
MI = None                                              si maturity_covered == 0
```

`MI ∈ [0,1]`. Es un **promedio ordinal** de madurez. **No** es el porcentaje de
frutos Light Red/Red.

### 6.3. harvestable_share (magnitud separada)

```
harvestable_share = (count[light_red] + count[red]) / maturity_covered
```

Información complementaria real. Se muestra junto al índice, claramente
diferenciada. `MI_TARGET = 0.8` se documenta como "nivel medio de madurez
equivalente a Light Red" (convención operacional), NO como harvestable_share.

### 6.4. Ejemplo

Conteos: green=2, breaker=1, turning=3, pink=2, light_red=1, red=1; covered=10.
```
Σ count*v = 2*0 + 1*0.2 + 3*0.4 + 2*0.6 + 1*0.8 + 1*1.0 = 4.4
MI = 4.4/10 = 0.44 ; predominant = turning
harvestable_share = (1+1)/10 = 0.20  (20%)
```
MI=0.44 y harvestable_share=20% son magnitudes distintas.

---

## 7. Coverage ratio

```
coverage_ratio_i = maturity_covered_i / total_tomatoes_i        (total_tomatoes_i > 0)
```

Mide la fracción de frutos del monitoreo con madurez clasificable. Se usa como
**peso** de la observación en la tendencia (§8) y como evidencia objetiva en la
UI (cobertura media).

---

## 8. Tendencia ponderada por cobertura (WLS determinista)

Observaciones utilizables de un módulo: `completed`, con métricas, `started_at`,
`maturity_covered > 0`, `total_tomatoes > 0`. **Sin corte de cobertura mínima**:
la cobertura entra como peso.

Para cada observación `i`: `x_i` = días desde la primera (`x_0 = 0`),
`y_i = MI_i`, `w_i = coverage_ratio_i`.

**Mínimos cuadrados ponderados (WLS):**

```
W    = Σ w_i
x̄_w  = (Σ w_i x_i) / W
ȳ_w  = (Σ w_i y_i) / W
Sxx_w = Σ w_i (x_i - x̄_w)^2
Sxy_w = Σ w_i (x_i - x̄_w)(y_i - ȳ_w)

slope     = Sxy_w / Sxx_w        (MI por día)     si Sxx_w > 0
intercept = ȳ_w - slope * x̄_w
```

- Determinista y reproducible. Una observación con baja cobertura influye menos
  que una con cobertura alta (Req 9.2, 16.15).
- Si `Sxx_w == 0` (todas las observaciones en la misma fecha, o una sola) → no
  hay dispersión temporal → "No estimable".

---

## 9. Ventana de cosecha derivada de la variabilidad real

### 9.1. Cruce del target sobre la recta ajustada

```
t_target   = (MI_TARGET - intercept) / slope        (slope != 0)
days_until = t_target - x_last
```

Se usa la **recta ajustada** (intercept+slope), NO `(MI_TARGET − MI_last)/slope`.

`days_until ≤ 0` por sí solo **NO** implica objetivo alcanzado: el cruce de la
recta ajustada puede quedar en el pasado mientras la observación más reciente
todavía tenga `MI_last < MI_TARGET`. La decisión de estado se toma en §9.4 según
`MI_last`, `slope` y la posición de `t_target`/`t_high` respecto a `x_last`.

### 9.2. Banda empírica desde residuales ponderados

Residual de cada observación respecto a la recta:

```
r_i = y_i - (intercept + slope * x_i)
```

Dispersión ponderada de residuales (desviación estándar ponderada):

```
s = sqrt( Σ w_i r_i^2 / Σ w_i )
```

`s` es la variabilidad **real** del ajuste (unidades de MI). No es un intervalo
de confianza estadístico y no se nombra como tal (Req 9.4).

### 9.3. Ventana = cruce de MI_TARGET con la banda ±s

Se trasladan verticalmente dos rectas paralelas a la ajustada, a ±s. El cruce de
`MI_TARGET` con cada una da los extremos de la ventana:

```
Recta superior: (intercept + s) + slope * t   → alcanza MI_TARGET antes
  t_low  = (MI_TARGET - s - intercept) / slope
Recta inferior: (intercept - s) + slope * t   → alcanza MI_TARGET después
  t_high = (MI_TARGET + s - intercept) / slope

days_low  = t_low  - x_last
days_high = t_high - x_last
window     = [ started_at_last + days_low ,  started_at_last + days_high ]
```

- Si `s == 0` (ajuste perfecto, residuales nulos) → ventana degenera a un único
  día (`days_low == days_high == days_until`). Se **acepta** la ventana
  degenerada, pero la UI NO la presenta como fecha garantizada: se comunica como
  "Estimación central: alrededor del DD/MM/YYYY" + "Sin dispersión observada en
  los monitoreos utilizados", manteniendo el disclaimer general de que no es un
  pronóstico agronómico (§11.3).
- La amplitud proviene **solo** de `s` (dispersión real) y `slope`. No hay
  constante ±25%.
- `days_low` puede ser ≤ 0 mientras `days_high` > 0: significa "podría estar
  entrando ya en el objetivo; a más tardar hacia `days_high`". La UI lo comunica
  como ventana que empieza "ahora" (fecha del último monitoreo). Este es
  precisamente el caso `t_target ≤ x_last` con `t_high > x_last` descrito en §9.4.

### 9.4. Estados de salida (lógica obligatoria)

El estado se decide sobre `MI_last`, `slope` y la posición del cruce ajustado
`t_target` (y de `t_high`) respecto a `x_last`. **Nunca** se declara
`target_reached` solo porque `days_until ≤ 0`.

```
# Guardas de datos (previas):
if n < MIN_OBS (3)  o  ninguna obs con maturity_covered > 0:
    status = "insufficient"
elif Sxx_w == 0:                      # sin dispersión temporal (misma fecha)
    status = "not_estimable"

# A) La observación más reciente ya alcanzó el nivel objetivo:
elif MI_last >= MI_TARGET:
    status = "target_reached"

# B) Aún por debajo y sin progresión que proyectar:
elif MI_last < MI_TARGET and slope <= 0:
    status = "not_estimable"

# C) Aún por debajo, con progresión positiva:
else:  # MI_last < MI_TARGET and slope > 0
    calcular t_target (§9.1) y la banda residual s (§9.2), t_low/t_high (§9.3)
    if t_target > x_last:
        status = "window"                    # comportamiento normal
        window_start = started_at_last + max(0, days_low)  (>=0 → "ahora")
        window_end   = started_at_last + days_high
    else:  # t_target <= x_last pero MI_last < MI_TARGET (ajuste proyectaba el pasado)
        if t_high > x_last:
            status = "window"
            window_start = started_at_last     # "ahora" / fecha del último monitoreo
            window_end   = started_at_last + (t_high - x_last)
        else:  # t_high <= x_last
            status = "not_estimable"
            reason = ("inconsistencia entre el ajuste histórico y la observación "
                      "actual: el modelo ajustado proyectaba el objetivo en el "
                      "pasado, pero el último dato observado sigue por debajo.")
```

Notas:
- `target_reached` se decide **solo** por `MI_last >= MI_TARGET` (caso A), nunca
  por `days_until <= 0`.
- El nombre de estado `target_reached` se comunica en UI como **"Nivel medio de
  madurez objetivo alcanzado"**, NO como "cosechable ahora"; `harvestable_share`
  se muestra aparte (§6.3, §11.3).
- `HarvestResult` incorpora un campo `reason: Optional[str]` para el subcaso de
  inconsistencia del bloque C.

### 9.5. Suficiencia/soporte de datos (evidencia objetiva, sin "confianza")

`HarvestResult` comunica:
- `n_observations` → "Estimación basada en N monitoreos".
- `mean_coverage_ratio = mean(coverage_ratio_i)` → "Cobertura media de madurez: X %".
- `slope_per_day`, `mi_last` como datos crudos disponibles.

No se emiten categorías "alta/media/baja".

### 9.6. Ejemplo numérico completo (ventana derivada de datos)

Observaciones utilizables de un módulo (día `x`, `MI`, `coverage_ratio` = peso `w`):

| i | x (días) | MI (y) | w |
|---|---|---|---|
| 0 | 0 | 0.30 | 0.6 |
| 1 | 7 | 0.42 | 0.8 |
| 2 | 14 | 0.55 | 1.0 |
| 3 | 21 | 0.66 | 0.9 |

```
W = 0.6+0.8+1.0+0.9 = 3.3
Σ w·x = 0.6*0 + 0.8*7 + 1.0*14 + 0.9*21 = 0 + 5.6 + 14 + 18.9 = 38.5
x̄_w = 38.5/3.3 = 11.6667
Σ w·y = 0.6*0.30 + 0.8*0.42 + 1.0*0.55 + 0.9*0.66
      = 0.18 + 0.336 + 0.55 + 0.594 = 1.66
ȳ_w = 1.66/3.3 = 0.503030

dx_i = x_i - x̄_w:  -11.6667, -4.6667, 2.3333, 9.3333
dy_i = y_i - ȳ_w:  -0.203030, -0.083030, 0.046970, 0.156970

Sxx_w = Σ w·dx^2
  0.6*(136.111) + 0.8*(21.778) + 1.0*(5.4444) + 0.9*(87.111)
  = 81.667 + 17.422 + 5.444 + 78.400 = 182.933
Sxy_w = Σ w·dx·dy
  0.6*(-11.6667*-0.203030)=0.6*(2.36869)=1.42121
  0.8*(-4.6667*-0.083030)=0.8*(0.38747)=0.30998
  1.0*( 2.3333* 0.046970)=1.0*(0.10960)=0.10960
  0.9*( 9.3333* 0.156970)=0.9*(1.46506)=1.31855
  Sxy_w = 1.42121+0.30998+0.10960+1.31855 = 3.15934

slope = 3.15934 / 182.933 = 0.017271 MI/día
intercept = 0.503030 - 0.017271*11.6667 = 0.503030 - 0.201498 = 0.301532
```

Cruce del target sobre la recta ajustada (MI_TARGET = 0.8; x_last = 21):
```
t_target   = (0.8 - 0.301532)/0.017271 = 0.498468/0.017271 = 28.862 días
days_until = 28.862 - 21 = 7.862 días
```

Residuales y dispersión ponderada:
```
fit_i = intercept + slope*x_i:
  x=0 : 0.301532
  x=7 : 0.301532 + 0.120897 = 0.422429
  x=14: 0.301532 + 0.241794 = 0.543326
  x=21: 0.301532 + 0.362691 = 0.664223
r_i = y_i - fit_i:
  0.30 - 0.301532 = -0.001532
  0.42 - 0.422429 = -0.002429
  0.55 - 0.543326 =  0.006674
  0.66 - 0.664223 = -0.004223
Σ w·r^2:
  0.6*(2.347e-6)=1.408e-6
  0.8*(5.900e-6)=4.720e-6
  1.0*(4.454e-5)=4.454e-5
  0.9*(1.7834e-5)=1.6051e-5
  Σ = 6.672e-5
s = sqrt(6.672e-5 / 3.3) = sqrt(2.022e-5) = 0.004497 MI
```

Ventana (cruce de MI_TARGET con la banda ±s):
```
t_low  = (0.8 - 0.004497 - 0.301532)/0.017271 = 0.493971/0.017271 = 28.601 días
t_high = (0.8 + 0.004497 - 0.301532)/0.017271 = 0.502965/0.017271 = 29.122 días
days_low  = 28.601 - 21 = 7.601 días
days_high = 29.122 - 21 = 8.122 días
```

Resultado: `status="window"`, ventana ≈ **[+7.6 días, +8.1 días]** desde el
último monitoreo. `n_observations = 4`; `mean_coverage_ratio = (0.6+0.8+1.0+0.9)/4
= 0.825 → "Cobertura media de madurez: 82 %"`. La banda es estrecha porque los
datos siguen casi perfectamente la recta (residuales muy pequeños): la amplitud
refleja la variabilidad real, no una constante.

> Contraste: si los MI fueran más ruidosos (residuales mayores), `s` crecería y
> la ventana se ensancharía automáticamente. Ese es el comportamiento deseado.

---

## 10. "Todos": semántica temporal cerrada

- **KPIs "Todos":** último monitoreo válido por módulo, conteos agregados
  (§5). Rotulado "último dato disponible de cada módulo" + `contributing_modules`
  + rango de fechas (§5.6).
- **Evolución / Sanidad / Maturity index en "Todos":** UNA gráfica, con **una
  serie independiente por módulo**, cada una con sus **fechas reales**. NO se
  crea un total histórico del invernadero ni puntos agregados por fecha.
- **Cosecha en "Todos":** resumen por módulo (`HarvestSummaryAll`), sin fecha
  única del invernadero. La unidad de cálculo sigue siendo el módulo.

---

## 11. UI (800×480, offline, touch)

### 11.1. Estructura vertical aprobada

```
[ Selector Invernadero ]        (select nativo, full-width, ≥48px)
[ Selector Módulo: Todos / … ]  (select nativo dependiente)
[ KPIs contextuales ]           (4 tarjetas, grid 2×2)
[ Tabs: Evolución | Sanidad | Madurez ]
[ UNA gráfica principal grande de la pestaña activa ]
[ Próxima cosecha ]             (ventana + evidencia objetiva + disclaimer)
[ Bloque operativo reorganizado: alertas, último monitoreo, actividades, sync ]
```

- Una sola gráfica grande visible a la vez. Cambio de pestaña client-side
  (mostrar/ocultar contenido ya renderizado) → inmediato y offline; sin fetch.
- Cambio de invernadero/módulo → recarga por querystring
  (`/dashboard?greenhouse_id=..&module_id=..`).
- **Series por módulo:** en "Todos", cada `ModuleSeries` se dibuja con leyenda
  por módulo. La UI aclara "series por módulo (fechas reales de cada uno)".

### 11.2. Gráficas sin librerías externas

- Distribución de madurez: reutilizar `partials/maturity_bar.html` con conteos
  reales.
- Series (evolución/sanidad/maturity index): SVG inline o barras `div width%`
  (patrón existente). Multi-serie por módulo con colores distinguibles.
- Reutilizar variables CSS `--maturity-*` y colores de estado. Sin dependencias.

### 11.3. Cosecha en UI

- `status="window"`: ventana como rango de fechas (o que empieza "ahora" /
  fecha del último monitoreo si `days_low ≤ 0`).
- `status="target_reached"`: **"Nivel medio de madurez objetivo alcanzado"** (NO
  "cosechable ahora", NO sinónimo de MI≥0.8 como %). `harvestable_share` se
  muestra **aparte**, con su denominador explícito (§11.5).
- `status="insufficient"`: "Datos insuficientes (se requieren ≥3 monitoreos
  utilizables)".
- `status="not_estimable"`: mensaje según la causa; si viene `reason` de
  inconsistencia (§9.4-C), se comunica que el ajuste histórico proyectaba el
  objetivo en el pasado pero el último dato sigue por debajo.
- Ventana degenerada (`is_degenerate_window`, `s==0`): NO se presenta como fecha
  garantizada. Wording: "Estimación central: alrededor del DD/MM/YYYY" + "Sin
  dispersión observada en los monitoreos utilizados".
- Evidencia objetiva siempre: "Estimación basada en N monitoreos", "Cobertura
  media de madurez: X %". Disclaimer general: "Estimación derivada de los
  monitoreos visuales del sistema; no es un pronóstico agronómico." `MI_TARGET`
  documentado como convención operacional (nivel medio ≡ Light Red).

### 11.4. Madurez predominante en UI

- Un estado: "Madurez predominante: Turning".
- Empate (lista `predominant_maturity` con >1): "Madurez predominante: Mixto —
  Turning / Pink" (orden fenológico).
- Lista vacía / sin cobertura: "Sin datos de madurez".

### 11.5. harvestable_share en UI (denominador explícito)

Siempre se rotula con su denominador real (`maturity_covered`), nunca sobre
`total_tomatoes`:

> "X % de los frutos **con madurez clasificable** están en Light Red/Red"

NO se escribe "X % de los frutos". Cuando `coverage_known=False` (solo fallback),
NO se muestra harvestable_share.

### 11.6. Accesibilidad / touch

Botones/selects ≥44px; texto ≥16px; contraste AA; `role="img"`+`aria-label` en
gráficas.

---

## 12. Plan de bulk loading / repositorios (evitar N+1)

### 12.1. Interfaces afectadas (ampliación de API, NO esquema)

- `src/domain/repositories/monitoring_metrics_repository.py` (ABC):
  añadir `get_by_monitoring_ids(ids: list[int]) -> dict[int, MonitoringMetrics]`.
- `src/domain/repositories/inspection_result_repository.py` (ABC):
  añadir `get_by_monitoring_ids(ids: list[int]) -> dict[int, list[DetectionInspectionResult]]`.

### 12.2. Implementaciones SQL afectadas

- `sql_monitoring_metrics_repository.py`:
  `SELECT ... WHERE monitoring_id IN (:ids)`, agrupar en dict por `monitoring_id`.
- `sql_inspection_result_repository.py`:
  `JOIN snapshots ON inspection_results.snapshot_id = snapshots.id
   WHERE snapshots.monitoring_id IN (:ids)`, agrupar en dict por `monitoring_id`.
- Manejo de lista vacía → `{}`; ids inexistentes → ausentes del dict.
- Otros repos con implementaciones deben cumplir la nueva ABC (p. ej. cualquier
  stub/fake): se añade el método correspondiente para no romper la interfaz.

### 12.3. Coste (estimación, sin medir aún)

- Antes (N+1): `2·M·K` queries (M módulos, K monitoreos por módulo).
- Después (bulk): recolección de monitoreos = 1 query por módulo
  (`get_by_module`) + **2 queries totales** (`metrics IN`, `results IN`).
- La medición formal en RPi se hace en la fase de verificación (tasks 9),
  documentando el nº real de queries por render.

### 12.4. Aislamiento

Los ids pasados a los métodos bulk provienen exclusivamente de monitoreos de
módulos del usuario (resueltos vía `get_all_by_owner` → `get_by_greenhouse` →
`get_by_module`). Los métodos bulk no introducen scope de usuario propio; el
scope lo garantiza el conjunto de ids.

### 12.5. Alternativa considerada

Cargar `InspectionResult` solo para monitoreos que entran en distribución/series
(en scope módulo son todos; en "Todos", los últimos válidos + los de las series).
Se mantiene, pero **siempre** vía el método bulk (no N+1). El historial de
cálculo NO se recorta por una constante (Req 13.6); si se limitan puntos en la
gráfica, es decisión de presentación documentada.

---

## 13. Historial de cálculo vs puntos representados (performance)

- **Historial disponible para cálculo:** todas las observaciones utilizables del
  módulo (sin recorte por constante arbitraria). La cosecha y la tendencia usan
  todo el historial válido.
- **Puntos representados en la gráfica:** si por legibilidad/performance en RPi
  se necesitara limitar los puntos dibujados, será una **decisión de
  presentación** explícita y documentada (p. ej. "mostrar los últimos K puntos"),
  que NO altera el cálculo de cosecha. Primero se mide el coste real; no se fija
  un límite a priori.

---

## 14. Comportamiento offline

- Render server-side con datos locales (SQLite). Sin fetch de red para analítica.
- Único fetch existente (`/api/sync/status`, badge de sync) se conserva y degrada
  en silencio offline. No se añaden fetches obligatorios.
- Test: el HTML no contiene `http(s)://`, `cdn`, `googleapis`, etc.

---

## 15. Aislamiento multiusuario (implementación)

- Ruta parte de `gh_repo.get_all_by_owner(user.id)` y solo desciende por FK.
- `greenhouse_id`/`module_id` de querystring se validan contra el conjunto del
  usuario; si no pertenecen, se ignoran (no encontrado) y se cae al default
  determinista (`min` por id).
- Ningún método sin scope (`list_all`, `get_active`) se usa. Los bulk reciben
  solo ids del usuario.
- Test: dos usuarios; A nunca ve datos de B ni forzando querystrings.

---

## 16. Edge cases

| Caso | Comportamiento |
|---|---|
| Usuario sin invernaderos | Empty state; sin selectores; sin errores. |
| Invernadero sin módulos | Selector módulo solo "Todos"; KPIs empty. |
| Módulo sin monitoreos válidos | KPIs "Sin monitoreos válidos"; series vacías; cosecha "insufficient". |
| 1 monitoreo válido | KPI con ese valor; sin delta; serie 1 punto; cosecha "insufficient". |
| 2 monitoreos | Delta; serie 2 puntos; cosecha "insufficient" (n<3). |
| Sin `MonitoringMetrics` | Ese monitoreo excluido de válidos. |
| Sin `InspectionResult` para un monitoreo válido | Distribución usa fallback `pct_*` para visualización; coverage/counts reales = None → no entra en tendencia. |
| Salud 0/100 y 100/0 | Barra correcta; sin división por cero. |
| Suma salud <100 (unknown) | "otros N%". |
| Madurez cobertura parcial | Distribución sobre `maturity_covered` + "X de Y". |
| Madurez totalmente no clasificable | "Sin datos de madurez"; sin índice/predominante; cosecha "insufficient". |
| MI_last ≥ target | "target_reached" (único disparador). |
| MI_last < target y slope ≤ 0 | "not_estimable". |
| Sin dispersión temporal (Sxx_w=0 / misma fecha) | "not_estimable". |
| MI_last < target, slope>0, t_target > x_last | "window" normal. |
| MI_last < target, slope>0, t_target ≤ x_last, t_high > x_last | "window" que empieza "ahora". |
| MI_last < target, slope>0, t_high ≤ x_last | "not_estimable" (inconsistencia ajuste vs observación). |
| days_until ≤ 0 con MI_last < target | NO es target_reached; se resuelve por los casos de arriba. |
| Residuales nulos (s=0) | Ventana puntual (`is_degenerate_window`), NO garantía. |
| Empate de madurez predominante | Lista de estados en orden fenológico → "Mixto". |
| Madurez solo con fallback pct_* | Distribución visual; "Cobertura no disponible"; no entra en índice/WLS/cosecha. |
| Gaps temporales irregulares | WLS sobre días reales; correcto. |
| "Todos" con módulos en fases distintas | Series por módulo; resumen de cosecha por módulo. |

---

## 17. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Coste E/S de `InspectionResult` en RPi | Latencia | Lectura bulk `IN (...)`; medir nº de queries; historial de cálculo no recortado, presentación sí documentable. |
| Confundir MI con harvestable_share | Malinterpretación | Magnitudes separadas y rotuladas; tests que las distinguen. |
| Denominadores salud vs madurez | Conclusiones falsas | Etiquetar cobertura/denominador; tests de semántica. |
| Ventana malinterpretada como IC estadístico | Riesgo académico | Nombrarla "ventana derivada de la variabilidad observada"; disclaimer; evidencia objetiva. |
| Sumar históricos como frutos únicos | KPI incorrecto | Regla "último válido"; test dedicado. |
| Series agregadas artificiales en "Todos" | Semántica falsa | Series por módulo con fechas reales; test dedicado. |
| Nueva ABC de repos rompe fakes/stubs | Tests rojos | Implementar el método bulk en todas las implementaciones/fakes usadas por tests. |

---

## 18. Decisiones cerradas (no requieren nueva aprobación)

Todas las decisiones de la lista "Decisiones ya aprobadas" del prompt están
incorporadas: conteos reales de `InspectionResult`; MI y harvestable_share
separados; `MI_TARGET=0.8` como nivel medio ≡ Light Red; proyección sobre recta
ajustada; sin thresholds arbitrarios (`MIN_OBS=3` y `MI_TARGET=0.8` son los
únicos parámetros aprobados; el resto se deriva de los datos); ventana desde
dispersión de residuales; series por módulo en "Todos"; default determinista por
`min(id)`; bulk `IN (...)`; sin cambios de esquema; offline/local.

No quedan decisiones abiertas pendientes de aprobación en este diseño. Cualquier
límite de **presentación** de puntos (Req 13.6) se decidirá tras medir el coste
en la fase de verificación y se documentará entonces.
