# ADR-006: Índice de madurez y estimación de ventana de próxima cosecha

## Estado
Aceptado

## Fecha
Septiembre 2026

## Contexto
El dashboard analítico (Spec 024) debe permitir al operador observar la evolución
del cultivo y estimar de forma prudente la próxima ventana de cosecha, usando
únicamente datos ya persistidos por Tomato Monitor. Restricciones del sistema:

- Las categorías de madurez del sistema son las etapas USDA:
  `green`, `breaker`, `turning`, `pink`, `light_red`, `red`.
- La **cobertura de madurez puede ser parcial**: no todos los frutos detectados
  en un monitoreo obtienen una etapa de madurez clasificable (p. ej. monitoring
  55: 16 frutos únicos, 10 con etapa clasificable).
- **No existe tracking longitudinal de identidad individual entre días**: el
  tracker identifica frutos dentro de una sesión, no mantiene identidad entre
  monitoreos.
- No se busca un modelo agronómico universal ni introducir ML adicional.
- Debe ser determinista, reproducible y explicable en una tesis.

## Decisión

### Escala ordinal de madurez
Se asigna a cada etapa un valor normalizado en [0, 1] (6 etapas, 5 pasos):

| Etapa | Valor |
|---|---|
| green | 0.0 |
| breaker | 0.2 |
| turning | 0.4 |
| pink | 0.6 |
| light_red | 0.8 |
| red | 1.0 |

### Conteos de madurez (fuente de verdad)
Los conteos por etapa (`count_stage`) y la cobertura (`maturity_covered`) se
derivan de `InspectionResult` contando filas con `maturity_stage != None`. NUNCA
se reconstruyen conteos desde los porcentajes persistidos `MonitoringMetrics.pct_*`.

### Maturity Index (MI)
Promedio ordinal ponderado por conteo, sobre el subconjunto con madurez
clasificable:

```
MI = Σ (count_stage * value_stage) / maturity_covered      (maturity_covered > 0)
MI = None                                                  (maturity_covered == 0)
```

`MI ∈ [0, 1]`. Es un **indicador ordinal operacional**, no una probabilidad ni un
porcentaje de madurez ni una medida agronómica universal.

