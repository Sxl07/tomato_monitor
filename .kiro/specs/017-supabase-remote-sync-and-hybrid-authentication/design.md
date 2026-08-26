# Design Document

## Overview

Este diseño extiende Tomato Monitor con autenticación híbrida (remota vía Supabase Auth + fallback local) y sincronización manual unidireccional (SQLite → Supabase), preservando la arquitectura offline-first existente. Los nuevos componentes se integran como puertos abstractos en la capa de aplicación con adaptadores concretos en infraestructura, sin tocar el pipeline capture-first ni componentes estables.

**Decisiones arquitectónicas clave:**

1. **Clean Architecture con puertos:** La capa de aplicación depende de abstracciones (`RemoteAuthPort`, `RemoteDataPort`, `RemoteStoragePort`, `SyncStatePort`). Las implementaciones concretas viven en infraestructura.
2. **httpx como dependencia directa de runtime:** Se usa httpx (pure Python, ARM64 compatible) para comunicación con Supabase REST API. Debe agregarse a `requirements.txt` y `requirements-raspberry.txt` ANTES de implementar los adapters (`requirements-test.txt` ya lo declara para testing).
3. **Perfiles por trigger:** El perfil remoto se crea automáticamente por un trigger `AFTER INSERT` en `auth.users`. La app NO hace POST manual a profiles.
4. **UUID pre-generado:** Los `remote_id` se generan localmente (UUID v4) y se persisten ANTES del envío remoto, eliminando la ventana de crash.
5. **JWT efímero:** Nunca se persiste. Para sync: operario proporciona password → sign_in → JWT → sync → descartar.
6. **Sin health check previo a login:** Se intenta sign_in directamente y se clasifica la respuesta.

---

## Architecture

### Integración en Clean Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Presentación (app/)                          │
│  routes/auth.py        — login, registro, logout                    │
│  routes/sync_api.py    — trigger sync, status, password prompt      │
│  templates/            — formularios registro, indicadores sync      │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Depends()
┌────────────────────────────▼────────────────────────────────────────┐
│                      Aplicación (src/application/)                   │
│  services/hybrid_auth_service.py   — orquesta auth remota + local   │
│  services/remote_sync_service.py   — orquesta sync manual           │
│  interfaces/remote_auth_port.py    — Protocol para auth remota      │
│  interfaces/remote_data_port.py    — Protocol para push de datos    │
│  interfaces/remote_storage_port.py — Protocol para upload archivos  │
│  interfaces/sync_state_port.py     — Protocol para estado de sync   │
│  dtos/sync_dtos.py                 — SyncProgress, SyncResult       │
└────────────────────────────┬────────────────────────────────────────┘
                             │ implementa puertos
┌────────────────────────────▼────────────────────────────────────────┐
│                     Infraestructura (src/infrastructure/)            │
│  supabase/                                                          │
│    supabase_config.py              — lee y valida env vars          │
│    supabase_auth_adapter.py        — implementa RemoteAuthPort      │
│    supabase_data_adapter.py        — implementa RemoteDataPort      │
│    supabase_storage_adapter.py     — implementa RemoteStoragePort   │
│  persistence/                                                       │
│    sync_state_repository.py        — implementa SyncStatePort       │
│    database.py                     — _migrate_add_sync_columns()    │
│    models/                         — columnas nuevas en modelos ORM │
└─────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                        Dominio (src/domain/)                         │
│  entities/user.py              — ya tiene remote_user_id            │
│  (sin cambios funcionales al dominio)                               │
└─────────────────────────────────────────────────────────────────────┘
```

### Principio de separación

- **Dominio:** Sin cambios significativos. Las entidades ya tienen campos relevantes.
- **Aplicación:** `HybridAuthService` y `RemoteSyncService` son coordinadores puros; dependen de puertos abstractos (Protocol/ABC), nunca importan httpx ni clases Supabase.
- **Infraestructura:** Los adapters encapsulan toda comunicación HTTP con Supabase e implementan los puertos definidos en aplicación.
- **Presentación:** Nuevas rutas delegando a servicios de aplicación vía inyección de dependencias.

### Flujo de datos de sincronización

```
SyncStatePort                RemoteDataPort              RemoteStoragePort
     │                            │                            │
     │ get_pending_entities()     │                            │
     │ reserve_remote_id()        │                            │
     │                            │ upsert(id=UUID, data)      │
     │                            │ Prefer: merge-duplicates   │
     │                            │                            │ upload(path, file)
     │ mark_synced()              │                            │
     │ set_storage_paths()        │                            │
     ▼                            ▼                            ▼
```

---

## Components and Interfaces

### 1. Puertos abstractos (Application layer)

**Ubicación:** `src/application/interfaces/`

```python
# src/application/interfaces/remote_auth_port.py
from typing import Protocol, Optional
from dataclasses import dataclass

@dataclass
class RemoteAuthResult:
    success: bool
    user_id: Optional[str] = None       # UUID de Supabase
    access_token: Optional[str] = None  # JWT efímero (no persistir)
    email: Optional[str] = None         # Email confirmado por Auth
    full_name: Optional[str] = None     # De user_metadata (para crear User local en primer login)
    error_type: Optional[str] = None    # Ver clasificación de errores
    error_message: Optional[str] = None

class RemoteAuthPort(Protocol):
    """Puerto para operaciones de autenticación remota."""
    
    def sign_up(self, email: str, password: str, full_name: str) -> RemoteAuthResult:
        """Registrar usuario remoto. Envía metadata full_name. NO envía role."""
        ...
    
    def sign_in(self, email: str, password: str) -> RemoteAuthResult:
        """Login remoto directo. Clasifica respuesta/excepción."""
        ...
```

```python
# src/application/interfaces/remote_data_port.py
from typing import Protocol, Optional
from dataclasses import dataclass

@dataclass
class RemoteUpsertResult:
    success: bool
    remote_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

class RemoteDataPort(Protocol):
    """Puerto para push de datos a backend remoto vía PostgREST."""
    
    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        """Upsert con id=UUID explícito y Prefer: resolution=merge-duplicates."""
        ...
```

```python
# src/application/interfaces/remote_storage_port.py
from typing import Protocol, Optional
from dataclasses import dataclass

@dataclass
class RemoteUploadResult:
    success: bool
    object_path: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

class RemoteStoragePort(Protocol):
    """Puerto para subida de archivos a almacenamiento remoto."""
    
    def upload_file(
        self,
        access_token: str,
        local_file_path: str,
        remote_path: str,
    ) -> RemoteUploadResult:
        """Sube archivo. Si ya existe, sobrescribe (idempotencia)."""
        ...
```

```python
# src/application/interfaces/sync_state_port.py
from typing import Protocol, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class StoragePaths:
    raw_storage_path: Optional[str] = None
    annotated_storage_path: Optional[str] = None

