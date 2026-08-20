# Data Model Steering - Tomato Monitor

## Database technology

- **Engine:** SQLite 3 (local file, no server process)
- **ORM:** SQLAlchemy (with declarative models)
- **File location:** `data/tomato_monitor.db` (excluded from version control via `.gitignore`)
- **Local-first source of truth:** SQLite is the operational source of truth. The system must function fully offline. Remote export/synchronization is optional, manual, and provider-agnostic — it must not be required for monitoring, activity logging, or reporting. ORM configuration must not point directly to a remote operational database. Remote sync goes through explicit application services/adapters, never by replacing the local SQLite engine.

## Entity hierarchy

```
User (Usuario/Operario)
  ├── Creates → Monitoring
  ├── Creates → ActivityLog
  └── Creates → ExportPackage

Greenhouse (Invernadero)
  └── 1:N → Module (Módulo)
                ├── 1:N → Monitoring (Monitoreo)
                │             ├── 1:N → Snapshot
                │             │           └── 1:N → InspectionResult
                │             └── 1:1 → MonitoringMetrics
                └── 1:N → ActivityLog (Bitácora)
                              └── N:1 → ActivityType (Catálogo)
```

---

## Entities

### Greenhouse

Represents the physical greenhouse infrastructure.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `name` | String(100) | NOT NULL, UNIQUE | Display name (e.g., "Invernadero Experimental 1") |
| `location` | String(200) | nullable | Optional physical location description |
| `created_at` | DateTime | NOT NULL, default=now | Record creation timestamp |
| `updated_at` | DateTime | NOT NULL, auto-update | Last modification timestamp |

**Relations:** One greenhouse has many modules.
**CRUD:** Create, read all, read by id, update name/location, delete (cascade to modules).

---

### Module

Represents a rectangular, delimited growing area within a greenhouse.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `greenhouse_id` | Integer | FK → Greenhouse, NOT NULL | Parent greenhouse |
| `name` | String(100) | NOT NULL | Display name (e.g., "Módulo 1") |
| `crop_type` | String(100) | NOT NULL, default="Tomate Cherry" | Current crop planted |
| `width_m` | Float | nullable | Module width in meters |
| `length_m` | Float | nullable | Module length in meters |
| `monitoring_frequency_days` | Integer | nullable | Expected monitoring frequency in days (default: 7 for tomato cherry) |
| `created_at` | DateTime | NOT NULL, default=now | |
| `updated_at` | DateTime | NOT NULL, auto-update | |

**Relations:** Belongs to one greenhouse. Has many monitorings.
**CRUD:** Create under greenhouse, read by greenhouse, read by id, update, delete (cascade to monitorings).
**Constraint:** (`greenhouse_id`, `name`) should be unique — no duplicate module names within the same greenhouse.

---

### Monitoring

Represents a single portable monitoring session for a module, where the operator manually traverses the area with the device.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `module_id` | Integer | FK → Module, NOT NULL | Module being monitored |
| `created_by_user_id` | Integer | FK → User, nullable | Operator who executed (nullable for migration compatibility) |
| `status` | String(20) | NOT NULL, default="initializing" | One of: `initializing`, `running`, `paused`, `finishing`, `analyzing`, `completed`, `aborted`, `error` |
| `started_at` | DateTime | NOT NULL, default=now | When the monitoring began |
| `completed_at` | DateTime | nullable | When the monitoring ended |
| `width_m` | Float | NOT NULL | Module width confirmed at monitoring start |
| `length_m` | Float | NOT NULL | Module length confirmed at monitoring start |
| `notes` | Text | nullable | Optional farmer notes |
| `total_snapshots` | Integer | NOT NULL, default=0 | Count of captured snapshots |
| `total_detections` | Integer | NOT NULL, default=0 | Count of tomatoes detected |
| `sync_status` | String(20) | NOT NULL, default="pending" | pending, exported, synced, error |

