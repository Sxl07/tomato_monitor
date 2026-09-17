# Tareas — Spec 024: Dashboard analítico contextual y estimación de próxima cosecha

> Regla de ejecución: cada bloque escribe tests **antes o durante** la
> implementación y verifica antes de pasar al siguiente. Cambios pequeños,
> revisables, reversibles. **No** modificar esquema de BD (ampliar API de
> repositorios con lecturas bulk `IN (...)` sí está permitido). **No** romper
> aislamiento multiusuario ni funcionalidad operativa existente.
>
> Parámetros aprobados: `MIN_OBS = 3`, `MI_TARGET = 0.8`. No hay otros thresholds
> arbitrarios; el resto se deriva de los datos.

---

## Bloque 0 — BASELINE (PRIMERA tarea, ANTES de tocar código productivo)

> Esta es la **primera** acción de la implementación. No se escribe ni modifica
> código productivo antes de completarla.

- [ ] 0.1 Ejecutar `python -m pytest -q` sobre la rama `feature/024-analytical-dashboard`
      **antes de cualquier implementación**.
- [ ] 0.2 Registrar el baseline exacto: número de `passed`, `failed`,
      `deselected`, `warnings`, y la **lista exacta de fallos preexistentes**
      (nombres de tests). Guardar este registro (p. ej. en el PR/spec notes) para
      la comparación final.
- [ ] 0.3 Solo después de registrar el baseline, comenzar la implementación
      (Bloque 1 en adelante).

> Verificación final (Bloque 12): regresión dirigida → full suite → comparación
> contra ESTE baseline inicial → corregir únicamente regresiones **nuevas** de
> Spec 024. NO reparar fallos preexistentes no relacionados (los mocks del
> dashboard SÍ se actualizan por ser parte directa de la spec).

## Bloque 1 — Preparación y contrato de datos (sin lógica de negocio)

- [ ] 1.1 Definir los dataclasses de design.md §3.3 (`MaturityCounts`,
      `MaturityDistributionFallback`, `ModuleValidMonitoring`, `ModuleScopeData`,
      `ScopeData`, `SeriesPoint`, `ModuleSeries`, `KpiBlock`, `HarvestResult`
      (con `reason`, `is_degenerate_window`), `HarvestSummaryAll`,
      `AnalyticsContext`) en `dashboard_analytics_dtos.py`.
- [ ] 1.2 Fijar `MIN_OBS` y `MI_TARGET` como constantes del módulo de estimación
      (no hardcodeadas en profundidad).
  - Tests: instanciación y valores por defecto; `MaturityCounts.covered`;
    `ModuleValidMonitoring.coverage_known` y `coverage_ratio` (incl.
    `total_tomatoes == 0` → None; solo-fallback → None).

## Bloque 2 — Conteos de madurez desde InspectionResult (Python puro)

- [ ] 2.1 `maturity_stats.py`: `counts_from_results(results) -> MaturityCounts`
      (contar por `maturity_stage != None`, ignorar estados desconocidos).
- [ ] 2.2 `distribution_pcts(counts)`, `predominant_stages(counts)` (design §5.4.1;
      lista, orden fenológico, empate → varios), `harvestable_share(counts)`
      (= `(light_red+red)/covered`, None si covered=0).
- [ ] 2.3 Fallback: `distribution_from_pcts(pcts)` para
      `MaturityDistributionFallback` (SOLO visual, `coverage_known=False`,
      design §4.1). No calcula conteos/cobertura/índice/harvestable_share.
  - Tests (Req 16.8, 16.9, 16.25, 16.26, 16.27): distribución normal; cobertura
    parcial; totalmente no clasificable (covered=0); predominante correcto;
    **empate → lista "Mixto"**; harvestable_share separado del índice y con
    denominador `maturity_covered`; fallback marca `coverage_known=False` y no
    deriva magnitudes reales.

## Bloque 3 — Maturity index (Python puro)

- [ ] 3.1 `maturity_index(counts) -> float | None`: `Σ count*v / covered`
      con escala 0.0→1.0 (green=0…red=1). None si covered=0.
  - Tests deterministas (Req 16.9): todo verde→0.0; todo rojo→1.0; ejemplo
    design.md §6.4 → 0.44 (y harvestable_share=0.20, magnitudes distintas);
    covered=0 → None.

## Bloque 4 — Reglas de agregación (Python puro)