class SyncStatePort(Protocol):
    """Puerto para persistencia del estado de sincronización.
    
    Permite consultar y actualizar el estado de sync sin modificar
    las interfaces de repositorios agrícolas existentes.
    """
    
    def get_pending_entities(self, entity_type: str) -> list[dict]:
        """Retorna entidades con remote_sync_status != 'synced'."""
        ...
    
    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        """Retorna el remote_id (UUID) de la entidad, o None."""
        ...
    
    def reserve_remote_id(self, entity_type: str, local_id: int, uuid: str) -> None:
        """Pre-genera y persiste un UUID con status='pending'."""
        ...
    
    def mark_syncing(self, entity_type: str, local_id: int) -> None:
        """Actualiza remote_sync_status a 'syncing'."""
        ...
    
    def mark_synced(self, entity_type: str, local_id: int, remote_id: str) -> None:
        """Actualiza remote_sync_status a 'synced' y last_synced_at."""
        ...
    
    def mark_error(self, entity_type: str, local_id: int, error_msg: str) -> None:
        """Actualiza remote_sync_status a 'error' y remote_sync_error."""
        ...
    
    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        """Retorna rutas de Storage ya registradas para un snapshot."""
        ...
    
    def set_storage_paths(self, snapshot_id: int, raw_path: Optional[str], annotated_path: Optional[str]) -> None:
        """Registra rutas de Storage tras subida exitosa."""
        ...
```

---

### 2. `SupabaseConfig` — Configuración

**Ubicación:** `src/infrastructure/supabase/supabase_config.py`

```python
@dataclass(frozen=True)
class SupabaseConfig:
    url: str                    # SUPABASE_URL
    publishable_key: str        # SUPABASE_PUBLISHABLE_KEY
    storage_bucket: str         # SUPABASE_STORAGE_BUCKET (default: "tomato-monitor-snapshots")
    
    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.publishable_key)

    @property
    def auth_url(self) -> str:
        return f"{self.url}/auth/v1"
    
    @property
    def rest_url(self) -> str:
        return f"{self.url}/rest/v1"
    
    @property
    def storage_url(self) -> str:
        return f"{self.url}/storage/v1"


def load_supabase_config() -> Optional[SupabaseConfig]:
    """Carga config desde env. Retorna None si no está configurado.
    
    Reglas:
    - Si SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY vacías → None (offline-only).
    - Si SUPABASE_URL presente pero key ausente → WARNING + None.
    - Si SUPABASE_SERVICE_ROLE_KEY está definida como variable de entorno → ERROR + None.
    - Valida SUPABASE_URL tiene esquema https (producción).
    - Valida SUPABASE_PUBLISHABLE_KEY no vacía.
    - Si todo válido → instancia configurada.
    """
```

---

### 3. `SupabaseAuthAdapter` — Implementa RemoteAuthPort

**Ubicación:** `src/infrastructure/supabase/supabase_auth_adapter.py`

```python
class SupabaseAuthAdapter:
    """Implementa RemoteAuthPort usando Supabase Auth REST API vía httpx."""
    
    def __init__(self, config: SupabaseConfig):
        ...
    
    def sign_up(self, email: str, password: str, full_name: str) -> RemoteAuthResult:
        """POST /auth/v1/signup con data={"email", "password", "data": {"full_name": full_name}}.
        
        NO envía role. El trigger remoto asigna 'operator' automáticamente.
        NO hace POST a profiles — el trigger handle_new_auth_user() los crea.
        """
        
    def sign_in(self, email: str, password: str) -> RemoteAuthResult:
        """POST /auth/v1/token?grant_type=password
        
        Se intenta directamente sin verificar alcanzabilidad previamente.
        Clasifica respuesta/excepción según tabla de errores.
        """
```

**Clasificación de errores del Auth adapter:**

| Condición | `error_type` | Comportamiento en login |
|---|---|---|
| 200 OK | — | Éxito, extraer UUID, email, user_metadata.full_name y JWT |
| Timeout/DNS/ConnectionRefused/NetworkUnreachable | `CONNECTIVITY` | Fallback local habilitado |
| HTTP 500/502/503 | `REMOTE_UNAVAILABLE` | Fallback local habilitado |
| Respuesta Auth con error explícito de credenciales (validar body) | `INVALID_CREDENTIALS` | Rechazo INMEDIATO, sin fallback |
| HTTP 422 (email already registered) | `EMAIL_EXISTS` | Informar al usuario |
| HTTP 429 | `RATE_LIMITED` | Rechazo sin fallback |
| HTTP 403 en contexto Auth | `AUTH_FORBIDDEN` | Rechazo sin fallback |
| Otro error | `UNKNOWN` | Rechazo sin fallback |

**Nota:** La clasificación NO se basa solo en status code. Se inspecciona el body/código de error de Supabase Auth cuando está disponible. HTTP 400/401 no es automáticamente INVALID_CREDENTIALS — se valida el mensaje de error.

**Nota:** NO existe método `create_profile()`. Los perfiles son creados por el trigger remoto.

---

### 4. `HybridAuthService` — Orquestación de autenticación

**Ubicación:** `src/application/services/hybrid_auth_service.py`

```python
@dataclass
class LoginResult:
    success: bool
    user: Optional[User] = None
    auth_method: str = "local"          # "remote" | "local" | "none"
    error_message: Optional[str] = None
    requires_internet: bool = False

class HybridAuthService:
    """Orquesta autenticación híbrida.
    
    Depende SOLO de puertos abstractos (RemoteAuthPort).
    NO importa httpx, SupabaseAuthAdapter, ni clases de infraestructura.
    """
    
    def __init__(
        self,
        auth_service: AuthService,              # existente
        user_repo: UserRepository,
        remote_auth: Optional[RemoteAuthPort],  # None = offline-only
    ):
        ...
    
    def login(self, email: str, password: str) -> LoginResult:
        """Flujo de login híbrido SIN health check previo.
        
        1. Si remote_auth es None → login local directo.
        2. Si remote_auth disponible:
           a. Intentar sign_in DIRECTAMENTE (no verificar alcanzabilidad).
           b. Clasificar resultado:
              - Éxito → resolver usuario local (ver _resolve_local_user) → crear sesión. JWT descartado.
              - INVALID_CREDENTIALS → RECHAZAR (NO fallback).
              - RATE_LIMITED → RECHAZAR (NO fallback).
              - AUTH_FORBIDDEN → RECHAZAR (NO fallback).
              - CONNECTIVITY / REMOTE_UNAVAILABLE → fallback local.
           c. Fallback local:
              - Si usuario tiene hash local → verificar PBKDF2.
              - Si no tiene hash → rechazar: "Requiere Internet".
        """
    
    def _resolve_local_user(self, email: str, password: str, auth_result: RemoteAuthResult) -> LoginResult:
        """Tras sign_in remoto exitoso, resolver/crear usuario local.
        
        Casos:
        A. User local no existe → CREAR nuevo User (full_name de metadata, hash, remote_user_id, role=operator).
           - full_name: usar auth_result.full_name si es string no vacío; de lo contrario usar email como display name temporal.
        B. User local existe, remote_user_id is None → ASOCIAR UUID remoto al usuario local.
        C. User local existe, remote_user_id == auth_result.user_id → login normal, actualizar hash.
        D. User local existe, remote_user_id != auth_result.user_id → IDENTITY_CONFLICT → rechazar.
        """
    
    def register(self, email: str, password: str, full_name: str) -> LoginResult:
        """Registro vía Supabase Auth + caché local.
        
        1. Requiere remote_auth disponible.
        2. sign_up(email, password, full_name) — NO envía role.
        3. Trigger remoto crea profile automáticamente.
        4. Crear/actualizar User local con hash + remote_user_id.
        5. Crear sesión local.
        """
    
    def _update_local_cache(self, user: User, password: str, remote_user_id: str) -> User:
        """Actualiza hash PBKDF2 local y remote_user_id tras auth remota exitosa."""