**Relations:** Belongs to one module. Has many snapshots. Has one MonitoringMetrics.
**CRUD:** Create under module, read by module, read by id, update status/counters, delete (cascade to snapshots and metrics).
**Status transitions:** `initializing → running → analyzing → completed` (capture-first happy path). Also: `running → finishing → completed` (legacy), `running → paused → running`, `running → aborted` (explicit cancellation only), `analyzing → completed`, `analyzing → error`, `any non-terminal → error`. `aborted` is reserved exclusively for explicit operator cancellation; system failures transition to `error`.

---

### Snapshot

Represents a single image captured during a monitoring session when the change detector triggered.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `monitoring_id` | Integer | FK → Monitoring, NOT NULL | Parent monitoring session |
| `captured_at` | DateTime | NOT NULL, default=now | Exact moment of capture |
| `image_path` | String(500) | NOT NULL | Relative path to stored image in filesystem |
| `frame_index` | Integer | NOT NULL | Sequence number within the monitoring |
| `change_score` | Float | nullable | Score from the change detector that triggered capture |
| `has_detections` | Boolean | NOT NULL, default=False | Whether inference found at least one tomato |

**Relations:** Belongs to one monitoring. Has many inspection results.
**CRUD:** Create during monitoring, read by monitoring, read by id. Delete only via cascade from monitoring.
**File storage:** Images stored at `outputs/monitorings/{monitoring_id}/snapshots/snapshot_{frame_index}.jpg`

---

### InspectionResult

Represents a single detected tomato within a snapshot, with its health and maturity assessments.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `snapshot_id` | Integer | FK → Snapshot, NOT NULL | Source snapshot |
| `detection_index` | Integer | NOT NULL | Detection index within the snapshot |
| `bbox_x1` | Integer | NOT NULL | Bounding box left |
| `bbox_y1` | Integer | NOT NULL | Bounding box top |
| `bbox_x2` | Integer | NOT NULL | Bounding box right |
| `bbox_y2` | Integer | NOT NULL | Bounding box bottom |
| `detection_score` | Float | NOT NULL | Detector confidence score |
| `health_label` | String(20) | NOT NULL | `healthy` or `unhealthy` |
| `health_confidence` | Float | NOT NULL | Health classifier confidence |
| `maturity_stage` | String(20) | nullable | USDA stage (green, breaker, turning, pink, light_red, red) |
| `maturity_percent` | Float | nullable | Maturity percentage (0-100) |
| `created_at` | DateTime | NOT NULL, default=now | |

**Relations:** Belongs to one snapshot.
**CRUD:** Create during inference, read by snapshot, read by monitoring (join). Delete only via cascade.
**Note:** `maturity_stage` is nullable because maturity is only estimated for healthy tomatoes with sufficient detection score.

---

### MonitoringMetrics

Pre-computed aggregated metrics for a completed monitoring session. Calculated once at monitoring completion and stored for fast retrieval.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `monitoring_id` | Integer | FK → Monitoring, UNIQUE, NOT NULL | One-to-one with monitoring |
| `total_tomatoes` | Integer | NOT NULL | Unique tomatoes detected across all snapshots |
| `healthy_count` | Integer | NOT NULL | Tomatoes classified as healthy |
| `unhealthy_count` | Integer | NOT NULL | Tomatoes classified as unhealthy |
| `pct_healthy` | Float | NOT NULL | Percentage healthy (0-100) |
| `pct_unhealthy` | Float | NOT NULL | Percentage unhealthy (0-100) |
| `pct_green` | Float | NOT NULL, default=0 | % green stage |
| `pct_breaker` | Float | NOT NULL, default=0 | % breaker stage |
| `pct_turning` | Float | NOT NULL, default=0 | % turning stage |
| `pct_pink` | Float | NOT NULL, default=0 | % pink stage |
| `pct_light_red` | Float | NOT NULL, default=0 | % light_red stage |
| `pct_red` | Float | NOT NULL, default=0 | % red stage |
| `snapshots_with_detections` | Integer | NOT NULL | Number of snapshots that had at least one detection |
| `computed_at` | DateTime | NOT NULL, default=now | When metrics were calculated |

