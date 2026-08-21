# Post-Raspberry Stabilization — Bugfix Design

## Overview

Correcciones puntuales identificadas durante la validación física del sistema Tomato Monitor en Raspberry Pi 5 (pantalla DSI 7", portrait 480×800, offline). La arquitectura existente funciona correctamente; este documento formaliza 13 ajustes mínimos + 1 ajuste adicional de timezone para entrada de datos, que mejoran la usabilidad y corrigen defectos observados en hardware real.

**Principio rector:** Correcciones quirúrgicas. No rediseñar. No romper capture-first pipeline, FSM, camera ownership, thermal monitoring ni vision infrastructure.

---

## Glossary

- **EDGE profile**: Perfil de ejecución para Raspberry Pi 5 definido en `src/infrastructure/config/settings.py`
- **CaptureWorker**: Owner exclusivo de cámara durante captura (`src/application/services/capture_worker.py`)
- **ExportService**: Generador de paquetes ZIP (`src/application/services/export_service.py`)
- **LogService**: Log in-memory por sesión de monitoreo (`src/application/services/log_service.py`)
- **HistoryService**: Constructor de timeline combinado (`src/application/services/history_service.py`)
- **DBManager**: Gestor de base de datos SQLite (`src/infrastructure/persistence/database.py`)

---

## Bug Details

### Bug Condition

El bug condition es un compuesto de 14 defectos independientes manifestados durante operación real en Raspberry Pi. Cada defecto tiene su condición de activación documentada en bugfix.md.

## Expected Behavior

Ver sección "Preservation Requirements" más abajo y bugfix.md secciones 2.1-2.14 para el comportamiento esperado detallado de cada fix.

## Hypothesized Root Cause

### 1. Dashboard quick links
`dashboard.html` contiene la sección "Acceso rápido" hardcoded. Redundante con el bottom nav.

### 2. Emoji rendering
`base_agricultural.html` bottom nav usa Unicode emojis (🏠🌱📦🔄). `history_service.py` usa emojis (📷💧🌱🧪🍅✂️🍃🌿🧹👁️🔍📝📋). Múltiples templates usan ⚠️🗑✎🌡️🍅. RPi OS Bookworm no incluye fuentes emoji por defecto.

**Archivos con emojis funcionales identificados:**
- `app/templates/base_agricultural.html` — bottom nav: 🏠🌱📦🔄
- `app/templates/agricultural/dashboard.html` — alertas: ⚠️
- `app/templates/agricultural/module_detail.html` — acciones: ✎🗑
- `app/templates/agricultural/greenhouse_detail.html` — acciones: ✎🗑
- `app/templates/agricultural/greenhouse_list.html` — alertas: ⚠️
- `app/templates/agricultural/monitoring_execution.html` — thermal/status: 🌡️⚠️
- `app/templates/agricultural/export_detail.html` — warnings: ⚠️
- `app/templates/error.html` — error icon: ⚠️
- `app/templates/auth/login.html` — branding: 🍅
- `src/application/services/history_service.py` — `_ACTIVITY_ICONS` dict + monitoring icon "📷" + fallback "📋"

### 3. Portrait margins
CSS `--safe-margin: 16px` existe pero algunos contenedores no respetan el safe area en viewport de 480px.

### 4. Mandatory dimensions (requiere migración SQLite)
- Domain: `Monitoring.width_m: float` (no Optional)
- ORM: `MonitoringModel.width_m` tiene `nullable=False`
- Schema físico SQLite: columnas creadas como `REAL NOT NULL`
- Validator: `validate_dimensions()` rechaza strings vacíos
- Route: `Form(...)` requiere valor
- Template: atributo `required` en inputs
- `MonitoringService.start_session()`: type hint `width_m: float`

**El schema físico SQLite existente tiene NOT NULL.** Cambiar solamente metadata SQLAlchemy (`nullable=True`) NO altera el schema en SQLite existente. Se requiere migración explícita.

### 5. EDGE thermal thresholds
`settings.py` EDGE profile: `analysis_thermal_pause_threshold=72.0`, `analysis_thermal_resume_threshold=65.0`. El SoC opera normalmente a 72-76°C durante inferencia.

### 6. UTC timestamps sin conversión
`history_service.py` usa `monitoring.started_at.strftime("%H:%M")` directamente. Templates usan `.strftime('%d/%m/%Y %H:%M')` en múltiples vistas. `context_builders.py` tiene `_format_date_spanish()` y `_format_time()` que no convierten timezone. Ningún lugar del código aplica conversión UTC→Bogota.

### 7. LogService timestamps
`log_service.py` usa `datetime.utcnow()` → naive datetime. El endpoint `/api/monitoring/{id}/log` serializa con `.isoformat()` sin sufijo 'Z'. La comparación `since` en `get_entries()` compara naive con naive (funciona internamente) pero el frontend no puede distinguir UTC de local.

### 8-9. Títulos y textos UI
Strings estáticos en templates.

### 10. Export image paths (diagnóstico pendiente, no bug confirmado)
ExportService construye: `f"snapshot_{frame_idx:06d}.jpg"` usando `snapshot.frame_index`.
CaptureWorker asigna: `frame_index=self._snapshot_count` y nombra: `f"snapshot_{self._snapshot_count:06d}.jpg"`.
SnapshotAnalysisService usa: `f"snapshot_{snapshot.frame_index:06d}.jpg"` para annotated.

**En el flujo normal, `frame_index == snapshot_count` y los nombres coinciden.** El problema de "0 imágenes" pudo originarse porque el export se generó antes de que existieran los monitorings con snapshots. No hay evidencia de inconsistencia de paths en el flujo normal.

**Decisión:** Preferir `Snapshot.image_path` como fuente autoritativa para robustez, pero NO declarar un bug de path sin counterexample que lo demuestre. Agregar tests diagnósticos que cubran todos los escenarios.

### 11. Orphan exports
No existe lógica de reconciliación en `lifespan()`. Si el proceso se reinicia durante generación ZIP, el registro queda en "generating" permanentemente.

### 12. Sync local-mode
`sync_status.html` describe el mecanismo ZIP en prosa pero no tiene indicador visual prominente de modo local ni explicación de estados.

### 13. httpx missing
`requirements.txt` no incluye `httpx`. `requirements-raspberry.txt` tampoco. FastAPI TestClient lo necesita.

### 14. Activity form timezone
`activity_create()` en `agricultural_ui.py` parsea fecha/hora con `datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")` y pasa el resultado directamente a `create_activity()`. Si el usuario escribe 21:17 (hora local Colombia), se persiste como 21:17 en UTC naive, pero debería ser 02:17 UTC del día siguiente.

---

## Correctness Properties

Property 1: Thermal pause at >= 78°C in EDGE profile (not 72°C). **Validates: Requirements 2.5**

Property 2: SVG icons render on all platforms without emoji fonts. **Validates: Requirements 2.2**

Property 3: Portrait safe margins >= 20px without overflow-x: hidden. **Validates: Requirements 2.3**

Property 4: Both-empty dimensions accepted, one-only rejected, existing data preserved after migration. **Validates: Requirements 2.4, 3.11**

Property 5: UTC timestamps displayed as America/Bogota (always -5h, no DST). **Validates: Requirements 2.6**

Property 6: LogService timestamps are timezone-aware with 'Z' suffix. **Validates: Requirements 2.7**

Property 7: Activity form local input (21:17 Bogota) → UTC persistence → display shows 21:17. **Validates: Requirements 2.14**

Property 8: ExportService uses Snapshot.image_path as authoritative source. **Validates: Requirements 2.10**

Property 9: Orphan "generating" exports reconciled to "error" at startup. **Validates: Requirements 2.11**

Property 10: Pipeline integrity preserved (capture-first, FSM, camera ownership unchanged). **Validates: Requirements 3.1, 3.5**

## Fix Implementation

### Fix 1: Remove "Acceso rápido" section

**File:** `app/templates/agricultural/dashboard.html`
**Change:** Eliminar completamente el bloque de "Acceso rápido" con sus 4 links.

---

### Fix 2: SVG icon system (macro/partial) — BARRIDO GLOBAL

**Files afectados:**
- NEW: `app/templates/agricultural/partials/icons.html` — Jinja2 macro
- `app/templates/base_agricultural.html` — bottom nav
- `app/templates/agricultural/dashboard.html` — alertas ⚠️
- `app/templates/agricultural/module_detail.html` — acciones ✎🗑
- `app/templates/agricultural/greenhouse_detail.html` — acciones ✎🗑
- `app/templates/agricultural/greenhouse_list.html` — alertas ⚠️
- `app/templates/agricultural/monitoring_execution.html` — thermal 🌡️, status ⚠️
- `app/templates/agricultural/export_detail.html` — warnings ⚠️
- `app/templates/error.html` — error ⚠️
- `app/templates/auth/login.html` — branding 🍅
- `src/application/services/history_service.py` — `_ACTIVITY_ICONS` → icon_key strings

**Approach:**
1. Crear `partials/icons.html` con macro: `{% macro icon(name, size=24) %}...{% endmacro %}`
2. Definir SVGs inline para: home, plant, package, sync, camera, water, lab, tomato, scissors, leaf, vine, broom, eye, search, note, clipboard, warning, thermometer, edit, delete, error
3. Cada icon: `<svg>` con viewBox, currentColor, aria-hidden="true", tamaño por CSS
4. Reemplazar TODOS los emojis funcionales identificados en el barrido
5. En `history_service.py`: cambiar `_ACTIVITY_ICONS` a icon_key strings semánticos (e.g., "watering", "fertilization", "phytosanitary", "harvest", "pruning", "defoliation", "trellising", "cleaning", "inspection", "pest_monitoring", "observation", "monitoring")
6. El template de module_detail resolverá el icono SVG usando la macro basándose en `item.icon_key`
7. Mantener caracteres tipográficos simples universalmente soportados ("+", "←") donde no representen iconografía compleja
8. Agregar test/barrido que impida reintroducir emojis funcionales conocidos en templates

---

### Fix 3: CSS safe margins para portrait 480×800

**File:** `app/static/css/agricultural.css`

**Approach (NO overflow-x: hidden global):**
```css
@media (max-width: 600px), (orientation: portrait) {
    .main-content {
        padding-left: 20px;
        padding-right: 20px;
    }

    .app-header {
        padding-left: 20px;
        padding-right: 20px;
    }

    /* Prevent overflow via proper sizing, not hiding */
    .card,
    .info-panel,
    .alert,
    .form-control,
    .btn {
        max-width: 100%;
        box-sizing: border-box;
    }

    /* Buttons full-width must not touch edges — they inherit container padding */
    .btn-full {
        width: 100%;
    }

    /* Header actions spacing */
    .header-actions {
        padding-right: 0; /* parent already has 20px */
    }
}
```

**NO** usar `overflow-x: hidden` para ocultar contenido. Si un componente causa overflow, corregir ese componente. Mantener bottom navigation full-bleed.

---

### Fix 4: Optional dimensions — end-to-end CON MIGRACIÓN SQLITE

**Capas afectadas:**
1. `src/domain/entities/monitoring.py` — `width_m: Optional[float] = None`, `length_m: Optional[float] = None`
2. `src/infrastructure/persistence/models/monitoring_model.py` — `nullable=True` para width_m y length_m
3. `src/infrastructure/persistence/database.py` — Migración SQLite en `_migrate_add_columns()` o nuevo helper
4. `src/application/validators.py` — Nueva función `validate_optional_dimensions()`
5. `src/application/services/monitoring_service.py` — `start_session(width_m: Optional[float], length_m: Optional[float])`
6. `app/routes/agricultural_ui.py` — Usar `validate_optional_dimensions()`, pasar Optional; change `Form(...)` → `Form("")` for width_m/length_m to prevent FastAPI 422 on empty/missing fields
7. `app/templates/agricultural/monitoring_setup.html` — Quitar `required`, agregar hint
8. Todos los mappers/converters entre entity↔model que asuman float
9. Test fixtures/factories que construyan Monitoring
10. `context_builders.py` o cualquier serializer que asuma `float`

**Estrategia de migración SQLite:**

`DatabaseManager` ya usa `_migrate_add_columns()` con `PRAGMA table_info` y `ALTER TABLE`. Pero SQLite **NO soporta ALTER COLUMN** para cambiar nullable constraints.

**Solución segura (respetando foreign keys):**

SQLite tiene `foreign_keys=ON` (configurado via PRAGMA en event listener). La tabla `monitorings` es padre de `snapshots` (monitoring_id FK) y `monitoring_metrics` (monitoring_id FK). Renombrar la tabla padre con FK activas puede causar problemas. La estrategia correcta:

```python
def _migrate_dimensions_nullable(engine) -> None:
    """Migrate width_m/length_m from NOT NULL to nullable.
    
    SQLite doesn't support ALTER COLUMN. Strategy:
    1. PRAGMA table_info → detect if width_m is NOT NULL
    2. If already nullable → no-op (idempotent)
    3. Close any open transaction, disable foreign_keys via raw DBAPI, VERIFY OFF
    4. BEGIN transaction
    5. Create monitorings_new with correct schema
    6. Copy data with EXPLICIT column list
    7. DROP old monitorings
    8. RENAME monitorings_new → monitorings
    9. PRAGMA foreign_key_check → if violations: ROLLBACK + ERROR
    10. COMMIT (only if FK check passes)
    11. Re-enable foreign_keys via raw DBAPI, VERIFY ON
    """
    with engine.connect() as conn:
        # 1. Check current schema
        result = conn.execute(text("PRAGMA table_info(monitorings)"))
        columns_info = result.fetchall()
        columns = {row[1]: row for row in columns_info}
        
        width_col = columns.get("width_m")
        if width_col is None:
            return  # Table doesn't have the column yet
        
        # row[3] is 'notnull' flag (1 = NOT NULL, 0 = nullable)
        if width_col[3] == 0:
            return  # Already nullable, nothing to do
        
        # 2. Get explicit column list for safe INSERT
        column_names = [row[1] for row in columns_info]
        col_list = ", ".join(column_names)
        
        # 3. Close any open transaction; FK OFF must be outside transaction
        conn.commit()
        
        # Use raw DBAPI connection for reliable PRAGMA control
        raw_conn = conn.connection.dbapi_connection
        raw_conn.execute("PRAGMA foreign_keys = OFF")
        
        # VERIFY FK is actually disabled
        cursor = raw_conn.execute("PRAGMA foreign_keys")
        fk_status = cursor.fetchone()[0]
        if fk_status != 0:
            raise DatabaseInitError(
                cause="Failed to disable foreign_keys for migration",
                original=None,
            )
        
        try:
            # 4-8. All destructive ops in one transaction
            raw_conn.execute("BEGIN")
            
            raw_conn.execute("""
                CREATE TABLE monitorings_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL REFERENCES modules(id),
                    status VARCHAR(20) NOT NULL DEFAULT 'initializing',
                    started_at DATETIME NOT NULL,
                    completed_at DATETIME,
                    width_m FLOAT,
                    length_m FLOAT,
                    notes TEXT,
                    total_snapshots INTEGER NOT NULL DEFAULT 0,
                    total_detections INTEGER NOT NULL DEFAULT 0,
                    created_by_user_id INTEGER REFERENCES users(id),
                    sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                )
            """)
            
            raw_conn.execute(f"""
                INSERT INTO monitorings_new ({col_list})
                SELECT {col_list} FROM monitorings
            """)
            
            raw_conn.execute("DROP TABLE monitorings")
            raw_conn.execute("ALTER TABLE monitorings_new RENAME TO monitorings")
            
            # 9. FK check BEFORE commit — can still ROLLBACK
            violations = raw_conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raw_conn.execute("ROLLBACK")
                raise DatabaseInitError(
                    cause=f"FK integrity violation after migration: {violations}",
                    original=None,
                )
            
            # 10. All good — commit
            raw_conn.execute("COMMIT")
            
        except DatabaseInitError:
            raise
        except Exception as e:
            raw_conn.execute("ROLLBACK")
            raise DatabaseInitError(
                cause=f"Migration failed: {e}", original=e,
            )
        finally:
            # 11. Re-enable foreign_keys and VERIFY
            raw_conn.execute("PRAGMA foreign_keys = ON")
            cursor = raw_conn.execute("PRAGMA foreign_keys")
            fk_status = cursor.fetchone()[0]
            if fk_status != 1:
                raise DatabaseInitError(
                    cause="Failed to re-enable foreign_keys after migration",
                    original=None,
                )
```

**Llamar desde `init_db()` después de `create_all()` y antes de `_migrate_add_columns()`.**

**Garantías:**
- Idempotente: if notnull flag already 0 → no-op.
- FK OFF verified via raw DBAPI PRAGMA read before proceeding.
- foreign_key_check runs INSIDE transaction BEFORE commit — can rollback on violation.
- INSERT with explicit column list — no order dependency.
- Raw DBAPI connection ensures PRAGMA runs outside SQLAlchemy autobegin.
- FK ON verified after migration completes.
- Fresh install: `create_all()` creates nullable directly; migration detects notnull=0 → no-op.

**Tests de migración:**
1. Crear DB con schema previo (NOT NULL width_m/length_m)
2. Insertar monitoring + snapshot asociado + monitoring_metrics asociado
3. Ejecutar `init_db()` (incluye migración)
4. Verificar datos de monitoring preservados
5. Verificar snapshot.monitoring_id sigue apuntando correctamente
6. Verificar metrics.monitoring_id sigue apuntando correctamente
7. `PRAGMA foreign_key_check` devuelve vacío
8. `PRAGMA foreign_keys` == 1 after migration
9. Crear nuevo Monitoring con width_m=None, length_m=None → éxito
10. Ejecutar `init_db()` AGAIN → idempotente, no error

---

### Fix 5: EDGE thermal thresholds

**File:** `src/infrastructure/config/settings.py`

**Change:**
- `analysis_thermal_pause_threshold=72.0` → `78.0`
- `analysis_thermal_resume_threshold=65.0` → `72.0`

El código usa comparación `>=` para el pause threshold. Documentar como "pause at >= 78°C".

---

### Fix 6: Centralized timezone utility — BIDIRECCIONAL

**File NEW:** `src/application/utils/__init__.py` + `src/application/utils/timezone.py`

**Implementación completa:**

```python
"""Centralized timezone conversion utilities for Tomato Monitor.

Convention:
- PERSISTENCE: UTC (naive datetimes in DB are interpreted as UTC)
- DISPLAY: America/Bogota
- USER INPUT: America/Bogota → convert to UTC before persisting
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def to_bogota(dt: datetime | None) -> datetime | None:
    """Convert UTC datetime (naive or aware) to America/Bogota.
    
    Naive datetimes are assumed to be UTC.
    Returns None if input is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BOGOTA_TZ)


def bogota_to_utc(dt: datetime | None) -> datetime | None:
    """Convert a Bogota-local datetime to UTC naive (for persistence).
    
    Input is assumed to be America/Bogota local time.
    Returns naive UTC datetime suitable for SQLite storage.
    Returns None if input is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume it's Bogota local time
        dt = dt.replace(tzinfo=BOGOTA_TZ)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.replace(tzinfo=None)  # Store as naive UTC


def format_bogota(dt: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Convert UTC datetime to Bogota and format as string.
    
    Returns empty string if input is None.
    """
    local = to_bogota(dt)
    if local is None:
        return ""
    return local.strftime(fmt)


def iso_utc(dt: datetime | None) -> str:
    """Format datetime as ISO-8601 with explicit UTC 'Z' indicator.
    
    Guarantees that 'Z' means real UTC:
    - Naive datetimes: assumed to already be UTC, stamped with Z.
    - Aware datetimes: converted to UTC via astimezone() FIRST, then Z.
    Returns empty string if input is None.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        # Assume naive = UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # CONVERT to UTC regardless of source timezone (e.g., America/Bogota)
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

# Tests for iso_utc:
# - naive UTC 02:17 → "...02:17:00.000000Z"
# - aware UTC 02:17+00:00 → "...02:17:00.000000Z" 
# - aware Bogota 21:17-05:00 → "...02:17:00.000000Z" (next day if applicable)
```

**Aplicación — TODAS las fechas visibles:**

| Archivo | Uso actual | Cambio |
|---|---|---|
| `history_service.py` | `.strftime("%d/%m/%Y")` directo | Usar `format_bogota(dt, "%d/%m/%Y")` |
| `context_builders.py` | `_format_date_spanish()`, `_format_time()` | Convertir a Bogota antes de formatear |
| `dashboard.html` | `last_monitoring.started_at.strftime(...)` | Recibir ya formateado desde context o usar Jinja filter |
| `activity_list.html` | `item.occurred_at.strftime(...)` | Recibir formateado o usar filter |
| `export_list.html` | `pkg.created_at.strftime(...)` | Recibir formateado o usar filter |
| `export_detail.html` | `package.created_at.strftime(...)` | Recibir formateado o usar filter |
| `sync_status.html` | `last_export.created_at.strftime(...)` | Recibir formateado o usar filter |
| `module_detail.html` | `item.date_display`, `item.time_display` | Ya formateados desde history_service |
| `agricultural_ui.py` (activity_create) | `datetime.strptime(...)` sin conversión | Usar `bogota_to_utc()` antes de persistir |

**Estrategia para evitar duplicación:**

La aplicación usa múltiples instancias de `Jinja2Templates` creadas independientemente en cada router (`agricultural_ui.py`, `auth.py`, `ui.py`, `pipeline.py`, `sessions.py`). **No existe** un `app.jinja_env` global.

Opción elegida: **Registrar el filter en cada instancia `templates.env.filters`** en los routers relevantes, o mejor: crear un módulo compartido que configure el environment.

Implementación mínima:

```python
# src/application/utils/jinja_filters.py
"""Jinja2 template filters for the agricultural UI."""
from src.application.utils.timezone import to_bogota


def filter_to_bogota(dt, fmt="%d/%m/%Y %H:%M"):
    """Jinja2 filter: convert UTC datetime to America/Bogota formatted string."""
    local = to_bogota(dt)
    return local.strftime(fmt) if local else ""


def register_filters(templates_instance):
    """Register custom filters on a Jinja2Templates instance."""
    templates_instance.env.filters["to_bogota"] = filter_to_bogota
```

Uso en cada router que necesita el filter:

```python
# app/routes/agricultural_ui.py
from src.application.utils.jinja_filters import register_filters

templates = Jinja2Templates(directory="app/templates")
register_filters(templates)
```

Registrar en: `agricultural_ui.py`, `auth.py`, y cualquier otro router que renderice timestamps.

Uso en templates: `{{ dt | to_bogota }}` o `{{ dt | to_bogota('%H:%M') }}`

Agregar test de render que confirme que el filtro está realmente disponible en templates.

---

### Fix 7: LogService ISO-8601 con timezone — NORMALIZAR COMPARACIONES

**File:** `src/application/services/log_service.py`

**Cambios:**
1. `datetime.utcnow()` → `datetime.now(timezone.utc)` en `add_entry()`
2. En `get_entries(since=...)`: normalizar `since` a aware si es naive antes de comparar

**File:** `app/routes/monitoring_api.py`

**Cambios:**
1. Serializar timestamp con sufijo 'Z': `entry.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")`
2. En parsing de `since`: si es naive, asumir UTC y hacerlo aware antes de pasar a `get_entries()`

**Tests requeridos:**
- Crear entry → timestamp tiene tzinfo
- `since` con naive datetime → funciona sin error
- `since` con aware datetime → funciona
- Múltiples entries con filtering
- Serialización incluye 'Z'

---

### Fix 8: History title update

**File:** `app/templates/agricultural/module_detail.html`
**Change:** "Historial del módulo" → "Historial general" + texto secundario "Monitoreos y actividades agrícolas registradas en este módulo"

---

### Fix 9: Export description clarification

**File:** `app/templates/agricultural/export_list.html`
**Change:** Texto actual → "Genera un respaldo ZIP con los datos e imágenes almacenados actualmente en este dispositivo."

---

### Fix 10: Export image paths — ROBUSTEZ SIN AFIRMAR BUG

**File:** `src/application/services/export_service.py`

**Cambio:** Preferir `Snapshot.image_path` como fuente autoritativa para raw images, usando `path_sanitizer.validate_safe_path()` existente. NO usar `str.startswith()` para containment check.

```python
from src.infrastructure.security.path_sanitizer import validate_safe_path, PathTraversalError

OUTPUTS_DIR = Path("outputs")

# Use stored image_path (authoritative from CaptureWorker)
raw_path = None
if snapshot.image_path:
    try:
        # Snapshot.image_path is relative (e.g., "outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg")
        # Normalize: strip "outputs/" prefix if present, validate within OUTPUTS_DIR
        image_rel = snapshot.image_path
        if image_rel.startswith("outputs/") or image_rel.startswith("outputs\\"):
            image_rel = image_rel[len("outputs/"):]
        raw_path = validate_safe_path(image_rel, OUTPUTS_DIR.resolve())
    except PathTraversalError:
        raw_path = None  # Skip unsafe paths silently

# Derive annotated path from the raw filename
annotated_path = None
if raw_path and raw_path.name:
    annotated_dir = OUTPUTS_DIR.resolve() / "monitorings" / str(m_id) / "annotated_snapshots"
    try:
        annotated_path = validate_safe_path(raw_path.name, annotated_dir)
    except PathTraversalError:
        annotated_path = None
```

**Path sanitizer `validate_safe_path()` strengthening:**

El helper actual usa `str(candidate).startswith(str(resolved_base))` internamente para containment check. Esto es vulnerable a prefix collisions (e.g., base=`outputs`, malicious path resolves to `outputs_evil/file`).

**Corregir** el containment check en `path_sanitizer.py` usando semántica de Path:

```python
# BEFORE (vulnerable):
if not str(candidate).startswith(str(resolved_base)):
    raise PathTraversalError(...)

# AFTER (robust):
try:
    candidate.relative_to(resolved_base)
except ValueError:
    raise PathTraversalError("Resolved path escapes allowed directory")
```

`Path.relative_to()` usa semántica de directorio real, no prefijos de string.

**Tests adicionales para path traversal:**
- `snapshot.image_path = "../../../etc/passwd"` → rejected
- `snapshot.image_path = "outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg"` → accepted
- `snapshot.image_path` con null bytes → rejected
- Sibling prefix attack: base=`outputs`, path resolves to `outputs_evil/file.jpg` → rejected

---

### Fix 11: Orphan export reconciliation

**File:** `app/main.py` — dentro de `lifespan()` después de `init_db()`

**Prerequisitos en SqlExportPackageRepository:**
- Agregar `list_by_status(status: str) -> list[ExportPackage]` si no existe
- Agregar `update_status(id: int, status: str, error_message: str)` si no existe

El repo actual tiene `list_pending()` que retorna pending+generating, y `update(id, fields)` que acepta dict. Se puede usar directamente:

```python
# Reconcile orphan exports
session = db_manager.get_session()
try:
    from src.infrastructure.persistence.repositories.sql_export_package_repository import SqlExportPackageRepository
    repo = SqlExportPackageRepository(session=session)
    pending = repo.list_pending()
    orphans = [p for p in pending if p.status == "generating"]
    for orphan in orphans:
        repo.update(orphan.id, {
            "status": "error",
            "error_message": "Exportación interrumpida antes de completarse. Puede volver a intentarse.",
            "completed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        })
    session.commit()
except Exception:
    session.rollback()
finally:
    session.close()
```

---

### Fix 12: Sync UI "Modo local" + explicación de estados

**File:** `app/templates/agricultural/sync_status.html`

**Agregar:**
1. Banner prominente "Modo local"
2. Explicación de estados:
   - **Pendiente:** dato aún no incluido en un respaldo local.
   - **Exportado:** dato incluido en un paquete ZIP local.
   - **Sincronizado:** reservado para futura sincronización remota (no se marca con LocalZipSyncAdapter).

NO cambiar el modelo ni SyncService por esta aclaración visual.

---

### Fix 13: httpx en dependencias de test

**Approach:** Crear `requirements-test.txt` como overlay puro (SIN `-r` reference):

```
# Test dependencies — pure overlay.
# Install AFTER your runtime requirements file:
#   PC:        pip install -r requirements.txt -r requirements-test.txt
#   Raspberry: pip install -r requirements-raspberry.txt -r requirements-test.txt
pytest>=8.0.0
hypothesis>=6.100.0
httpx>=0.27.0
```

**Procedimiento reproducible:**

```
# PC (desarrollo):
pip install -r requirements.txt -r requirements-test.txt

# Raspberry Pi (runtime + tests):
pip install -r requirements-raspberry.txt -r requirements-test.txt
pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
```

**Correcciones adicionales en `requirements-raspberry.txt`:**
- Agregar `SQLAlchemy==2.0.41` (actualmente solo está en requirements.txt pero la app lo necesita en runtime en Raspberry)
- Corregir documentación de versión de Python: la Raspberry física validada ejecuta **Python 3.13.5**, no 3.11 como indica el header actual. Actualizar el comentario o agregar nota indicando el entorno actualmente validado.

**Relación entre archivos:**
- `requirements.txt` — dependencias base genéricas (funciona en cualquier plataforma)
- `requirements-raspberry.txt` — versiones pinned para ARM64 + todo lo necesario en runtime (incluye SQLAlchemy)
- `requirements-test.txt` — overlay puro: solo pytest, hypothesis, httpx. SIN `-r` reference. Se instala DESPUÉS del archivo de runtime.

---

### Fix 14: Activity form — conversión timezone en entrada

**File:** `app/routes/agricultural_ui.py` — función `activity_create()`

**Cambio:** Después de parsear `occurred_at` con `strptime`, convertir de Bogota a UTC:

```python
from src.application.utils.timezone import bogota_to_utc, utc_now

# Parse occurred_at from date + time
occurred_at = None
if occurred_at_date.strip():
    try:
        date_str = occurred_at_date.strip()
        time_str = occurred_at_time.strip() if occurred_at_time.strip() else "00:00"
        local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        occurred_at = bogota_to_utc(local_dt)  # Convert to UTC for persistence
    except ValueError:
        errors.append("Formato de fecha/hora inválido.")

# If no date provided, use current UTC time
if occurred_at is None and not errors:
    occurred_at = utc_now().replace(tzinfo=None)  # Naive UTC for DB
```

**Test de round-trip:**
- Operador escribe 19/08/2026 21:17 → se persiste como ~02:17 UTC
- Al mostrar en la UI → `to_bogota()` → 19/08/2026 21:17 ✓

---

## Preservation Requirements

### Componentes que NO se modifican funcionalmente:

- `src/application/services/capture_worker.py` — sin cambios
- `src/application/services/snapshot_analysis_service.py` — sin cambios
- `src/application/services/monitoring_service.py` — solo cambio de type hint `float → Optional[float]`
- `src/application/services/monitoring_runtime_registry.py` — sin cambios
- `src/domain/value_objects/monitoring_status.py` — sin cambios
- `src/infrastructure/camera/*` — sin cambios
- `src/infrastructure/vision/*` — sin cambios
- `app/static/js/monitoring.js` — sin cambios
- `app/templates/agricultural/monitoring_execution.html` — solo reemplazo de emojis por SVG icons (sin cambio lógico)
- `app/templates/agricultural/monitoring_report.html` — sin cambios funcionales

### Invariantes protegidos:

- Lifecycle capture-first: start → capture → finalize → release camera → analyze → complete
- FSM transitions: INITIALIZING → RUNNING → ANALYZING → COMPLETED
- Single camera owner (CaptureWorker)
- Orden: capture → release → analysis
- Tracking, detección, clasificación, inferencia
- Lógica de Scene Gate y cooldown/timeout
- FULL profile thresholds (78/72) sin modificar

---

## Testing Strategy

### Tests de migración SQLite (Fix 4):
1. Crear DB con schema previo (NOT NULL width_m/length_m)
2. Insertar monitorings con valores existentes (5.0, 3.0)
3. Ejecutar `init_db()` (que incluye migración)
4. Verificar datos existentes preservados
5. Crear nuevo Monitoring con width_m=None, length_m=None → éxito
6. Crear nuevo Monitoring con width_m=5.0, length_m=3.0 → éxito
7. Crear nuevo Monitoring con width_m=5.0, length_m=None → error validación

### Tests de timezone (Fixes 6, 14):
1. `to_bogota(naive_utc)` → offset -5h
2. `to_bogota(aware_utc)` → offset -5h
3. `to_bogota(None)` → None
4. `bogota_to_utc(local_21_17)` → UTC 02:17 next day
5. Round-trip: user enters 21:17 → persist → display → shows 21:17
6. Colombia no tiene DST → offset siempre -5h

### Tests de LogService (Fix 7):
1. `add_entry()` → timestamp has tzinfo
2. `get_entries(since=naive)` → no crash, normalizes
3. `get_entries(since=aware)` → works correctly
4. API serialization includes 'Z' or '+00:00'

### Tests de export (Fix 10):
1. Export sin snapshots → images_count=0 (correcto)
2. Export con snapshots y archivos → images_count > 0
3. Solo raw → incluido
4. Solo annotated → incluido
5. Ambos → ambos incluidos
6. Archivo faltante → files_missing

### Tests de orphan reconciliation (Fix 11):
1. 0 orphans → no cambia nada
2. 1 orphan generating → transitioned to error
3. Multiple orphans → all transitioned
4. Completed exports → NOT affected

### Test de no-emoji (Fix 2):
- Barrido: ningún template activo contiene emojis funcionales conocidos
- Bottom nav contiene `<svg` elements
- `history_service.py` `_ACTIVITY_ICONS` devuelve strings sin Unicode emoji