```

**Diagrama de flujo — Login híbrido (sin health check previo):**

```
Operario → email + password
  │
  ├─ remote_auth es None? → login local directo
  │
  └─ remote_auth disponible → sign_in DIRECTO
       │
       ├─ Éxito → _resolve_local_user:
       │     ├─ No existe User local → CREAR (full_name metadata, hash, remote_user_id)
       │     ├─ Existe, remote_user_id is None → ASOCIAR UUID → actualizar hash
       │     ├─ Existe, remote_user_id == UUID → actualizar hash → sesión
       │     └─ Existe, remote_user_id ≠ UUID → IDENTITY_CONFLICT → rechazar
       │
       ├─ INVALID_CREDENTIALS → RECHAZAR (no fallback)
       ├─ RATE_LIMITED → RECHAZAR (no fallback)
       ├─ AUTH_FORBIDDEN → RECHAZAR (no fallback)
       │
       └─ CONNECTIVITY / REMOTE_UNAVAILABLE → fallback local
            │
            ├─ Usuario tiene hash → verify_password → sesión / rechazo
            └─ No tiene hash → "Requiere Internet para primer login"
```

**Diagrama de flujo — Registro (trigger crea profile):**

```
Operario → nombre + email + password
  │
  ├─ remote_auth es None? → "Registro no disponible sin conexión remota"
  │
  └─ remote_auth disponible → sign_up(email, password, metadata={"full_name"})
       │
       ├─ Éxito → trigger crea profile → crear User local (hash + remote_user_id) → sesión
       ├─ EMAIL_EXISTS → "El email ya está registrado"
       ├─ CONNECTIVITY → "Se requiere conexión a Internet"
       └─ Otro error → "No se pudo crear la cuenta"
```

---

### 5. `SupabaseDataAdapter` — Implementa RemoteDataPort

**Ubicación:** `src/infrastructure/supabase/supabase_data_adapter.py`

```python
class SupabaseDataAdapter:
    """Implementa RemoteDataPort usando PostgREST vía httpx.
    
    Envía UUID pre-generado como campo 'id' explícito en el payload.
    Usa Prefer: resolution=merge-duplicates para upsert idempotente.
    """
    
    def __init__(self, config: SupabaseConfig):
        ...
    
    def upsert(self, access_token: str, table: str, data: dict) -> RemoteUpsertResult:
        """POST /rest/v1/{table} con:
        - Header: Prefer: resolution=merge-duplicates
        - Header: Authorization: Bearer {access_token}
        - Header: apikey: {publishable_key}
        - Body: data (incluye 'id' = UUID pre-generado)
        - on_conflict: id
        
        El UUID pre-generado ya viene en data['id'].
        Retries envían el mismo UUID → no crea duplicados.
        """
```

**Mapeo local → remoto:**

| Tabla remota | Campo FK remoto | Fuente local → Valor remoto |
|---|---|---|
| modules | greenhouse_id | greenhouse.remote_id (UUID) |
| monitorings | module_id | module.remote_id (UUID) |
| monitorings | created_by_user_id | User.remote_user_id (UUID) — nullable si local es NULL |
| monitoring_metrics | monitoring_id | monitoring.remote_id (UUID) |
| snapshots | monitoring_id | monitoring.remote_id (UUID) |
| inspection_results | snapshot_id | snapshot.remote_id (UUID) |
| activity_logs | module_id | module.remote_id (UUID) |
| activity_logs | user_id | User.remote_user_id (UUID) |
| activity_logs | activity_type_code | ActivityType.code (identificador natural estable) |

---

### 6. `SupabaseStorageAdapter` — Implementa RemoteStoragePort

**Ubicación:** `src/infrastructure/supabase/supabase_storage_adapter.py`

```python
class SupabaseStorageAdapter:
    """Implementa RemoteStoragePort usando Supabase Storage REST API."""
    
    def __init__(self, config: SupabaseConfig):
        ...
    
    def upload_file(
        self,
        access_token: str,
        local_file_path: str,
        remote_path: str,
    ) -> RemoteUploadResult:
        """POST /storage/v1/object/{bucket}/{remote_path}
        
        - Content-Type: image/jpeg
        - x-upsert: true (sobrescribir si existe → idempotencia)
        - Authorization: Bearer {access_token}
        - Timeout: 60s
        
        Si archivo local no existe → retorna error sin interrumpir.
        """
    
    @staticmethod
    def build_snapshot_path(remote_monitoring_uuid: str, frame_index: int, snapshot_type: str) -> str:
        """Construye ruta determinística en bucket.
        
        Retorna: monitorings/{remote_monitoring_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg
        """
        return f"monitorings/{remote_monitoring_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg"
```

---

### 7. `SyncStateRepository` — Implementa SyncStatePort

**Ubicación:** `src/infrastructure/persistence/sync_state_repository.py`

```python
class SyncStateRepository:
    """Implementa SyncStatePort usando SQLAlchemy sobre columnas de sync en tablas existentes.
    
    Lee/escribe las columnas remote_id, remote_sync_status, last_synced_at,
    remote_sync_error, raw_storage_path, annotated_storage_path de los modelos ORM.
    """
    
    def __init__(self, session_factory):
        ...
    
    def get_pending_entities(self, entity_type: str) -> list[dict]:
        """Query entidades donde remote_sync_status != 'synced'.
        Retorna dicts con id, remote_id, y campos necesarios para mapeo.
        """
    
    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        """Lee remote_id de la entidad."""
    
    def reserve_remote_id(self, entity_type: str, local_id: int, uuid: str) -> None:
        """SET remote_id = uuid, remote_sync_status = 'pending'."""
    
    def mark_syncing(self, entity_type: str, local_id: int) -> None:
        """SET remote_sync_status = 'syncing'."""
    
    def mark_synced(self, entity_type: str, local_id: int, remote_id: str) -> None:
        """SET remote_sync_status = 'synced', last_synced_at = utcnow()."""
    
    def mark_error(self, entity_type: str, local_id: int, error_msg: str) -> None:
        """SET remote_sync_status = 'error', remote_sync_error = error_msg."""
    
    def get_storage_paths(self, snapshot_id: int) -> StoragePaths:
        """Lee raw_storage_path y annotated_storage_path del snapshot."""
    
    def set_storage_paths(self, snapshot_id: int, raw_path: Optional[str], annotated_path: Optional[str]) -> None:
        """SET raw_storage_path, annotated_storage_path."""
```

---

### 8. `SyncRuntimeState` — Estado en memoria para progreso

**Ubicación:** `src/application/services/sync_runtime_state.py`

```python
import threading
from dataclasses import dataclass, field