**Relations:** One-to-one with Monitoring.
**CRUD:** Create at monitoring completion or finalization. Read by monitoring. No manual updates — recalculate if needed.
**Note:** Metrics are computed when the monitoring reaches `completed` state (after `analyzing` finishes successfully). For `aborted` sessions, partial metrics with zero values are persisted. The `analyzing` state does NOT have metrics yet — they are created only upon successful transition to `completed`.

---

## Entities — Traceability and Operations (Spec 015 target)

### User

Represents an authenticated operator of the system.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `full_name` | String(150) | NOT NULL | Operator's display name |
| `email` | String(200) | NOT NULL, UNIQUE | Login identifier |
| `password_hash` | String(255) | NOT NULL | Securely hashed password |
| `role` | String(20) | NOT NULL, default="operator" | operator, admin |
| `is_active` | Boolean | NOT NULL, default=True | Whether user can log in |
| `remote_user_id` | String(100) | nullable | ID in remote system (future sync) |
| `sync_status` | String(20) | NOT NULL, default="local_only" | local_only, synced, pending_sync |
| `created_at` | DateTime | NOT NULL, default=now | |
| `updated_at` | DateTime | NOT NULL, auto-update | |
| `last_login_at` | DateTime | nullable | Last successful login |

**Relations:** Creates monitorings, activities, exports.
**CRUD:** Create (registration), read by email, update profile/password, deactivate.
**Note:** Registration is online-oriented/prototyped locally. Login works offline for active users.

---

### ActivityType

Represents a type of agricultural activity from the backend-defined catalog.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `code` | String(50) | NOT NULL, UNIQUE | Machine-readable code (e.g., "riego", "poda") |
| `name` | String(100) | NOT NULL | Display name (e.g., "Riego", "Poda") |
| `category` | String(50) | NOT NULL | Category (mantenimiento, nutrición, protección, vigilancia, manejo_planta, producción, general) |
| `requires_product` | Boolean | NOT NULL, default=False | Whether this activity requires a product name |
| `allows_quantity` | Boolean | NOT NULL, default=False | Whether quantity can be recorded |
| `default_unit` | String(20) | nullable | Default unit (L, kg, mL, unidades) |
| `is_active` | Boolean | NOT NULL, default=True | Whether type appears in UI selector |

**Relations:** Referenced by ActivityLog entries.
**CRUD:** Read all active, read by code. **Not editable from UI in this version.** Seeded from backend on init_db.
**Note:** The catalog is defined at the application level and inserted during database initialization. The UI does not allow creating or editing activity types.

---

### ActivityLog

Represents a single agricultural activity performed on a module by an operator.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `module_id` | Integer | FK → Module, NOT NULL | Module where activity was performed |
| `activity_type_id` | Integer | FK → ActivityType, NOT NULL | Type of activity |
| `user_id` | Integer | FK → User, NOT NULL | Operator who registered the activity |
| `product_name` | String(150) | nullable | Product used (fertilizer, fungicide, etc.) |
| `quantity` | Float | nullable | Amount applied |
| `unit` | String(20) | nullable | Unit of measurement (L, kg, mL) |
| `notes` | Text | nullable | Operator observations |
| `occurred_at` | DateTime | NOT NULL, default=now | When the activity happened |
| `created_at` | DateTime | NOT NULL, default=now | When it was registered in the system |
| `sync_status` | String(20) | NOT NULL, default="pending" | pending, exported, synced, error |

**Relations:** Belongs to one module, one activity type, one user.
**CRUD:** Create, read by module, read by user, read all. Delete only via cascade from module.
**Note:** Activities are distinguishable from monitoring events in history views. Traceability: user + occurred_at + module + type.

---

### ExportPackage