- [ ] 4.1 `select_last_valid` y `prev_valid` por módulo.
- [ ] 4.2 KPI frutos: módulo (último válido + delta) y "Todos" (Σ últimos válidos,
      `contributing_modules`, sin delta).
- [ ] 4.3 Sanidad agregada ponderada por conteos + `health_other_pct` (design §5.3).
- [ ] 4.4 Madurez agregada: suma de **conteos reales** por estado (solo obs con
      `coverage_known=True`), pct desde conteos, predominante (lista/Mixto),
      harvestable_share agregado (design §5.4). NO reconstruir desde `pct_*`.
- [ ] 4.5 "Último monitoreo" en "Todos" = más reciente entre módulos (design §5.5).
- [ ] 4.6 Frescura `freshness_from/to` (design §5.6).
  - Tests (Req 16.5, 16.7, 16.8, 16.11, 16.12, 16.18):
    - 1/2/≥3 monitoreos; KPI usa último válido (caso que falla si se suma
      histórico); sanidad 0/100, 100/0, suma <100; agregación "Todos" ponderada
      por conteos reales (dos módulos de tamaños distintos → NO promedio de %);
      módulo sin válido excluido; contributing_modules y rango de fechas.

## Bloque 5 — Series por módulo (Python puro)

- [ ] 5.1 `evolution_series(scope_data) -> list[ModuleSeries]`: en módulo, 1 serie
      (frutos por monitoreo); en "Todos", **N series** (una por módulo) con fechas
      reales. Sin totales históricos agregados.
- [ ] 5.2 `health_series` (pct_healthy por monitoreo) y `maturity_index_series`
      (índice por monitoreo; solo puntos con `coverage_known=True`), ambas
      series por módulo.
  - Tests (Req 16.10, 16.13): 0/1/2/≥3 puntos; cambio vs anterior; "Todos" =
    series independientes por módulo con fechas reales (no un total del
    invernadero); gaps irregulares mantienen fechas.

## Bloque 6 — Harvest estimator (Python puro, determinista)

- [ ] 6.1 Observaciones utilizables (completed, métricas, started_at,
      `coverage_known=True` con `maturity_covered>0`, `total_tomatoes>0`). Sin
      corte de cobertura mínima. Las observaciones solo-fallback NO entran.
- [ ] 6.2 Tendencia WLS ponderada por cobertura (design §8): `slope`, `intercept`,
      `Sxx_w`. Guarda `Sxx_w==0`.
- [ ] 6.3 Cruce sobre recta ajustada (design §9.1): `t_target=(MI_TARGET-intercept)/slope`,
      `days_until=t_target-x_last`. NO `(MI_TARGET-MI_last)/slope`.
- [ ] 6.4 Banda empírica desde residuales ponderados (design §9.2–9.3):
      `s=sqrt(Σ w r² / Σ w)`, ventana por cruce de MI_TARGET con banda ±s;
      marcar `is_degenerate_window` si `s==0`.
- [ ] 6.5 **Estados de salida (lógica obligatoria, design §9.4):**
      - `insufficient`: n<MIN_OBS o ninguna obs utilizable.
      - `not_estimable`: `Sxx_w==0`.
      - `target_reached`: **solo** si `MI_last ≥ MI_TARGET`.
      - `not_estimable`: `MI_last < MI_TARGET` y `slope ≤ 0`.
      - `MI_last < MI_TARGET` y `slope > 0`:
        - `t_target > x_last` → `window` normal.
        - `t_target ≤ x_last` y `t_high > x_last` → `window` desde "ahora".
        - `t_target ≤ x_last` y `t_high ≤ x_last` → `not_estimable` con `reason`
          de inconsistencia.
      Evidencia objetiva (design §9.5): `n_observations`, `mean_coverage_ratio`,
      `slope_per_day`, `mi_last`.
- [ ] 6.6 Resumen "Todos" `HarvestSummaryAll` por módulo (design §10).
  - Tests deterministas (Req 16.14–16.17, 16.22–16.24):
    - Ejemplo design §9.6 reproduce slope≈0.017271, s≈0.004497, ventana≈[+7.6, +8.1] días.
    - <3 obs → insufficient; Sxx_w=0 (misma fecha) → not_estimable;
      slope≤0 y MI_last<target → not_estimable.
    - **target_reached SOLO por MI_last≥MI_TARGET**: caso con `days_until ≤ 0`
      pero `MI_last < MI_TARGET` que NO debe dar target_reached.
    - `t_target ≤ x_last` con `t_high > x_last` → window desde "ahora";
      con `t_high ≤ x_last` → not_estimable (reason inconsistencia).
    - `s==0` → `is_degenerate_window=True` (no garantía).
    - ponderación: baja cobertura influye menos que alta cobertura.
    - cruce sobre recta ajustada (test que falla si se usa la fórmula cruda).
    - "Todos": módulos en categorías distintas → resumen sin fecha única.