@dataclass
class SyncRuntimeState:
    """Estado en memoria del proceso de sync actual. Thread-safe.
    
    Previene sync concurrente y provee progreso real a la UI.
    """
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    is_syncing: bool = False
    phase: str = ""
    processed: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    last_result: Optional[dict] = None
    
    def try_acquire(self) -> bool:
        """Intenta iniciar sync. Retorna False si ya hay uno en curso."""
        with self._lock:
            if self.is_syncing:
                return False
            self.is_syncing = True
            self.processed = 0
            self.total = 0
            self.errors = []
            return True
    
    def release(self, result: Optional[dict] = None) -> None:
        """Libera el sync y almacena resultado.
        
        Acepta result=None para el caso donde la excepción ocurrió
        antes de producir un SyncResult (e.g., fallo durante sign_in).
        """
        with self._lock:
            self.is_syncing = False
            self.last_result = result
    
    def update_progress(self, phase: str, processed: int, total: int) -> None:
        """Actualiza progreso thread-safe."""
        with self._lock:
            self.phase = phase
            self.processed = processed
            self.total = total
    
    def get_status(self) -> dict:
        """Lee estado actual (thread-safe)."""
        with self._lock:
            return {
                "is_syncing": self.is_syncing,
                "phase": self.phase,
                "processed": self.processed,
                "total": self.total,
                "errors": list(self.errors),
                "last_result": self.last_result,
            }
```

---

### 9. `RemoteSyncService` — Orquestación de sincronización

**Ubicación:** `src/application/services/remote_sync_service.py`

```python
@dataclass
class SyncResult:
    success: bool
    entities_synced: int
    entities_failed: int
    images_uploaded: int
    images_failed: int
    errors: list[str]
    duration_seconds: float

class RemoteSyncService:
    """Orquesta la sincronización manual unidireccional SQLite → Supabase.
    
    Depende SOLO de puertos abstractos. NO importa httpx ni adaptadores concretos.
    Respeta orden de dependencias padre-hijo.
    Usa UUID pre-generados como PK explícito en upserts.
    
    NOTA sobre SyncRuntimeState ownership:
    RemoteSyncService NO es dueño del lifecycle de SyncRuntimeState.
    - NO llama try_acquire()
    - NO llama release()
    - SÍ llama update_progress() para reportar progreso real
    La ruta sync_api es la única dueña del try_acquire/release (en try/finally).
    """
    
    def __init__(
        self,
        remote_data: RemoteDataPort,
        remote_storage: RemoteStoragePort,
        sync_state: SyncStatePort,
        runtime_state: SyncRuntimeState,  # Solo para update_progress(), NO para acquire/release
    ):
        ...
    
    def execute_sync(self, access_token: str, user_remote_id: str) -> SyncResult:
        """Ejecuta sincronización completa con orden de dependencia.
        
        Precondiciones (verificadas por la ruta ANTES de llamar):
        - No hay monitoreo activo (running/analyzing)
        - Sync adquirido (runtime_state.try_acquire() exitoso — hecho por la ruta)
        - user tiene remote_user_id
        - access_token es JWT efímero válido
        
        Este método NO adquiere ni libera SyncRuntimeState.
        Si lanza excepción, la ruta se encarga de release() en finally.
        
        Flujo:
        1. Verificar identidad: user_remote_id existe (perfil fue creado por trigger)
        2. Sync greenhouses (reservar UUID → upsert → mark_synced)
        3. Sync modules (verificar greenhouse padre synced → reservar UUID → upsert)
        4. Sync monitorings (verificar module padre synced → reservar UUID → upsert, created_by_user_id = user.remote_user_id)
        5. Sync monitoring_metrics (verificar monitoring padre synced)
        6. Sync snapshots + Storage (ver flujo detallado abajo)
        7. Sync inspection_results (verificar snapshot padre synced)
        8. Sync activity_logs (verificar module padre synced + user.remote_user_id + activity_type mapped por code → activity_type_code)
        
        Por cada entidad:
        a. get_remote_id() → si None → uuid4() → reserve_remote_id()
        b. mark_syncing()
        c. Build payload con id=remote_id, FKs=parent.remote_id
        d. remote_data.upsert(token, table, payload)
        e. Si éxito → mark_synced() / Si fallo → mark_error()
        f. runtime_state.update_progress(phase, processed, total)
        g. Continuar con siguiente entidad
        """
    
    def _sync_snapshot_with_storage(self, access_token: str, snapshot: dict) -> tuple[bool, int]:
        """Flujo completo por snapshot (corrección 3.9):
        
        1. Verificar monitoring padre tiene remote_id synced
        2. Obtener/reservar remote_id estable del snapshot
        3. Leer archivo local (image_path)
        4. Si no existe → mark_error, return (False, 0)
        5. Upload raw a ruta determinística
        6. Si anotado existe → upload anotado
        7. Upsert snapshot metadata en PostgREST (id=UUID, monitoring_id=parent_UUID, paths)
        8. Solo si TODO exitoso → mark_synced + set_storage_paths
        9. Si Storage OK pero PostgREST falla → NO eliminar uploads, mark_error
        
        Retorna: (synced: bool, images_uploaded: int)
        """
```

**Flujo de UUID pre-generado (corrección 3.7, 3.8):**

```
Entidad sin remote_id
  │
  ├─ Generar UUID v4 localmente
  ├─ reserve_remote_id(entity_type, local_id, uuid) → persiste en SQLite con status="pending"
  │
  ├─ Build payload: {"id": uuid, "name": ..., "parent_id": parent_uuid, ...}
  ├─ remote_data.upsert(token, table, payload)
  │     Header: Prefer: resolution=merge-duplicates
  │     on_conflict: id
  │
  ├─ Si éxito → mark_synced(entity_type, local_id, uuid)
  │
  └─ Si fallo → mark_error(...) — remote_id CONSERVADO para reintento
       Próximo reintento → get_remote_id() retorna MISMO uuid → reusar
```

**Propiedad de crash-recovery:** Si la app crashea entre el upsert remoto exitoso y el mark_synced local, al reintentar se enviará el mismo UUID → upsert actualiza (no duplica) → mark_synced se completa.

---

### 10. API — Nuevas rutas FastAPI

**Ubicación:** `app/routes/auth.py` (extender) + `app/routes/sync_api.py` (nuevo)

#### Rutas de autenticación

| Método | Ruta | Descripción | Auth requerida |
|---|---|---|---|
| GET | `/registro` | Formulario de registro (HTML) | No |
| POST | `/registro` | Procesar registro | No |
| POST | `/login` | Login híbrido (modificar existente) | No |

#### Rutas de sincronización

| Método | Ruta | Descripción | Auth requerida |
|---|---|---|---|
| POST | `/api/sync/trigger` | Iniciar sincronización manual | Sí (API) |
| GET | `/api/sync/status` | Estado actual de sync | Sí (API) |

**POST /api/sync/trigger:**

La ruta sync_api es el ÚNICO dueño del lifecycle de SyncRuntimeState:

```python
# Pseudocódigo conceptual del endpoint
if not runtime_state.try_acquire():
    return HTTP 409 "Sincronización en curso"

