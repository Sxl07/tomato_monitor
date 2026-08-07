# Design Document

## Overview

Arquitectura objetivo del sistema portátil de monitoreo visual y trazabilidad agrícola:

```
Operador autenticado (login local offline)
  → Raspberry Pi 5 portátil con cámara y pantalla táctil DSI 7"
    → Dashboard contextual (indicadores reales)
      → Selección de invernadero/módulo
        → Alertas operativas (pendientes/vencidos)
        → Monitoreo visual capture-first (portable)
          → Captura de snapshots
          → Finalización → liberación de cámara
          → Análisis diferido
          → Reporte con métricas
        → Bitácora de actividades agrícolas
        → Historial combinado (monitoreos + actividades)
      → Exportación ZIP (datos + imágenes)
      → Sincronización manual provider-agnostic (futuro)
```

El operario transporta físicamente el dispositivo, recorre el invernadero, ejecuta monitoreos visuales manuales y registra labores agrícolas. No hay robot autónomo, chasis, motores ni navegación automática.

---

## Current Stable Baseline

El sistema actual ya tiene implementado y validado:

| Componente | Estado |
|---|---|
| Greenhouse → Module → Monitoring → Snapshot → InspectionResult → MonitoringMetrics | ✅ Completo |
| CaptureWorker (single camera owner, Scene Gate, snapshot saving) | ✅ Completo |
| SnapshotAnalysisService (deferred inference, tracking, dedup, thermal pause) | ✅ Completo |
| MonitoringService (lifecycle orchestration, finalize_capture, run_analysis) | ✅ Completo |
| MonitoringRuntimeRegistry (thread/worker tracking, finalization claims) | ✅ Completo |
| MonitoringState FSM con ANALYZING | ✅ Completo |
| Finalizar captura (POST /monitoreos/{id}/finalizar-captura) | ✅ Completo |
| Análisis diferido con progreso (X/Y snapshots) | ✅ Completo |
| Reportes con snapshots anotados/raw | ✅ Completo |
| Thermal pause durante análisis con alertas UI | ✅ Completo |
| UI landscape 800×480 touch-first | ✅ Completo |
| 826 tests pasando (unitarios, dominio, infra, properties, boundaries) | ✅ Completo |

**Estos componentes NO se reescriben. Se extienden por composición.**

---

## Major Gaps

Funcionalidades que actualmente NO existen y esta spec introduce:

| Gap | Prioridad |
|---|---|
| User entity / Auth local offline | Alta |
| Dashboard contextual | Alta |
| Module monitoring frequency | Media |
| Operational alerts | Media |
| Agricultural activity log | Media |
| Activity type catalog | Media |
| ZIP export | Media |
| Manual sync foundation | Baja |
| Portrait responsive layout | Media |

---

## Proposed Modules

### 1. Login / Usuarios
Autenticación local offline con hashing seguro. Registro orientado a futuro online.

### 2. Dashboard General
Pantalla resumen con indicadores basados exclusivamente en datos reales del sistema.

### 3. Invernaderos y Módulos
Extensión del CRUD actual con frecuencia de monitoreo por módulo.

### 4. Alertas / Tareas Pendientes
Cálculo de alertas operativas a partir del estado del sistema (módulos vencidos, exports pendientes).

### 5. Monitoreo Visual Portátil
Mismo capture-first pipeline. El operario recorre manualmente. Sin cambios en lógica core.

### 6. Bitácora de Actividades Agrícolas
Registro de labores desde catálogo predefinido. Trazabilidad con usuario + fecha + módulo.

### 7. Historial y Reportes
Timeline combinado de monitoreos y actividades por módulo. Acceso a reportes existentes.

### 8. Exportación / Sincronización
ZIP local como primer mecanismo. Sync manual provider-agnostic como extensión futura.

### 9. Configuración Técnica Básica
Ajustes de dispositivo, usuario, frecuencia por defecto. Mínimo para MVP.

---

## Operator Flow