## Bloque 7 — Repositorios bulk (ampliación de API, sin esquema)

- [ ] 7.1 ABC `MonitoringMetricsRepository.get_by_monitoring_ids(ids) -> dict`
      + impl SQL `WHERE monitoring_id IN (:ids)` (design §12).
- [ ] 7.2 ABC `InspectionResultRepository.get_by_monitoring_ids(ids) -> dict`
      + impl SQL `JOIN snapshots ... WHERE snapshots.monitoring_id IN (:ids)`.
- [ ] 7.3 Actualizar cualquier fake/stub de repos usado en tests para cumplir la
      nueva ABC.
  - Tests (Req 16.21): normal (varios ids), lista vacía → `{}`, ids inexistentes
    ausentes del dict, agrupación correcta por monitoring_id.

## Bloque 8 — AnalyticsService (orquestación en aplicación, sin BD)

- [ ] 8.1 `AnalyticsService.build_analytics(scope_data, today) -> AnalyticsContext`
      componiendo KPIs (B4), series (B5) y cosecha (B6). Sin acceso a BD.
  - Tests: dado `scope_data` armado a mano, contexto correcto; empty states; no
    se inventan datos; scope módulo vs "Todos".

## Bloque 9 — Carga de datos en la ruta (repos + aislamiento + bulk, orden obligatorio)

- [ ] 9.1 En `agricultural_ui.py`, resolver scope desde querystring validando
      pertenencia; default determinista `min(greenhouses, key=lambda gh: gh.id)`
      (design §15).
- [ ] 9.2 **Orden obligatorio de carga (design §3.2):**
      1. candidatos = monitoreos con `status=="completed"` y `started_at` (aún NO
         "válidos").
      2. `candidate_ids`.
      3. bulk `metrics_by_id = metrics_repo.get_by_monitoring_ids(candidate_ids)`.
      4. válidos = candidatos cuyo id ∈ `metrics_by_id`.
      5. `valid_ids`.
      6. bulk `results_by_id = inspection_repo.get_by_monitoring_ids(valid_ids)`.
      7. derivar `MaturityCounts` (o fallback si un válido no tiene results).
      No etiquetar "válido" antes de confirmar `MonitoringMetrics`.
- [ ] 9.3 Construir `ScopeData`, llamar a `AnalyticsService` y a `DashboardService`
      (operativo); pasar ambos al template.
  - Tests (Req 16.1–16.4, 16.6): selección invernadero/módulo; "Todos"; default
    determinista; aislamiento (A no ve B ni forzando querystring); empty state;
    monitoreo completed **sin métricas** excluido de válidos; verificación del
    orden candidatos→metrics→válidos→results y uso de métodos bulk.

## Bloque 10 — UI: selectores, KPIs, pestañas, cosecha (Jinja + CSS/SVG/JS local)

- [ ] 10.1 Rediseñar `dashboard.html` (design §11.1): selectores, 4 KPIs (2×2),
      tabs Evolución/Sanidad/Madurez, una gráfica grande por pestaña, sección
      cosecha, bloque operativo reorganizado.
- [ ] 10.2 Cambio de pestaña client-side (JS local, sin red); selectores recargan
      por querystring.
- [ ] 10.3 Gráficas locales: reutilizar `maturity_bar.html` (conteos reales o
      fallback visual con etiqueta "Cobertura no disponible"); partials
      multi-serie por módulo para evolución/sanidad/índice (SVG o `div width%`).
      Leyenda por módulo en "Todos".
- [ ] 10.4 Sección cosecha (design §11.3): window/target_reached/insufficient/
      not_estimable con sus textos; `target_reached` = "Nivel medio de madurez
      objetivo alcanzado" (no "cosechable ahora"); ventana degenerada (`s==0`)
      como "estimación central" sin garantía; evidencia objetiva; disclaimer;
      MI_TARGET como convención.
- [ ] 10.5 Predominante en UI (design §11.4): "Mixto — A / B" en empate.
      harvestable_share (design §11.5) rotulado con denominador explícito
      "X % de los frutos **con madurez clasificable** en Light Red/Red".