try:
    # 1. Verificar precondiciones
    verificar_usuario_tiene_remote_user_id()  # → 400
    verificar_no_monitoreo_activo()           # → 409
    
    # 2. Obtener JWT efímero
    auth_result = remote_auth.sign_in(email, password)  # puede fallar
    
    # 3. Ejecutar sync (en threadpool para no bloquear event loop)
    result = await run_in_threadpool(sync_service.execute_sync, token, user_remote_id)
    
    # 4. Descartar JWT (sale de scope)
    return result
finally:
    runtime_state.release(result if 'result' in locals() else None)
```

Precondiciones verificadas:
1. Usuario autenticado con remote_user_id → 400 si no
2. No hay monitoreo activo (consultar MonitoringRuntimeRegistry) → 409 si activo
3. No hay sync en curso (try_acquire()) → 409 si en curso
4. Password en request body → sign_in → JWT efímero
5. Ejecutar sync en threadpool
6. finally: release(result) — SIEMPRE se ejecuta, incluso si sign_in o sync fallan

```json
// Request body
{"password": "contraseña_del_operario"}

// Response 200
{
  "success": true,
  "entities_synced": 45,
  "entities_failed": 2,
  "images_uploaded": 38,
  "images_failed": 0,
  "errors": ["Module 'Módulo 3': timeout al sincronizar"],
  "duration_seconds": 12.4
}

// Response 409 (monitoreo activo)
{"detail": "Finaliza el monitoreo en curso antes de sincronizar"}

// Response 409 (sync en curso)
{"detail": "Sincronización en curso, espera a que finalice"}
```

**GET /api/sync/status:**
```json
{
  "supabase_configured": true,
  "user_has_remote_id": true,
  "is_syncing": false,
  "pending_count": 12,
  "synced_count": 45,
  "error_count": 2,
  "last_sync_at": "2025-01-15T10:30:00Z",
  "current_progress": null
}
```

---

### 11. Migración integrada en DatabaseManager (corrección 3.6)

**Ubicación:** `src/infrastructure/persistence/database.py`

```python
def _migrate_add_sync_columns(engine) -> None:
    """Agrega columnas de sincronización remota a tablas existentes.
    
    Sigue el mismo patrón de _migrate_add_columns():
    - PRAGMA table_info para detectar columnas existentes
    - ALTER TABLE ADD COLUMN para cada columna ausente
    - Idempotente: ejecutar múltiples veces no produce errores
    
    Tablas afectadas:
    - greenhouses: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - modules: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - monitorings: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - snapshots: remote_id, remote_sync_status, last_synced_at, remote_sync_error, raw_storage_path, annotated_storage_path
    - monitoring_metrics: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - inspection_results: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - activity_logs: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    """
    # Estructura: {table_name: [(column_name, column_type_sql), ...]}
    sync_columns = {
        "greenhouses": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
        "modules": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
        "monitorings": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
        "snapshots": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
            ("raw_storage_path", "VARCHAR(500)"),
            ("annotated_storage_path", "VARCHAR(500)"),
        ],
        "monitoring_metrics": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
        "inspection_results": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
        "activity_logs": [
            ("remote_id", "VARCHAR(36)"),
            ("remote_sync_status", "VARCHAR(20) DEFAULT 'pending'"),
            ("last_synced_at", "DATETIME"),
            ("remote_sync_error", "TEXT"),
        ],
    }
    
    with engine.connect() as conn:
        for table, columns in sync_columns.items():
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            existing_cols = {row[1] for row in result.fetchall()}
            
            for col_name, col_type in columns:
                if col_name not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
        
        conn.commit()
```

Invocado desde `DatabaseManager.init_db()`:
```python
def init_db(self) -> None:
    Base.metadata.create_all(self._engine, checkfirst=True)
    _migrate_dimensions_nullable(self._engine)
    _migrate_add_columns(self._engine)
    _migrate_add_sync_columns(self._engine)  # Spec 017
    session = self._session_factory()
    try:
        seed_activity_types(session)
    finally:
        session.close()
```

---

## Data Models

### Cambios al esquema SQLite existente

Las entidades sincronizables necesitan metadatos de sync. Se agregan columnas a tablas existentes:

#### Tabla `greenhouses` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado como identidad remota estable |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso (UTC) |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

#### Tabla `modules` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

#### Tabla `monitorings` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

**Nota:** El campo existente `sync_status` (pending/exported/synced/error) se mantiene para la exportación ZIP local. `remote_sync_status` es independiente y se refiere exclusivamente a la sincronización con Supabase.

#### Tabla `snapshots` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |
| `raw_storage_path` | VARCHAR(500) | NULL | Ruta del objeto raw en Storage |
| `annotated_storage_path` | VARCHAR(500) | NULL | Ruta del objeto anotado en Storage |

#### Tabla `monitoring_metrics` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

#### Tabla `inspection_results` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

#### Tabla `activity_logs` — Columnas nuevas

| Columna | Tipo | Default | Descripción |
|---|---|---|---|
| `remote_id` | VARCHAR(36) | NULL | UUID v4 pre-generado |
| `remote_sync_status` | VARCHAR(20) | "pending" | pending, syncing, synced, error |
| `last_synced_at` | DATETIME | NULL | Último sync exitoso |
| `remote_sync_error` | TEXT | NULL | Descripción del último error |

**Nota:** El campo existente `sync_status` en activity_logs se mantiene para semántica local/export.

#### Tabla `users` — Sin columnas nuevas

El modelo `UserModel` ya tiene `remote_user_id` y `sync_status`. Se reutilizan sin cambios.

### Estrategia de migración

- Todas las columnas nuevas son **nullable** o tienen **defaults** → compatible con registros históricos.
- `_migrate_add_sync_columns(engine)` se integra en `DatabaseManager.init_db()` después de las migraciones existentes.
- Usa PRAGMA table_info + ALTER TABLE (mismo patrón que `_migrate_add_columns()`).
- Script auxiliar opcional `scripts/migrate_017.py` reutiliza la misma lógica.
- Tests: DB antigua → migración → segunda ejecución idempotente → integrity_check → foreign_key_check.

### Tablas remotas (prerequisito cloud de esta spec)

Las siguientes tablas deben crearse en Supabase PostgreSQL como parte de esta spec, antes de habilitar sync de actividades:

- `activity_types` — catálogo idempotente (seed con mismo contenido que `INITIAL_ACTIVITY_TYPES`). Se usa `code` como identificador natural estable para mapeo local ↔ remoto.
- `activity_logs` — registros de actividades con FKs a modules (UUID), users (UUID), y activity_types (referenciado por `code`).

La creación de estas tablas se realiza como paso cloud separado ANTES de implementar RemoteSyncService/activity sync. No es una fase futura indefinida.

---

## Error Handling

### Clasificación precisa de errores por contexto

**Auth adapter errors:**

```python
class AuthErrorType(str, Enum):
    CONNECTIVITY = "connectivity"               # Timeout, DNS, ConnectionRefused, NetworkUnreachable
    REMOTE_UNAVAILABLE = "remote_unavailable"   # HTTP 500, 502, 503
    INVALID_CREDENTIALS = "invalid_credentials" # Auth rechaza password (validar body, no solo status)
    EMAIL_EXISTS = "email_exists"               # Signup con email existente (422)
    RATE_LIMITED = "rate_limited"               # HTTP 429
    AUTH_FORBIDDEN = "auth_forbidden"           # HTTP 403 en contexto Auth
    UNKNOWN = "unknown"                         # No clasificado
