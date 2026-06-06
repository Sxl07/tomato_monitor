# Data Model Steering - Tomato Monitor

## Database technology

- **Engine:** SQLite 3 (local file, no server process)
- **ORM:** SQLAlchemy (with declarative models)
- **File location:** `data/tomato_monitor.db` (excluded from version control via `.gitignore`)
- **Remote persistence:** NOT allowed in operational mode. The system must function fully offline.

## Entity hierarchy

```
Greenhouse (Invernadero)
  └── 1:N → Module (Módulo)
                └── 1:N → Monitoring (Monitoreo)
                              ├── 1:N → Snapshot
                              │           └── 1:N → InspectionResult
                              └── 1:1 → MonitoringMetrics
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
| `created_at` | DateTime | NOT NULL, default=now | |
| `updated_at` | DateTime | NOT NULL, auto-update | |

**Relations:** Belongs to one greenhouse. Has many monitorings.
**CRUD:** Create under greenhouse, read by greenhouse, read by id, update, delete (cascade to monitorings).
**Constraint:** (`greenhouse_id`, `name`) should be unique — no duplicate module names within the same greenhouse.

---

### Monitoring

Represents a single robot traversal and monitoring session for a module.

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | Integer | PK, autoincrement | Unique identifier |
| `module_id` | Integer | FK → Module, NOT NULL | Module being monitored |
| `status` | String(20) | NOT NULL, default="initializing" | One of: `initializing`, `running`, `paused`, `finishing`, `completed`, `aborted`, `error` |
| `started_at` | DateTime | NOT NULL, default=now | When the monitoring began |
| `completed_at` | DateTime | nullable | When the monitoring ended |
| `width_m` | Float | NOT NULL | Module width confirmed at monitoring start |
| `length_m` | Float | NOT NULL | Module length confirmed at monitoring start |
| `notes` | Text | nullable | Optional farmer notes |
| `total_snapshots` | Integer | NOT NULL, default=0 | Count of captured snapshots |
| `total_detections` | Integer | NOT NULL, default=0 | Count of tomatoes detected |

**Relations:** Belongs to one module. Has many snapshots. Has one MonitoringMetrics.
**CRUD:** Create under module, read by module, read by id, update status/counters, delete (cascade to snapshots and metrics).
**Status transitions:** `initializing → running → finishing → completed` (happy path). Also: `running → paused → running`, `running → aborted`, `any → error`.

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
**CRUD:** Create at monitoring completion. Read by monitoring. No manual updates — recalculate if needed.

---

## Integrity rules

- **Cascade deletes:** Deleting a greenhouse cascades to modules → monitorings → snapshots → results → metrics.
- **Orphan protection:** No snapshot or result can exist without a parent monitoring.
- **Status consistency:** MonitoringMetrics is only created when monitoring status is `completed` or `aborted` (partial metrics for aborted are allowed).
- **Image paths:** Stored as relative paths from the project root. Absolute paths are not persisted.
- **No remote writes:** The SQLite database file is the single source of truth. No ORM configuration may point to a remote database in operational mode.
- **Timestamps:** All timestamps use UTC. Display layer converts to local timezone for the farmer.

## Migration strategy

- Use SQLAlchemy `create_all()` for initial schema creation (no migration tool required at this stage).
- If schema evolves, use Alembic for migrations (add only when needed, not preemptively).
- The CSV persistence layer (`src/infrastructure/persistence/local/`) remains available for legacy benchmark mode but is not used by the new monitoring flow.

## Rules for Kiro

- When creating new entities or modifying the data model, update this steering file first.
- Do not add columns without documenting them here.
- Do not use raw SQL queries in application or domain code — use SQLAlchemy ORM.
- Do not store binary image data in the database — use filesystem paths.
- Do not add indexes prematurely — optimize based on measured query performance.
- Keep the domain layer (`src/domain/`) free of SQLAlchemy imports — use repository interfaces.