### Harvestable Share (magnitud separada)
```
harvestable_share = (count_light_red + count_red) / maturity_covered
```
Es una señal complementaria; se muestra con denominador explícito ("frutos con
madurez clasificable") y NO se confunde con el MI.

### Coverage ratio
```
coverage_ratio = maturity_covered / total_tomatoes
```
Mide la fracción de frutos con madurez clasificable; se usa como peso de la
tendencia y como evidencia objetiva en la UI.

### Tendencia (mínimos cuadrados ponderados por cobertura, WLS)
Sobre las observaciones utilizables de un módulo (`x` = días desde la primera,
`y` = MI, `w` = coverage_ratio):

```
W     = Σ w_i
x̄_w   = Σ(w_i x_i)/W ;   ȳ_w = Σ(w_i y_i)/W
Sxx_w = Σ w_i (x_i - x̄_w)^2
Sxy_w = Σ w_i (x_i - x̄_w)(y_i - ȳ_w)
slope     = Sxy_w / Sxx_w            (si Sxx_w > 0)
intercept = ȳ_w - slope * x̄_w
```

### Target
```
MI_TARGET = 0.8
```
Interpretado como **"nivel medio de madurez equivalente a Light Red"**. NO
significa "80 % de los tomates están cosechables".

### Ventana de cosecha
- Cruce del target sobre la **recta ajustada** WLS:
  `t_target = (MI_TARGET - intercept) / slope`; `days_until = t_target - x_last`.
- Dispersión ponderada de residuales:
  `r_i = y_i - (intercept + slope*x_i)`; `s = sqrt(Σ w_i r_i^2 / Σ w_i)`.
- Banda ±s: la ventana es el cruce de `MI_TARGET` con las rectas paralelas
  desplazadas ±s:
  `t_low = (MI_TARGET - s - intercept)/slope`;
  `t_high = (MI_TARGET + s - intercept)/slope`.
- La incertidumbre de la ventana proviene de la variabilidad observada (no de una
  constante arbitraria). Estabilidad numérica: `s < 1e-12` se trata como 0
  (ventana degenerada / estimación puntual).

### Estados de salida (orden determinista)
1. sin observación utilizable → `insufficient`
2. `MI_last >= MI_TARGET` → `target_reached` (aunque haya 1–2 observaciones)
3. `n < MIN_OBS (3)` → `insufficient`
4. `Sxx_w == 0` (sin dispersión temporal) → `not_estimable`
5. `MI_last < MI_TARGET` y `slope <= 0` → `not_estimable`
6. `MI_last < MI_TARGET` y `slope > 0`:
   - `t_target > x_last` → `window` (normal)
   - `t_target <= x_last` y `t_high > x_last` → `window` desde "ahora"
   - `t_target <= x_last` y `t_high <= x_last` → `not_estimable` (inconsistencia)

`target_reached` NUNCA se decide solo por `days_until <= 0`.

### Scope "Todos"
La estimación es siempre **por módulo**. En scope "Todos" se produce un resumen
por módulo (`HarvestSummaryAll`), nunca una fecha única del invernadero.

## Ejemplo numérico (aprobado)
Observaciones `(día, MI, coverage_ratio)`:

| día | MI | w |
|---|---|---|
| 0 | 0.30 | 0.6 |
| 7 | 0.42 | 0.8 |
| 14 | 0.55 | 1.0 |
| 21 | 0.66 | 0.9 |

Resultado (reproducido por tests):

```
slope     ≈ 0.0172704 MI/día
intercept ≈ 0.3015422
s         ≈ 0.0044965
t_target  ≈ 28.862 días
ventana   ≈ +7.60 a +8.12 días desde el último monitoreo (status = window)
n_observations = 4 ; cobertura media ≈ 82 %
```

## Limitaciones
- Es una estimación **visual**, no un pronóstico agronómico.
- No usa datos meteorológicos ni estima kilogramos/rendimiento.
- No hay identidad longitudinal individual entre días; la serie no representa
  frutos únicos persistentes.
- Mínimo 3 observaciones utilizables para proyectar una tendencia futura.
- `target_reached` puede describir el estado actual con menos de 3 observaciones
  (no es una proyección).
- La cobertura parcial pondera la tendencia (observaciones de baja cobertura
  influyen menos).
- Las observaciones con solo fallback visual (`pct_*` sin `InspectionResult`) no
  entran en WLS ni en la estimación de cosecha.

## Consecuencias

### Positivas
- Determinista y reproducible (cubierto por tests con ejemplos conocidos).
- Explicable en la tesis (fórmulas cerradas, sin ML adicional).
- Se actualiza automáticamente cuando aparecen nuevos monitoreos.
- La incertidumbre deriva de los datos observados, no de constantes arbitrarias.
- Semántica de cobertura explícita evita conclusiones falsas por cobertura parcial.

### Negativas
- La proyección lineal es una simplificación; una maduración no lineal no se
  modela.
- Con pocas observaciones o dispersión baja, la ventana puede ser muy estrecha
  (se comunica como estimación central, no como garantía).
- Depende de la calidad de la clasificación de madurez del pipeline.

## Evidencia
- Implementación pura y testeable: `src/application/services/maturity_index.py`,
  `maturity_stats.py`, `harvest_estimator.py`, `dashboard_aggregation.py`.
- Tests deterministas: `tests/unit/test_maturity_index.py`,
  `test_maturity_stats.py`, `test_harvest_estimator.py` (incluye el ejemplo
  numérico anterior), `test_dashboard_aggregation.py`, `test_analytics_service.py`.

## Relación con specs
- Spec 024: Dashboard analítico contextual y estimación de próxima cosecha (este ADR).
- Spec 006: Modelo de datos agrícola (MonitoringMetrics, InspectionResult).
- Spec 022: Aislamiento multiusuario (las lecturas del dashboard respetan el scope
  del usuario).