```

**Data/Storage adapter errors:**

```python
class DataErrorType(str, Enum):
    CONNECTIVITY = "connectivity"               # Timeout, DNS, ConnectionRefused
    REMOTE_UNAVAILABLE = "remote_unavailable"   # HTTP 500, 502, 503
    RLS_DENIED = "rls_denied"                   # HTTP 403 de PostgREST/Storage (Row Level Security)
    STORAGE_ERROR = "storage_error"             # Fallos específicos de Storage
    UNKNOWN = "unknown"                         # No clasificado
```

**Nota:** La clasificación no se basa solo en HTTP status code. Se inspecciona el body/código de error de Supabase cuando está disponible. HTTP 400/401 no es automáticamente INVALID_CREDENTIALS — se valida el mensaje de error devuelto por Auth.

### Tabla de decisión para login (corrección 3.11)

| Escenario | Acción | Fallback local? |
|---|---|---|
| remote_auth es None (Supabase no configurado) | Login local directo | N/A (es el único) |
| sign_in éxito | Login remoto + actualizar cache | No necesario |
| sign_in → INVALID_CREDENTIALS | **RECHAZAR INMEDIATAMENTE** | **NO** |
| sign_in → RATE_LIMITED (429) | **RECHAZAR** con mensaje apropiado | **NO** |
| sign_in → AUTH_FORBIDDEN (403) | **RECHAZAR** | **NO** |
| sign_in → CONNECTIVITY (timeout/DNS/refused) | Fallback local | Sí |
| sign_in → REMOTE_UNAVAILABLE (500/502/503) | Fallback local | Sí |
| Fallback local + sin hash local | Rechazar: "Requiere Internet" | — |

### Garantías de integridad durante sync

1. **Atomicidad por entidad:** Cada entidad se sincroniza individualmente. Un fallo en una no afecta a las demás.
2. **No corrupción:** `remote_id` se reserva ANTES del envío. Si falla, el UUID se conserva para reintento.
3. **Preservación de archivos:** NUNCA se eliminan, mueven ni renombran archivos locales.
4. **Preservación de PKs:** NUNCA se modifican IDs enteros locales.
5. **Crash-recovery:** Si crash entre upsert remoto exitoso y mark_synced local, el reintento usa mismo UUID → upsert (no duplica) → mark_synced se completa.
6. **Storage idempotente:** Si Storage upload OK pero PostgREST falla, no se eliminan objetos ya subidos. Reintento reutiliza mismas rutas.
7. **SyncRuntimeState siempre se libera:** `try_acquire()` siempre se complementa con `release()` en un bloque finally. Si ocurre excepción durante auth o sync, el lock se libera. Al reiniciar la app, `SyncRuntimeState` comienza en estado `idle` (es solo memoria).
8. **Reconciliación de "syncing" stale:** Una entidad con `remote_sync_status = "syncing"` persistido (crash anterior) se considera retryable. El estado activo real lo determina `SyncRuntimeState` en memoria. Al iniciar nueva sync: pending, error y syncing stale se procesan reutilizando remote_id reservado.

### Mensajes de error para el operario

| Categoría (RemoteErrorType) | Mensaje UI |
|---|---|
| CONNECTIVITY | "Sin conexión a Internet. Intenta más tarde." |
| REMOTE_UNAVAILABLE | "El servidor no está disponible temporalmente." |
| INVALID_CREDENTIALS | "Credenciales incorrectas. Verifica tu email y contraseña." |
| RATE_LIMITED | "Demasiados intentos. Espera unos minutos." |
| RLS_DENIED | "Acceso denegado. Contacta al administrador." |
| STORAGE_ERROR | "No se pudieron subir algunas imágenes. Puedes reintentar." |
| UNKNOWN | "Error inesperado. Se preservó tu información local." |

### Dirty tracking para entidades modificadas post-sync

Una entidad que ya tiene `remote_id` reservado y `remote_sync_status = "synced"` puede ser modificada localmente (e.g., editar nombre de greenhouse, actualizar notas de módulo).

**Regla:** Cuando una entidad sincronizable cambia localmente:
- Preservar `remote_id` (NUNCA regenerar UUID).
- Establecer `remote_sync_status = "pending"`.
- Limpiar `remote_sync_error`.

En la próxima sincronización: pending + remote_id existente → upsert con MISMO UUID → synced.

**Entidades editables que requieren dirty tracking:**
- Greenhouse (name, location)
- Module (name, crop_type, dimensions, monitoring_frequency_days)
- ActivityLog (si existe flujo de edición)

**Entidades NO editables post-creación (no requieren dirty tracking):**
- Monitoring (datos de sesión inmutables una vez completada)
- Snapshot (captura inmutable)
- InspectionResult (resultado inmutable)
- MonitoringMetrics (computados una vez al completar)

**Implementación:** La lógica de dirty tracking se integra en los puntos de persistencia local existentes (repositories CRUD). NO se usan SQLAlchemy events globales que podrían accidentalmente revertir un `mark_synced` a `pending` inmediatamente después de sincronizar.

### Semántica de DELETE para MVP

Spec 017 NO implementa propagación de eliminaciones locales hacia Supabase:
- No hay tombstones.
- No hay delete queue.
- No hay DELETE automático SQLite → Supabase.

**Semántica definida:**
- SQLite es autoridad operacional local.
- Eliminar localmente una entidad NO implica eliminar su copia previamente sincronizada en Supabase.
- Supabase es un respaldo/réplica de datos sincronizados, no un mirror exacto del estado local.
- El upsert/sync aplica a registros existentes y nuevos (CREATE/UPDATE), no a eliminaciones.
- "last-write-wins local" aplica a registros sincronizables mediante upsert, NO a eliminaciones físicas.

**Limitación conocida:** Supabase puede retener registros que ya fueron eliminados localmente. La sincronización de DELETE requerirá una estrategia de tombstones/delete queue en una iteración posterior.

---

## Testing Strategy

### Tests unitarios (sin Internet, sin Supabase)

| Componente | Qué se testea | Mock/Fake |
|---|---|---|
| `SupabaseConfig` | Lectura de env vars, validación, rechazo de service_role | Variables de entorno simuladas |
| `HybridAuthService` | Login sin health check, registro sin create_profile, fallback, rechazo | Fake RemoteAuthPort |
| `RemoteSyncService` | UUID pre-generado, orden de sync, idempotencia, errores, bloqueo | Fake RemoteDataPort, RemoteStoragePort, SyncStatePort |
| `SupabaseAuthAdapter` | Clasificación de errores, parsing respuestas | `httpx.MockTransport` |
| `SupabaseDataAdapter` | Payload con UUID explícito, header merge-duplicates | `httpx.MockTransport` |
| `SupabaseStorageAdapter` | Rutas determinísticas, x-upsert, error handling | `httpx.MockTransport` |
| `SyncStateRepository` | Reservación UUID, mark_synced, mark_error, idempotencia | In-memory SQLite |
| `SyncRuntimeState` | Concurrency lock, progreso thread-safe | Directo (no requiere mock) |
| `_migrate_add_sync_columns` | Migración idempotente, integrity_check, foreign_key_check | In-memory SQLite |

### Tests obligatorios (NO opcionales)

Los siguientes tests son críticos y NO deben marcarse como opcionales:

1. **INVALID_CREDENTIALS no dispara fallback:** Supabase rechaza credenciales → login falla sin intentar hash local.
2. **Primer login remoto crea usuario local:** sign_in exitoso + no existe User local → se crea User con hash.
3. **Conflicto de identidad rechazado:** User local con remote_user_id ≠ UUID autenticado → rechazo.
4. **Migración idempotente:** DB antigua + migración + segunda ejecución + integrity_check + foreign_key_check.
5. **Dependencia padre-hijo:** Hijo con padre sin remote_id synced → no se sincroniza.
6. **Rutas Storage determinísticas:** Misma entrada → misma ruta siempre.
7. **Integridad local ante errores:** Error de red → remote_id reservado no se pierde, image_path no se modifica.
8. **Inicio sin Supabase:** App inicia sin variables → funciona igual que antes.
9. **Bloqueo sync con monitoreo activo:** POST /api/sync/trigger con monitoreo running → 409.
10. **Crash-recovery UUID:** Entidad con remote_id reservado + upsert exitoso previo → reintento usa mismo UUID.
11. **SyncRuntimeState se libera ante excepción:** Excepción durante sync → is_syncing = False.
12. **Reconciliación syncing stale:** Entidad con remote_sync_status="syncing" persistido → retryable en siguiente sync.
13. **Concurrency guard:** Segundo POST /api/sync/trigger mientras sync activo → 409.
14. **Storage failure handling:** Raw file no existe → mark_error, no claim synced.

### Fakes para tests de aplicación

```python
class FakeRemoteAuth:
    """Implementa RemoteAuthPort para tests de HybridAuthService."""
    
    def __init__(self, behavior: str = "success"):
        self.behavior = behavior
    
    def sign_in(self, email, password) -> RemoteAuthResult:
        if self.behavior == "success":
            return RemoteAuthResult(success=True, user_id="uuid-123", access_token="ephemeral-jwt")
        elif self.behavior == "invalid_credentials":
            return RemoteAuthResult(success=False, error_type="invalid_credentials")
        elif self.behavior == "connectivity":
            return RemoteAuthResult(success=False, error_type="connectivity")
        elif self.behavior == "rate_limited":
            return RemoteAuthResult(success=False, error_type="rate_limited")
        ...
    
    def sign_up(self, email, password, full_name) -> RemoteAuthResult:
        # NO tiene create_profile — trigger lo hace
        ...