```
1. Operario enciende Raspberry Pi
2. Sistema muestra pantalla de login
3. Operario inicia sesión (local, offline)
4. Dashboard muestra:
   - Contexto de invernadero/cultivo
   - Módulos pendientes de monitoreo
   - Alertas operativas
   - Actividades recientes
5. Operario selecciona módulo pendiente
6. Opcionalmente registra actividad agrícola (riego, poda, etc.)
7. Inicia monitoreo visual portátil
8. Recorre manualmente el módulo con la Raspberry (cámara activa)
9. Finaliza captura cuando termina el recorrido
10. Sistema libera cámara
11. Sistema analiza snapshots (diferido, con progreso)
12. Operario revisa reporte
13. Operario exporta datos/imágenes (ZIP) o marca para sincronización futura
14. Operario continúa con el siguiente módulo o cierra sesión
```

---

## Data Model Proposal

### User (nueva entidad)

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | PK, autoincrement | Identificador único |
| full_name | String(150) | NOT NULL | Nombre completo del operario |
| email | String(200) | NOT NULL, UNIQUE | Email (login identifier) |
| password_hash | String(255) | NOT NULL | Securely hashed password |
| role | String(20) | NOT NULL, default="operator" | Rol: operator, admin |
| is_active | Boolean | NOT NULL, default=True | Usuario activo |
| remote_user_id | String(100) | nullable | ID en sistema remoto (futuro) |
| sync_status | String(20) | NOT NULL, default="local_only" | local_only, synced, pending_sync |
| created_at | DateTime | NOT NULL, default=utcnow | Fecha de creación |
| updated_at | DateTime | NOT NULL, auto-update | Última modificación |
| last_login_at | DateTime | nullable | Último login exitoso |

### ActivityType (nueva entidad)

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | PK, autoincrement | Identificador único |
| code | String(50) | NOT NULL, UNIQUE | Código máquina (riego, poda, etc.) |
| name | String(100) | NOT NULL | Nombre para mostrar |
| category | String(50) | NOT NULL | Categoría (mantenimiento, nutrición, etc.) |
| requires_product | Boolean | NOT NULL, default=False | Si requiere indicar producto |
| allows_quantity | Boolean | NOT NULL, default=False | Si permite registrar cantidad |
| default_unit | String(20) | nullable | Unidad por defecto (L, kg, mL) |
| is_active | Boolean | NOT NULL, default=True | Tipo activo en catálogo |

### ActivityLog (nueva entidad)

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | PK, autoincrement | Identificador único |
| module_id | Integer | FK → Module, NOT NULL | Módulo donde se realizó |
| activity_type_id | Integer | FK → ActivityType, NOT NULL | Tipo de actividad |
| user_id | Integer | FK → User, NOT NULL | Operario que registró |
| product_name | String(150) | nullable | Producto utilizado (fertilizante, fungicida) |
| quantity | Float | nullable | Cantidad aplicada |
| unit | String(20) | nullable | Unidad (L, kg, mL, unidades) |
| notes | Text | nullable | Observaciones |
| occurred_at | DateTime | NOT NULL, default=utcnow | Momento de la actividad |
| created_at | DateTime | NOT NULL, default=utcnow | Fecha de registro |
| sync_status | String(20) | NOT NULL, default="pending" | pending, synced, error |

### ExportPackage (nueva entidad)

| Field | Type | Constraints | Description |
|---|---|---|---|
| id | Integer | PK, autoincrement | Identificador único |
| created_by_user_id | Integer | FK → User, NOT NULL | Usuario que solicitó |
| scope | String(50) | NOT NULL | Alcance: full, greenhouse, module, monitoring |
| scope_id | Integer | nullable | ID del recurso exportado (si scope no es full) |
| file_path | String(500) | nullable | Ruta al ZIP generado |
| file_size_bytes | Integer | nullable | Tamaño del archivo |
| status | String(20) | NOT NULL, default="pending" | pending, generating, completed, error |
| error_message | Text | nullable | Detalle del error si falló |
| records_count | Integer | NOT NULL, default=0 | Registros incluidos |
| images_count | Integer | NOT NULL, default=0 | Imágenes incluidas |
| created_at | DateTime | NOT NULL, default=utcnow | Fecha de solicitud |
| completed_at | DateTime | nullable | Fecha de finalización |
| manifest_json | Text | nullable | JSON con checksums y conteos |

### Module (extensión)

Agregar a la entidad existente:

| Field | Type | Constraints | Description |
|---|---|---|---|
| monitoring_frequency_days | Integer | nullable | Frecuencia esperada en días |

### Monitoring (extensión)

Agregar a la entidad existente:

| Field | Type | Constraints | Description |
|---|---|---|---|
| created_by_user_id | Integer | FK → User, nullable | Operario que ejecutó (nullable para migración) |
| sync_status | String(20) | NOT NULL, default="pending" | pending, synced, error |

### OperationalAlert (calculada, no necesariamente persistida)

Las alertas operativas se calculan a partir del estado del sistema:

```python
@dataclass
class OperationalAlert:
    alert_type: str          # "monitoring_overdue", "export_pending", "analysis_error", etc.
    severity: str            # "info", "warning", "critical"
    title: str               # Título corto para mostrar
    message: str             # Detalle
    module_id: int | None    # Módulo relacionado (si aplica)
    source: str              # Origen del dato
    created_at: datetime     # Momento del cálculo
```

Tipos de alerta MVP:
- `monitoring_overdue`: Módulo supera su frecuencia de monitoreo
- `monitoring_pending`: Módulo nunca monitoreado
- `export_pending`: Datos/imágenes sin exportar
- `analysis_error`: Último monitoreo terminó en error
- `thermal_warning`: Última sesión tuvo pausa térmica

---

## Authentication Design

### Principios

- **Local-first:** Login funciona sin internet usando usuarios almacenados en SQLite local.
- **Password hashing:** Mandatory. The concrete hashing library must be selected after dependency and Raspberry Pi ARM64 compatibility review. Candidate options include bcrypt or argon2, but the spec does not mandate one yet.
- **Session management:** Secure signed cookies or equivalent local session mechanism. The concrete session library must be selected during implementation after reviewing available options and their ARM64 compatibility.
- **Route protection:** Dependency-injection based (Depends()) that validates session. Public routes: login, static, health.
- **Test compatibility:** Los 826 tests actuales NO deben romperse. Usar fixture de test que inyecta usuario autenticado.
- **No secrets hardcoded:** All credentials and keys stored outside source code.

### Flujo de login

```
1. GET /login → renderizar formulario
2. POST /login → validar email + password → crear sesión cookie → redirect /dashboard
3. Cualquier ruta protegida sin sesión → redirect /login
4. POST /logout → eliminar cookie → redirect /login
```

### Riesgo: ruptura de tests

Agregar auth puede romper todos los tests de rutas. Estrategia de mitigación:

1. Implementar auth como dependency inyectable (Depends()), no como middleware global.
2. En conftest.py de tests, override la dependency con un mock user.
3. No usar middleware global que bloquee requests sin cookie; usar Depends() por ruta.
4. Proteger gradualmente: primero login funcional, luego proteger rutas una a una.

---

## Dashboard Design

### Indicadores MVP (todos de datos reales)

| Indicador | Fuente |
|---|---|
| Invernaderos activos | COUNT(greenhouses) |
| Módulos activos | COUNT(modules) |
| Módulos pendientes de monitoreo | Cálculo: frequency vs last monitoring |
| Módulos vencidos | Cálculo: overdue por frequency |
| Último monitoreo (fecha) | MAX(monitorings.started_at) WHERE status=completed |
| Monitoreos esta semana | COUNT(monitorings) WHERE started_at > 7 days ago AND status=completed |
| Snapshots en último monitoreo | monitoring.total_snapshots |
| Tomates detectados (último) | monitoring_metrics.total_tomatoes |
| Actividades recientes | ActivityLog ORDER BY occurred_at DESC LIMIT 5 |
| Exportaciones pendientes | COUNT WHERE sync_status != synced |
| Estado térmico | Último thermal event si existe |

### Indicadores NO incluidos (sin evidencia)

No incluir sin modelo/datos reales:
- Déficit de riego
- Plagas detectadas
- Rendimiento proyectado
- Eficiencia hídrica
- Recomendaciones agronómicas

---

## Alerts Design

### Cálculo de alertas

Las alertas MVP se calculan dinámicamente al cargar el dashboard. No requieren tabla propia en esta fase. Un `AlertService` evalúa el estado actual del sistema y retorna alertas vigentes:

```python
class AlertService:
    def compute_alerts(self, db_session) -> list[OperationalAlert]:
        alerts = []
        alerts.extend(self._check_overdue_modules(db_session))
        alerts.extend(self._check_pending_exports(db_session))
        alerts.extend(self._check_analysis_errors(db_session))
        alerts.extend(self._check_thermal_warnings(db_session))
        return sorted(alerts, key=lambda a: SEVERITY_ORDER[a.severity], reverse=True)
```

**Computed vs persisted:** In MVP, alerts are computed on each request. When a condition is no longer true (e.g., module is monitored), the alert simply stops appearing. Persistent alert tracking (with resolved_at, status) is optional and deferred to a future iteration if needed for export/sync evidence.

### Alertas no permitidas

Las alertas NO deben:
- Recomendar riego, fertilización o poda
- Diagnosticar plagas o enfermedades sin modelo entrenado
- Proyectar rendimiento sin datos históricos suficientes
- Mostrar indicadores inventados

---

## Activity Log Design

### Diferenciación clara

| Concepto | Propósito | Ubicación actual |
|---|---|---|
| Activity Log (nuevo) | Bitácora agrícola: labores del operario por módulo | Nueva funcionalidad |
| Monitoring Technical Log | Log técnico del monitoreo (captura, análisis, thermal) | monitoring_execution.html (existente) |

**No confundir.** El Activity Log es un registro agrícola (riego, poda, cosecha). El Technical Log es un log de eventos de la sesión de monitoreo.

### Catálogo

Semilla inicial insertada al crear la base de datos (similar a `init_db()`). No editable desde UI en esta versión.

### Formulario

```
Módulo: [seleccionado automáticamente si viene de module_detail]
Tipo de actividad: [selector del catálogo]
Fecha/hora: [default ahora, editable]
Producto: [opcional, visible si requires_product]
Cantidad: [opcional, visible si allows_quantity]
Unidad: [opcional, default del tipo]
Notas: [opcional, textarea]
[Guardar]  [Cancelar]
```

---

## History Design

### Combined Timeline

En `module_detail.html`, agregar sección de historial combinado:

```
Timeline por módulo:
  [2025-01-15 10:30] 🍅 Monitoreo completado — 45 tomates, 78% sanos → [Ver reporte]
  [2025-01-14 08:00] 🌱 Fertilización — Producto: NPK 20-20-20, 2L
  [2025-01-12 14:00] 🍅 Monitoreo completado — 38 tomates, 82% sanos → [Ver reporte]
  [2025-01-10 09:30] ✂️ Poda — Notas: Eliminación de chupones
  [2025-01-08 16:00] 💧 Riego — 15L
```

Ordenado por timestamp descendente. Iconos/colores distinguen tipo de evento.

---

## ZIP Export Design

### Estructura

```python
class ExportService:
    def generate_export(self, scope, scope_id, user_id) -> ExportPackage:
        # 1. Collect structured data (JSON serializable)
        # 2. Create temp directory
        # 3. Write JSON files
        # 4. Copy snapshots (raw + annotated) incrementally
        # 5. Generate manifest with checksums
        # 6. Create ZIP using streaming (zipfile module)
        # 7. Record ExportPackage in DB
        # 8. Return package info
```

### Consideraciones RPi

- NO cargar todas las imágenes en RAM simultáneamente.
- Usar `zipfile.ZipFile` con escritura incremental.
- Limitar a un export a la vez (no paralelo).
- Mostrar progreso en UI si es posible.
- Guardar ZIP en `outputs/exports/export_{id}_{timestamp}.zip`.

---

## Manual Sync Design

### Provider-agnostic

```python
class RemoteSyncAdapter(ABC):
    """Interface for remote synchronization providers."""
    
    @abstractmethod
    def upload_file(self, local_path: Path, remote_path: str) -> SyncResult: ...
    
    @abstractmethod
    def upload_data(self, data: dict, collection: str) -> SyncResult: ...
    
    @abstractmethod
    def check_connection(self) -> bool: ...
```

### Implementación MVP

En esta versión, la "sincronización" es simplemente:
1. Generar ZIP export.
2. Marcar registros como "exportados".
3. El operario transfiere manualmente el ZIP (USB, red local, etc.).