Represents a ZIP export package generated by the system.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `created_by_user_id` | Integer | FK → User, NOT NULL | Operator who requested export |
| `scope` | String(50) | NOT NULL | Export scope: full, greenhouse, module, monitoring |
| `scope_id` | Integer | nullable | ID of scoped resource (if not full) |
| `file_path` | String(500) | nullable | Relative path to generated ZIP |
| `file_size_bytes` | Integer | nullable | Size of generated file |
| `status` | String(20) | NOT NULL, default="pending" | pending, generating, completed, error |
| `error_message` | Text | nullable | Error details if failed |
| `records_count` | Integer | NOT NULL, default=0 | Data records included |
| `images_count` | Integer | NOT NULL, default=0 | Image files included |
| `created_at` | DateTime | NOT NULL, default=now | When export was requested |
| `completed_at` | DateTime | nullable | When export finished |
| `manifest_json` | Text | nullable | JSON manifest with checksums and counts |

**Relations:** Created by one user.
**CRUD:** Create, read by user, read by id, update status. Delete only manually.
**Note:** ZIP files stored at `outputs/exports/export_{id}_{timestamp}.zip`.

---

### OperationalAlert (computed, not persisted in MVP)

Represents a computed operational alert based on system state.

```python
@dataclass
class OperationalAlert:
    alert_type: str          # monitoring_overdue, monitoring_pending, export_pending, analysis_error, thermal_warning
    severity: str            # info, warning, critical
    title: str               # Short display title
    message: str             # Detail message
    module_id: int | None    # Related module (if applicable)
    source: str              # Data source for the alert
    created_at: datetime     # Computation timestamp
```

**Not persisted in MVP.** Computed dynamically when dashboard/alerts are loaded. When a condition is no longer true, the alert disappears. Persistent alert tracking (with resolved_at, status) is optional for future iterations.

**No hay entidad Jornada/Turno.** La trazabilidad se logra con user + timestamp + module + activity/monitoring.

---

## Integrity rules

- **Cascade deletes:** Deleting a greenhouse cascades to modules → monitorings → snapshots → results → metrics. Deleting a module also cascades to activity logs.
- **Orphan protection:** No snapshot or result can exist without a parent monitoring. No activity log without a parent module.
- **Status consistency:** MonitoringMetrics is only created when monitoring status is `completed` or `aborted` (partial metrics for aborted are allowed).
- **Image paths:** Stored as relative paths from the project root. Absolute paths are not persisted.
- **Local-first source of truth:** SQLite is the operational source of truth. Remote export/sync is allowed only through explicit provider-agnostic application services and must not be required for monitoring, activity logging, or reporting.
- **Timestamps:** All timestamps use UTC. Display layer converts to local timezone for the operator.
- **User traceability:** Monitoring and ActivityLog records reference the user who created them.
- **Nullable user FK on Monitoring:** `created_by_user_id` is nullable to allow migration without breaking existing data.

## Migration strategy

- Use SQLAlchemy `create_all()` for initial schema creation (no migration tool required at this stage).
- New columns added to existing tables (monitoring_frequency_days, created_by_user_id, sync_status) MUST be nullable or have defaults for migration safety.
- If schema evolves further, use Alembic for migrations (add only when needed, not preemptively).
- The CSV persistence layer (`src/infrastructure/persistence/local/`) remains available for legacy benchmark mode but is not used by the new monitoring flow.
- Activity type catalog is seeded during init_db() — similar to create_all() but for reference data.

## Rules for Kiro

- When creating new entities or modifying the data model, update this steering file first.
- Do not add columns without documenting them here.
- Do not use raw SQL queries in application or domain code — use SQLAlchemy ORM.
- Do not store binary image data in the database — use filesystem paths.
- Do not add indexes prematurely — optimize based on measured query performance.
- Keep the domain layer (`src/domain/`) free of SQLAlchemy imports — use repository interfaces.
- ActivityType catalog is backend-defined; do not implement UI editing in this version.
- OperationalAlert is computed; do not create a persistence table unless needed for export evidence.
- New columns on existing tables must be nullable or have defaults to avoid breaking existing data.