```

### Tests de integración (separados, requieren Internet)

- Marcados con `@pytest.mark.supabase`.
- NO ejecutados en suite principal (`python -m pytest`).
- Requieren variables de entorno reales de Supabase.
- Validan end-to-end: registro → login → sync → Storage.

---

## Correctness Properties

### Property 1: Login remoto inválido nunca dispara fallback local

*Para cualquier* combinación de email y password donde el sign_in directo a Supabase responde con `INVALID_CREDENTIALS`, el sistema SIEMPRE rechaza el login sin intentar verificación contra el hash local, independientemente de si el usuario tiene hash local almacenado. Lo mismo aplica para `RATE_LIMITED` y `RLS_DENIED`.

**Validates: Requirements 4.3, 4.7, 17.2**

### Property 2: Rutas de Storage determinísticas

*Para cualquier* UUID de monitoreo válido, frame_index entero no-negativo, y tipo de snapshot (raw o annotated), la función `build_snapshot_path` SIEMPRE produce la misma cadena de ruta, y dicha ruta cumple el formato `monitorings/{uuid}/{type}/snapshot_{frame_index:06d}.jpg`.

**Validates: Requirements 14.2, 14.3, 15.2**

### Property 3: Idempotencia de sincronización via UUID pre-generado

*Para cualquier* entidad local con `remote_id` ya reservado, ejecutar la sincronización envía el MISMO UUID como campo `id` en el payload, produciendo un upsert (no inserción nueva). El `remote_id` NUNCA se modifica ni se regenera durante reintentos.

**Validates: Requirements 8.3, 10.4, 10.6, 15.1, 15.4, 15.5**

### Property 4: Preservación de integridad local ante errores de red

*Para cualquier* error de red o Supabase durante sincronización, el sistema NUNCA modifica: (a) un `remote_id` previamente reservado, (b) archivos locales de snapshots, (c) el `image_path` de un snapshot, ni (d) datos de entidades que no estaban siendo sincronizadas en ese momento.

**Validates: Requirements 13.1, 13.2, 13.4, 17.3, 17.4**

### Property 5: Orden de dependencia padre-hijo en sincronización

*Para cualquier* entidad hija que se sincroniza exitosamente, su entidad padre SIEMPRE tiene un `remote_id` con `remote_sync_status = "synced"` al momento de la sincronización del hijo. Una entidad hija con padre sin `remote_id` synced NUNCA alcanza `remote_sync_status = "synced"`.

**Validates: Requirements 12.1, 12.2, 12.3, 12.5**

### Property 6: Clasificación correcta de errores

*Para cualquier* respuesta o excepción del sign_in: (a) timeout/DNS/ConnectionRefused/NetworkUnreachable → CONNECTIVITY (fallback habilitado), (b) HTTP 500/502/503 → REMOTE_UNAVAILABLE (fallback habilitado), (c) HTTP 400/401 con credenciales inválidas → INVALID_CREDENTIALS (NO fallback), (d) HTTP 429 → RATE_LIMITED (NO fallback). Nunca un error 4xx explícito es clasificado como CONNECTIVITY.

**Validates: Requirements 4.3, 4.7, 4.8, 17.1, 17.2**

### Property 7: Registro nunca crea usuario local sin confirmación remota

*Para cualquier* intento de registro, un usuario local con `remote_user_id` asignado SOLO existe si Supabase Auth respondió exitosamente con un UUID. Si Supabase falla o es inalcanzable, NO se crea usuario local nuevo. El role NO se envía durante signup (el trigger asigna "operator").

**Validates: Requirements 2.1, 2.3, 2.5, 2.8**

### Property 8: Aplicación inicia sin Supabase configurado

*Para cualquier* combinación donde `SUPABASE_URL` o `SUPABASE_PUBLISHABLE_KEY` están vacías o ausentes, la aplicación inicia exitosamente en modo offline-only sin errores bloqueantes, y todas las funcionalidades locales permanecen disponibles.

**Validates: Requirements 1.2, 7.1, 7.5**

### Property 9: Hash local se actualiza tras cada login remoto exitoso

*Para cualquier* login remoto exitoso, el `password_hash` local del usuario se actualiza con PBKDF2-SHA256 de la contraseña proporcionada, de modo que un login offline posterior con la misma contraseña SIEMPRE tiene éxito. El JWT obtenido se descarta inmediatamente tras el login.

**Validates: Requirements 4.2, 5.1, 5.2, 18.3**

### Property 10: Compatibilidad con datos históricos y migración idempotente

*Para cualquier* base de datos SQLite existente sin columnas de sync (pre-Spec017), ejecutar `_migrate_add_sync_columns()` agrega las columnas necesarias sin error. Ejecutarla una segunda vez es un no-op. Los registros históricos se leen correctamente con defaults. `PRAGMA integrity_check` y `PRAGMA foreign_key_check` pasan después de la migración.

**Validates: Requirements 16.1, 16.3, 16.4, 26.3, 26.5**

### Property 11: Crash-recovery entre upsert remoto y confirmación local

*Para cualquier* entidad cuyo upsert remoto tuvo éxito pero el `mark_synced` local no se completó (crash/restart), al reintentar la sincronización el sistema envía el MISMO UUID pre-reservado, produciendo un upsert idempotente (actualización, no duplicado), y completa el `mark_synced`.

**Validates: Requirements 10.4, 10.6, 15.1**

### Property 12: Sync bloqueado durante monitoreo activo

*Para cualquier* intento de sincronización cuando existe un monitoreo con estado `running` o `analyzing`, el sistema SIEMPRE rechaza con HTTP 409 sin iniciar ninguna operación de sync.

**Validates: Requirements 8.1, 8.2**

### Property 13: Primer login remoto crea caché local

*Para cualquier* sign_in remoto exitoso donde no existe User local con ese email, el sistema SIEMPRE crea un nuevo User local con hash PBKDF2, remote_user_id, y role="operator", de modo que un login offline posterior sea posible.

**Validates: Requirements 3.6, 5.1**

### Property 14: Conflicto de identidad nunca sobrescribe remote_user_id

*Para cualquier* sign_in remoto exitoso donde existe un User local con el mismo email pero `remote_user_id` diferente al UUID autenticado, el sistema SIEMPRE rechaza la asociación sin sobrescribir el `remote_user_id` existente.

**Validates: Requirements 3.8**

### Property 15: SyncRuntimeState siempre se libera tras excepción

*Para cualquier* excepción durante la ejecución de sync (auth fallido, error de red, excepción inesperada), `SyncRuntimeState.is_syncing` SIEMPRE retorna a False, permitiendo que la siguiente solicitud de sync proceda normalmente.

**Validates: Requirements 8.9**

### Property 16: Dirty tracking preserva remote_id

*Para cualquier* entidad con `remote_sync_status = "synced"` que se modifica localmente, el sistema SIEMPRE preserva su `remote_id` existente y actualiza `remote_sync_status` a "pending". En la siguiente sincronización, se envía el MISMO UUID (upsert), nunca uno nuevo.

**Validates: Requirements 10.6, 15.4**

---

## Decisiones de diseño y justificaciones

### D1: httpx sobre supabase-py

**Decisión:** Usar `httpx` como cliente HTTP directo.
**Alternativa descartada:** `supabase-py` (SDK oficial de Supabase para Python).
**Razón:** `supabase-py` agrega ~5 subdependencias (`gotrue`, `storage3`, `postgrest`, `realtime`, `supafunc`). La API REST de Supabase es HTTP estándar. `httpx` es pure Python y compatible con ARM64.
**Nota:** httpx no está en `requirements.txt` actualmente. Debe agregarse como dependencia directa de runtime dentro de esta spec, ANTES de implementar los adapters. `requirements-test.txt` ya la declara (`httpx>=0.27.0`) pero solo para testing.

### D2: Puertos abstractos en capa de aplicación

**Decisión:** Definir `RemoteAuthPort`, `RemoteDataPort`, `RemoteStoragePort`, `SyncStatePort` como Protocol/ABC.
**Razón:** La capa de aplicación no debe importar implementaciones de infraestructura. Permite reemplazar adapters con fakes para testing sin modificar lógica.

### D3: UUID pre-generado (reservación local)

**Decisión:** Generar UUID v4 localmente y persistirlo ANTES del envío remoto.
**Alternativa descartada:** Obtener UUID de Supabase tras inserción exitosa.
**Razón:** Elimina la ventana de crash entre escritura remota y confirmación local. El reintento siempre usa el mismo UUID → no crea duplicados.

### D4: JWT efímero, nunca persistido

**Decisión:** Solicitar password al operario para sync → sign_in → JWT → sync → descartar.
**Alternativa descartada:** Persistir refresh_token para re-autenticación silenciosa.
**Razón:** Minimizar superficie de ataque. No hay JWT en SQLite, cookies, filesystem, ni request.state.

### D5: Sin health check previo al login

**Decisión:** Intentar sign_in directamente y clasificar respuesta/excepción.
**Alternativa descartada:** ConnectivityChecker.is_reachable() antes de sign_in.
**Razón:** Un health check agrega latencia, introduce falsos negativos (health OK pero auth falla), y no aporta seguridad real. La clasificación de la respuesta de sign_in es suficiente.

### D6: Migración en DatabaseManager (no script externo obligatorio)

**Decisión:** `_migrate_add_sync_columns()` invocado desde `init_db()`.
**Alternativa descartada:** Script externo obligatorio (`scripts/migrate_017.py`) como paso manual.
**Razón:** El proyecto ya usa este patrón (ver `_migrate_add_columns()`). El script auxiliar puede existir pero la migración ocurre automáticamente al iniciar la app.

### D7: SyncStatePort separado de repositorios agrícolas

**Decisión:** `SyncStatePort` como abstracción independiente para estado de sync.
**Alternativa descartada:** Agregar métodos de sync a GreenhouseRepository, ModuleRepository, etc.
**Razón:** Los repositorios agrícolas tienen responsabilidad de CRUD de datos de dominio. El estado de sync es una preocupación de infraestructura de sincronización que no debe contaminar las interfaces de dominio.

### D8: Perfiles creados por trigger (no por la app)

**Decisión:** No implementar `create_profile()` en el adapter. El trigger remoto crea profiles.
**Alternativa descartada:** POST manual a tabla profiles tras signup.
**Razón:** El trigger ya existe y funciona. Duplicar la lógica genera inconsistencias. La app solo necesita confirmar que el signup fue exitoso.

---

## Consideraciones de rendimiento en Raspberry Pi

1. **Sync NO bloquea el event loop:** Las operaciones httpx se ejecutan en un thread separado (`run_in_threadpool` de Starlette).
2. **Subidas de imágenes secuenciales:** No paralelizar uploads para evitar saturar CPU/red en RPi.
3. **Timeout por request:** 30s para datos, 60s para uploads de imágenes.
4. **Sin retry automático agresivo:** Máximo 1 retry por entidad con backoff de 2s.
5. **Liberación de memoria:** No cargar todas las imágenes en memoria; leer y subir una por una.
6. **No interferir con pipeline:** Sync solo se ejecuta cuando NO hay un monitoreo `running` o `analyzing`.
7. **Un solo sync a la vez:** `SyncRuntimeState` previene ejecuciones concurrentes.