- [ ] 10.6 Etiquetas de cobertura ("X de Y con madurez"); frescura en "Todos"
      ("último dato disponible de cada módulo" + rango de fechas).
  - Tests (Req 16.19, 16.24, 16.25, 16.27): estructura/touch (selectores, 4 KPIs,
    3 tabs, una gráfica por pestaña); ausencia de red/CDN en HTML; lenguaje
    operativo; "Todos" muestra series por módulo; "Mixto" en empate; ventana
    degenerada sin garantía; harvestable_share con denominador explícito.

## Bloque 11 — Preservación operativa y mocks del dashboard

- [ ] 11.1 Verificar preservación: alertas operativas, contexto de invernadero en
      alertas, último monitoreo, actividades recientes, badge de sync (Req 12).
- [ ] 11.2 Actualizar los mocks del Dashboard en `test_dashboard_ui.py`
      (`gh_repo.get_all` → `get_all_by_owner`, `export_repo.list_pending` →
      `list_by_user`, y nuevos métodos bulk) para reflejar el flujo real. Está
      permitido porque el Dashboard es parte directa de esta spec; documentar el
      motivo en el commit.
  - Tests (Req 16.20): `test_alert_service.py` (contexto de invernadero) sigue
    verde; tests de dashboard reflejan el flujo real de repositorios.

## Bloque 12 — Verificación final contra baseline + performance + documentación

- [ ] 12.1 Ejecutar **regresión dirigida** de los módulos tocados por Spec 024
      (analítica, dashboard, repos, alert service).
- [ ] 12.2 Ejecutar la **full suite** `python -m pytest -q`.
- [ ] 12.3 **Comparar contra el baseline del Bloque 0**: corregir únicamente las
      **nuevas** regresiones introducidas por Spec 024. NO reparar fallos
      preexistentes no relacionados (documentarlos como fuera de alcance). Los
      mocks del dashboard SÍ se actualizan (Bloque 11) por ser parte de la spec.
- [ ] 12.4 Medir/estimar el nº de queries por render tras el bulk loading
      (design §12.3) y documentarlo. Confirmar ausencia de N+1 evitable.
- [ ] 12.5 Decidir, **solo si la medición lo justifica**, un límite de
      **presentación** de puntos por gráfica (design §13); documentarlo como
      decisión de presentación, no de cálculo. Si no hace falta, no limitar.
- [ ] 12.6 Actualizar `docs/thesis-notes/current-state.md` y crear un ADR
      (`docs/decisions/ADR-NNN`) para las fórmulas de maturity index y ventana de
      cosecha, incluyendo el ejemplo numérico.
- [ ] 12.7 Checklist de cierre: aislamiento multiusuario, offline/sin CDN, sin
      cambios de esquema, preservación operativa.

---

## Mapa tareas → requerimientos

| Bloque | Requerimientos cubiertos |
|---|---|
| 0 | baseline (ANTES de implementar) |
| 1 | 14, glosario |
| 2 | 7.1, 7.2, 7.4, 7.7, 7.9, 8, 8.5, 16.8/16.9/16.25/16.26/16.27 |
| 3 | 7.6, 16.9 |
| 4 | 3, 4, 6, 11, 16.5/16.7/16.8/16.11/16.12/16.18 |
| 5 | 5, 16.10/16.13 |
| 6 | 9, 10, 16.14/16.15/16.16/16.17/16.22/16.23/16.24 |
| 7 | 8.3, 15, 16.21 |
| 8 | 14.1, 14.3 |
| 9 | 1, 2, 13.4, 14.2, 16.1/16.2/16.3/16.4/16.6 |
| 10 | 1.7, 3, 5.9, 6.4, 7.8, 13.1/13.2/13.5, 16.19/16.24/16.25/16.27 |
| 11 | 12, 16.20 |
| 12 | verificación vs baseline, 13.4, 13.6, 14.5 |

## Fuera de alcance (recordatorio)

- Cambios de esquema (SQLite/Supabase/RLS/sync/recovery). Ampliar API de repos
  con bulk `IN (...)` NO es cambio de esquema.
- Fix visual de cobertura de madurez del reporte individual (pospuesto tras Spec 025).
- ML/forecasting adicional, kilogramos, rendimiento, meteorología.
- Reparar fallos preexistentes no relacionados en esta rama.