Adaptadores concretos (Drive, S3, Supabase) quedan para spec futura.

### Estados de sync

```
pending → exporting → exported → [syncing → synced] (futuro)
                   ↘ error (con retry)
```

---

## UI/UX Design

### Orientación

| Modo | Resolución | Uso |
|---|---|---|
| Raspberry vertical (portrait) | 480×800 | Uso principal en campo |
| Raspberry horizontal (landscape) | 800×480 | Uso alternativo |
| Desktop | Variable | Desarrollo y consulta |

### Responsive Strategy

Agregar `@media` queries para portrait:
- `@media (orientation: portrait)` o `@media (max-width: 600px)` para columna única.
- Cards apiladas verticalmente.
- Formularios de ancho completo.
- Navegación compacta (bottom bar o hamburger).
- Botones full-width en pantallas angostas.

### Navegación propuesta

**Desktop / Landscape:**
```
[Dashboard] [Invernaderos] [Bitácora] [Alertas] [Exportar] [Usuario ▾]
```

**Portrait / Compact:**
```
Bottom navigation: [🏠 Inicio] [📸 Monitoreo] [📋 Bitácora] [⚠️ Alertas] [⋯ Más]
```

### Lenguaje UI

- ❌ "El robot recorre el módulo"
- ✅ "Recorra el módulo con el dispositivo"
- ❌ "Navegación automática"
- ✅ "Monitoreo manual"
- ❌ "Traversal session"
- ✅ "Sesión de monitoreo"

---

## Architecture Boundaries

### Mantener (no tocar)

- Capture-first pipeline (CaptureWorker → finalize → SnapshotAnalysisService)
- Deferred analysis con progreso
- Thermal monitoring y pause/resume
- Local SQLite persistence
- Report generation (snapshots anotados + métricas)
- Monitoring state machine (MonitoringState FSM)
- Single camera owner invariant

### No introducir

- RobotOrchestrator
- Robot domain entities (RobotPosition, MovementDecision)
- Motor adapters (BTS7960, NoOp)
- GPIO movement scripts
- Autonomous navigation
- Battery monitor (para locomotion)
- Chassis/robot safety controller

### Limpiar gradualmente (after audit confirms they are unused)

- Interfaces de dominio potencialmente obsoletas: `robot_movement_service.py`, `decision_service.py` — removal only after verifying they are not referenced by tests or current pipeline
- Pyc cache de interfaces eliminadas (battery_monitor, motor_controller, etc.)
- Lenguaje robot en steering files y product definition

---

## Risks

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Spec muy amplia para un sprint | Alto | Implementar en waves incrementales |
| Migración SQLite con datos existentes | Medio | Usar Alembic o columns nullable + defaults |
| Auth middleware rompe 826 tests | Alto | Auth como Depends(), no middleware global; fixture mock user |
| bcrypt/argon2 wheel en ARM64 | Bajo | Verify ARM64 wheel availability during implementation; select library after review |
| ZIP export con muchas imágenes en RPi | Medio | Escritura incremental, no cargar todo en RAM |
| Portrait CSS break existing landscape | Medio | @media additive, no destructivo |
| Sync remoto indefinido | Bajo | Provider-agnostic interface, solo ZIP local en MVP |
| Indicadores dashboard sin datos | Medio | Solo mostrar datos reales; no inventar métricas |
| Scope creep durante implementación | Alto | Seguir task list estrictamente; no agregar features |

---

## Incremental Strategy

Implementar en waves para minimizar riesgo:

| Wave | Contenido | Prerequisito |
|---|---|---|
| 1 | Audit scope language + align steering | — |
| 2 | Data model foundation (User, ActivityType, ActivityLog, Module extension) | — |
| 3 | Auth local minimal (login, session, route protection) | Wave 2 |
| 4 | Module frequency + operational alerts | Wave 2 |
| 5 | Activity log (catalog, form, persistence) | Wave 2, 3 |
| 6 | Dashboard + combined history | Wave 2, 3, 4, 5 |
| 7 | ZIP export + sync status tracking | Wave 2, 3 |
| 8 | Portrait UI polish | Wave 6 |
| 9 | Final validation + thesis alignment | All |
