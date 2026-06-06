# Próximos pasos del proyecto

**Última actualización:** junio 2026

---

## Cambio de enfoque: de video a monitoreo agrícola en tiempo real

A partir de junio 2026, el sistema evoluciona hacia un flujo operacional basado en:

- Cámara en tiempo real (Raspberry Pi AI Camera)
- Captura de snapshots por detección de cambios (Scene Gate)
- Inferencia por imagen (no por frame de video)
- Reportes con métricas agrícolas organizados por Invernadero → Módulo → Monitoreo

El pipeline de video se conserva exclusivamente como herramienta de benchmark y pruebas (Spec 001).

---

## 1. Benchmark de línea base en Raspberry Pi (Spec 001) — COMPLETADA

El benchmark de rendimiento del pipeline actual en Raspberry Pi 5 fue completado y establece la referencia cuantitativa para la tesis y para comparaciones futuras.

**Estado:** Completada. Todas las fases (1-8) finalizadas. Scripts de benchmark creados, experimentos comparativos ejecutados, resultados documentados, ADRs actualizados con evidencia cuantitativa, y `requirements-raspberry.txt` generado.

**Resultados clave:**

| Métrica | Valor medido |
|---|---|
| FPS — `full_detection` (sin Scene Gate) | 0.17 FPS |
| FPS — `sparse_flow` (Scene Gate + Optical Flow) | 1.80 FPS |
| FPS — `sparse_flow_candidate` (min5/max12 + flow) | 2.10 FPS |
| Reducción de invocaciones del detector | ~92% |
| RAM pico | ~2.1 GB |
| Cuello de botella identificado | Latencia del detector en CPU (5.3–6.4 s/frame) |

**Pendiente:** Ejecución formal de `bench_full_video.py` con registro de temperatura detallado (los experimentos comparativos no incluyeron métricas térmicas formales).

**Desbloquea:** Spec 006 (modelo de datos), Spec 007 (flujo de monitoreo live), Spec 003 (optimización edge).

---

## 2. Modelo de datos agrícola (Spec 006)

Implementar la capa de persistencia SQLite con el modelo jerárquico: Greenhouse → Module → Monitoring → Snapshot → InspectionResult → MonitoringMetrics.

**Incluye:**

- Entidades de dominio y value objects
- Modelos SQLAlchemy
- Repositorios con interfaz en dominio e implementación en infraestructura
- Migraciones iniciales con `create_all()`
- CRUD completo para greenhouses, modules y monitorings

**Prerrequisito:** Independiente. Puede iniciar inmediatamente.

---

## 3. Flujo de ejecución de monitoreo (Spec 007)

Implementar el flujo completo de monitoreo en tiempo real: selección de módulo → confirmación de parámetros → inicio de cámara → captura de snapshots → inferencia → consolidación de resultados.

**Incluye:**

- Integración de cámara live (picamera2)
- Adaptación del Scene Gate para modo streaming
- Inferencia por snapshot (detección + sanidad + madurez)
- Máquina de estados del monitoreo (IDLE → INITIALIZING → RUNNING → FINISHING → COMPLETED)
- Persistencia de snapshots y resultados en SQLite

**Prerrequisito:** Spec 001 (línea base para comparar rendimiento), Spec 006 (persistencia).

---

## 4. Rediseño de UI agrícola (Spec 008)

Rediseñar la interfaz web para el agricultor operando en pantalla táctil DSI 7" (800×480).

**Incluye:**

- Navegación jerárquica: Invernadero → Módulo → Monitoreo
- Pantallas: lista de invernaderos, detalle de invernadero, detalle de módulo, configuración de monitoreo, ejecución live, reporte final
- Diseño touch-first (targets ≥44×44 px, tipografía ≥16 px)
- Lenguaje en español, orientado al agricultor
- Estados visuales del monitoreo con indicadores claros

**Prerrequisito:** Spec 006 (datos), Spec 007 (flujo de monitoreo).

---

## 5. Métricas agregadas y reportes (Spec 009)

Calcular y presentar métricas agrícolas útiles para el agricultor al finalizar cada monitoreo.

**Incluye:**

- Cálculo de MonitoringMetrics (conteo total, % madurez por etapa USDA, % sanidad)
- Pantalla de reporte final con métricas visuales
- Consulta histórica de monitoreos por módulo
- Comparación entre monitoreos (opcional, si el tiempo lo permite)

**Prerrequisito:** Spec 006 (datos), Spec 007 (flujo).

---

## 6. Optimización del pipeline para edge (Spec 003)

Con la línea base formal establecida y el modo live funcionando, se pueden tomar decisiones de optimización informadas:

- Evaluar reemplazo de Detectron2/RetinaNet por detector más ligero
- Cuantización de modelos
- Ajuste de parámetros del Scene Gate
- Reducción de resolución de entrada
- Explorar NPU de la AI Camera

**Prerrequisito:** Spec 001 completada formalmente, Spec 007 funcional.

---

## 7. Documentación para tesis (Spec 004)

Pendiente de completar antes de la entrega:

- [ ] `docs/benchmarks/raspberry-baseline.md` — resultados reales del benchmark
- [ ] ADRs expandidos con evidencia cuantitativa
- [ ] Metadatos de modelos (dataset, métricas de validación, condiciones de entrenamiento)
- [ ] `requirements-raspberry.txt` — versiones exactas validadas en RPi 5
- [ ] Documentación del flujo live vs. video como contribución técnica

---

## 8. Calidad y seguridad (Spec 005)

- Suite de tests automatizados con pytest
- Validación de entradas en todos los endpoints
- Manejo robusto de errores en carga de modelos y cámara
- Protección contra path traversal

---

## Orden de ejecución sugerido

| # | Spec | Descripción | Prerrequisito | Estado |
|---|---|---|---|---|
| 001 | Benchmark de línea base | Medición formal en RPi 5 | Hardware disponible | ✅ Completada |
| 006 | Modelo de datos agrícola | SQLite + entidades + repositorios | Independiente | Pendiente de crear |
| 002 | Integración cámara live | AI Camera con picamera2 | Spec 001 completada | Absorbida por Spec 007 |
| 007 | Flujo de monitoreo live | Cámara + snapshots + inferencia | 001, 006 | Pendiente de crear |
| 008 | UI agrícola | Interfaz táctil para agricultor | 006, 007 | Pendiente de crear |
| 009 | Métricas y reportes | Cálculos agregados + visualización | 006, 007 | Pendiente de crear |
| 003 | Optimización edge | Reducción de latencia/consumo | 001, 007 | Planificada |
| 004 | Documentación tesis | Evidencia formal completa | 001-003, 006-009 | Parcialmente completada |
| 005 | Calidad y seguridad | Tests + hardening | Todo lo anterior | Planificada |

**Prioridad inmediata:** Crear Spec 006 (modelo de datos agrícola) — es independiente y desbloquea Spec 007, 008 y 009.

---

## Diagrama de dependencias

```
001 (Benchmark) ──→ 002 (Cámara Live) ─┐
       │                                │
       └────────────────────────────────├──→ 007 (Monitoreo Live) ──→ 008 (UI Agrícola)
                                        │                         ──→ 009 (Métricas)
006 (Modelo de Datos) ──────────────────┘
                                        
001 + 007 ──→ 003 (Optimización Edge)

004 (Documentación) ← acumula de todos
005 (Calidad) ← aplica a todos
```
