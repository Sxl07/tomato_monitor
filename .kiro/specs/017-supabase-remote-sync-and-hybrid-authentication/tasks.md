# Implementation Plan: Sincronización Remota con Supabase y Autenticación Híbrida

## Overview

Implementación incremental de la integración opcional con Supabase para Tomato Monitor organizada en 5 fases:

- **Fase A (Base):** Puertos abstractos, configuración, dependencia httpx, migración SQLite, SyncStateRepository.
- **Fase B (Auth):** Adaptador Auth, HybridAuthService con primer-login y conflicto de identidad, rutas, registro.
- **Fase C (Cloud prerequisite):** Crear tablas activity_types/activity_logs en Supabase.
- **Fase D (Remote sync):** Adaptadores Data/Storage, SyncRuntimeState, RemoteSyncService incluyendo ActivityLog, rutas API/UI.
- **Fase E (Validation):** Suite local completa, smoke tests reales contra Supabase, validación física en Raspberry Pi, documentación.

Cada tarea deja el build y tests existentes en estado verde. NO usar Run All Tasks — cada fase debe revisarse antes de continuar.

## Tasks

- [ ] 1. Puertos abstractos y configuración de Supabase
  - [ ] 1.1 Crear puertos abstractos en `src/application/interfaces/`
    - Crear `src/application/interfaces/__init__.py`
    - Crear `src/application/interfaces/remote_auth_port.py` con Protocol `RemoteAuthPort` y dataclass `RemoteAuthResult` (incluye campos: success, user_id, access_token, email, full_name, error_type, error_message)
    - Crear `src/application/interfaces/remote_data_port.py` con Protocol `RemoteDataPort` y dataclass `RemoteUpsertResult`
    - Crear `src/application/interfaces/remote_storage_port.py` con Protocol `RemoteStoragePort` y dataclass `RemoteUploadResult`
    - Crear `src/application/interfaces/sync_state_port.py` con Protocol `SyncStatePort` y dataclass `StoragePaths`
    - Las interfaces NO importan httpx ni clases de infraestructura
    - _Requirements: 25.1, 25.3_

  - [ ] 1.2 Crear `src/infrastructure/supabase/__init__.py` y `supabase_config.py`
    - Crear el directorio `src/infrastructure/supabase/`
    - Implementar `SupabaseConfig` dataclass con propiedades `is_configured`, `auth_url`, `rest_url`, `storage_url`
    - Implementar `load_supabase_config()` que lea `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_STORAGE_BUCKET`
    - Validar: si `SUPABASE_SERVICE_ROLE_KEY` variable está definida → log ERROR + return None
    - Validar: SUPABASE_URL presente pero key ausente → WARNING + return None
    - Validar: SUPABASE_URL con esquema https en producción
    - Validar: key no vacía
    - NO basar rechazo en longitud arbitraria (>20) ni buscar "service_role" dentro del valor de la key
    - _Requirements: 1.1–1.7, 18.1, 18.2_

  - [ ] 1.3 Escribir tests unitarios para `SupabaseConfig`
    - Test: config completa retorna instancia válida
    - Test: variables vacías retorna None
    - Test: URL sin key retorna None con warning
    - Test: SUPABASE_SERVICE_ROLE_KEY definida → rechazada con error
    - Test: propiedades auth_url, rest_url, storage_url construyen URLs correctas
    - Test: URL sin esquema https → warning apropiado
    - _Requirements: 1.1–1.7, 24.5_

  - [ ] 1.4 Actualizar `.env.example` con variables de Supabase
    - Agregar sección `--- Supabase (optional) ---` con SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_STORAGE_BUCKET
    - No incluir valores reales; solo placeholders documentativos
    - _Requirements: 1.1, 18.7_

- [ ] 2. Dependencia httpx runtime
  - [ ] 2.1 Determinar y pinear versión de httpx
    - Consultar versión instalada en entorno PC (de requirements-test.txt: >=0.27.0)
    - Verificar última versión estable compatible con Python 3.10+ y ARM64
    - Seleccionar versión compatible (httpx es pure Python — no requiere compilación ARM64)
    - Agregar `httpx==<version>` a `requirements.txt`
    - Agregar `httpx==<version>` a `requirements-raspberry.txt`
    - `requirements-test.txt` puede relajar a `httpx>=0.27.0` (overlay de testing) o mantener
    - Verificar `import httpx` exitoso en PC
    - _Requirements: 19.2, 19.3_

- [ ] 3. Migración de esquema SQLite integrada en DatabaseManager
  - [ ] 3.1 Agregar columnas de sync a modelos ORM existentes
    - `GreenhouseModel`: remote_id (String(36), nullable), remote_sync_status (String(20), default "pending"), last_synced_at (DateTime, nullable), remote_sync_error (Text, nullable)
    - `ModuleModel`: mismas 4 columnas
    - `MonitoringModel`: mismas 4 columnas (independientes del sync_status existente para export ZIP)
    - `SnapshotModel`: mismas 4 columnas + raw_storage_path (String(500), nullable) + annotated_storage_path (String(500), nullable)
    - `MonitoringMetricsModel`: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - `InspectionResultModel`: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - `ActivityLogModel`: remote_id, remote_sync_status, last_synced_at, remote_sync_error
    - Todas nullable o con defaults para compatibilidad con datos históricos
    - _Requirements: 10.1, 10.2, 11.1, 13.3, 16.4, 27.3_

  - [ ] 3.2 Crear función `_migrate_add_sync_columns(engine)` en `database.py`
    - Implementar función idempotente usando PRAGMA table_info + ALTER TABLE ADD COLUMN
    - Seguir exactamente el patrón de `_migrate_add_columns(engine)` existente
    - Incluir todas las tablas: greenhouses, modules, monitorings, snapshots, monitoring_metrics, inspection_results, activity_logs
    - Invocar desde `DatabaseManager.init_db()` después de `_migrate_add_columns()`
    - _Requirements: 26.1, 26.2, 26.3_

  - [ ] 3.3 Escribir tests de migración idempotente
    - Test: DB vacía → create_all + migrate → columnas existen
    - Test: DB con datos existentes sin columnas de sync → migrate → columnas agregadas, datos preservados
    - Test: ejecutar migración dos veces consecutivas → sin errores (idempotente)
    - Test: PRAGMA integrity_check pasa después de migración
    - Test: PRAGMA foreign_key_check pasa después de migración
    - Test: registros históricos sin remote_id se leen correctamente
    - Test: remote_sync_status default es "pending" para nuevos registros
    - **Property 10: Compatibilidad con datos históricos y migración idempotente**
    - _Requirements: 16.1, 16.3, 16.4, 26.3, 26.5_

- [ ] 4. SyncStateRepository (implementación de SyncStatePort)
  - [ ] 4.1 Crear `src/infrastructure/persistence/sync_state_repository.py`
    - Implementar clase `SyncStateRepository` que satisface `SyncStatePort`
    - Usa SQLAlchemy session para leer/escribir columnas de sync en modelos existentes
    - Entity types soportados: "greenhouse", "module", "monitoring", "snapshot", "monitoring_metrics", "inspection_result", "activity_log"
    - Implementar: get_pending_entities, get_remote_id, reserve_remote_id, mark_syncing, mark_synced, mark_error, get_storage_paths, set_storage_paths
    - _Requirements: 11.1–11.7_

  - [ ] 4.2 Escribir tests unitarios para `SyncStateRepository`
    - Test: reserve_remote_id persiste UUID con status "pending"
    - Test: mark_syncing actualiza status a "syncing"
    - Test: mark_synced actualiza status a "synced" y last_synced_at
    - Test: mark_error actualiza status a "error" y remote_sync_error
    - Test: get_pending_entities retorna entidades no synced (incluyendo "syncing" stale)
    - Test: get_remote_id retorna UUID o None
    - Test: set_storage_paths persiste rutas correctamente
    - Test: entidad con remote_sync_status="syncing" (stale) es candidata retryable
    - Usar in-memory SQLite
    - _Requirements: 11.1–11.7_

- [ ] 5. Checkpoint — Base
  - Ejecutar `python -m pytest -q` para confirmar que la suite completa pasa con nuevas columnas y SyncStateRepository.
  - Verificar que la aplicación inicia sin variables de Supabase configuradas.
  - Verificar que `src/application/interfaces/` no importa nada de `src/infrastructure/`.

- [ ] 6. Adaptador de autenticación Supabase (RemoteAuthPort)
  - [ ] 6.1 Crear `SupabaseAuthAdapter` en `src/infrastructure/supabase/supabase_auth_adapter.py`
    - Implementar clase que satisface `RemoteAuthPort`
    - sign_up(email, password, full_name): POST /auth/v1/signup con data: {"full_name"} en metadata. NO enviar role.
    - sign_in(email, password): POST /auth/v1/token?grant_type=password. Extraer user_id, access_token, email, full_name de user_metadata.
    - Se intenta sign_in DIRECTAMENTE sin health check previo
    - Clasificar errores por contexto Auth: CONNECTIVITY, REMOTE_UNAVAILABLE, INVALID_CREDENTIALS (validar body, no solo status), EMAIL_EXISTS, RATE_LIMITED, AUTH_FORBIDDEN, UNKNOWN
    - NO implementar create_profile() — trigger lo hace
    - _Requirements: 2.1, 2.2, 2.8, 4.1, 4.3, 4.7, 4.8, 17.1, 18.1_

  - [ ] 6.2 Escribir tests unitarios para `SupabaseAuthAdapter`
    - Test: sign_in exitoso → extraer UUID, email, full_name y token
    - Test: sign_in con respuesta de credenciales inválidas (validar body) → INVALID_CREDENTIALS
    - Test: sign_up con HTTP 422 → EMAIL_EXISTS
    - Test: timeout excepción → CONNECTIVITY
    - Test: HTTP 503 → REMOTE_UNAVAILABLE
    - Test: HTTP 429 → RATE_LIMITED
    - Test: HTTP 403 en Auth → AUTH_FORBIDDEN
    - Test: sign_up NO envía role en payload
    - Test: NO existe método create_profile
    - Usar httpx.MockTransport
    - **Property 6: Clasificación correcta de errores**
    - _Requirements: 4.3, 4.7, 4.8, 17.1_

- [ ] 7. Servicio de autenticación híbrida (HybridAuthService)
  - [ ] 7.1 Crear `HybridAuthService` en `src/application/services/hybrid_auth_service.py`
    - Constructor recibe: AuthService, UserRepository, Optional[RemoteAuthPort]. NO ConnectivityChecker.
    - NO importa httpx ni SupabaseAuthAdapter (solo RemoteAuthPort)
    - login(email, password): intento directo → clasificar → éxito/rechazo/fallback
    - _resolve_local_user(email, password, auth_result): manejar 4 casos (no existe, remote_user_id None, coincide, conflicto)
    - register(email, password, full_name): sign_up → crear User local → sesión
    - _Requirements: 2.1–2.8, 3.1–3.8, 4.1–4.8, 5.1–5.5, 6.1–6.6_

  - [ ] 7.2 Escribir tests: INVALID_CREDENTIALS no fallback
    - Test: Supabase responde INVALID_CREDENTIALS → login RECHAZADO → no se verifica hash local
    - Test: usuario TIENE hash local válido → aún así se rechaza si Supabase dice INVALID_CREDENTIALS
    - Test: RATE_LIMITED → rechazo sin fallback
    - Test: AUTH_FORBIDDEN → rechazo sin fallback
    - **Property 1: Login remoto inválido nunca dispara fallback local**
    - _Requirements: 4.3, 4.7, 17.2_

  - [ ] 7.3 Escribir tests: primer login remoto crea caché local
    - Test: sign_in exitoso + User local NO existe → se CREA User (full_name, hash, remote_user_id, role=operator)
    - Test: después de crear User → login offline con misma contraseña funciona
    - Test: full_name se extrae de RemoteAuthResult.full_name (metadata)
    - Test: full_name metadata vacío/ausente → User se crea usando email como display name temporal
    - Test: login NO se rechaza por full_name faltante
    - **Property 13: Primer login remoto crea caché local**
    - _Requirements: 3.6, 5.1_

  - [ ] 7.4 Escribir tests: conflicto de identidad
    - Test: User local con remote_user_id=NULL → sign_in exitoso → se ASOCIA UUID correctamente
    - Test: User local con remote_user_id == UUID autenticado → login normal, hash actualizado
    - Test: User local con remote_user_id ≠ UUID autenticado → IDENTITY_CONFLICT → rechazo → remote_user_id NO sobrescrito
    - **Property 14: Conflicto de identidad nunca sobrescribe remote_user_id**
    - _Requirements: 3.7, 3.8_

  - [ ] 7.5 Escribir tests unitarios adicionales para HybridAuthService
    - Test: remote_auth None → login local directo
    - Test: CONNECTIVITY → fallback local exitoso con hash existente
    - Test: REMOTE_UNAVAILABLE (500) → fallback local exitoso
    - Test: CONNECTIVITY + sin hash local → requires_internet=True
    - Test: registro exitoso → user local creado con remote_user_id y hash
    - Test: registro sin remote_auth → error "Registro no disponible"
    - Test: registro CONNECTIVITY → error "Se requiere conexión"
    - **Property 9: Hash local se actualiza tras cada login remoto exitoso**
    - _Requirements: 4.2, 4.4, 5.1, 5.2, 2.3, 2.5_

- [ ] 8. Integración de autenticación en rutas FastAPI
  - [ ] 8.1 Modificar `app/routes/auth.py` para usar HybridAuthService
    - Modificar login_submit para usar HybridAuthService.login()
    - Inyectar dependencias: si Supabase configurado → crear adapter; si no → pasar None
    - JWT obtenido se descarta inmediatamente (no se guarda)
    - Preservar require_current_user_html y require_current_user_api sin cambios
    - _Requirements: 4.1–4.8, 6.5, 6.6, 18.3_

  - [ ] 8.2 Agregar ruta `/registro` (GET y POST)
    - GET: formulario (nombre, email, password, confirmar)
    - POST: procesar con HybridAuthService.register()
    - Si Supabase no configurado: mensaje "Registro no disponible"
    - _Requirements: 2.1–2.8, 22.1–22.4_

  - [ ] 8.3 Crear template `app/templates/auth/register.html`
    - Formulario touch-first, portrait, targets ≥44px
    - _Requirements: 22.1–22.4_

  - [ ] 8.4 Registrar SupabaseConfig en `app/dependencies.py` y startup
    - Factory get_supabase_config() → app.state.supabase_config
    - Factory get_hybrid_auth_service() construye con adapters apropiados
    - _Requirements: 1.2, 1.3, 7.5, 25.4_

- [ ] 9. Checkpoint — Auth
  - Ejecutar `python -m pytest -q`.
  - Verificar: app inicia sin Supabase → login local funciona como antes.
  - Verificar: src/application/ no importa httpx ni clases de infraestructura Supabase.

- [ ] 10. Prerequisito cloud — Tablas activity_types y activity_logs en Supabase
  - [ ] 10.1 Crear tablas remotas en Supabase PostgreSQL
    - Crear tabla `activity_types` con: id UUID PK (default gen_random_uuid()), code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, requires_product BOOLEAN, allows_quantity BOOLEAN, default_unit TEXT, is_active BOOLEAN. Usar `code` como identificador natural estable para mapeo.
    - Crear tabla `activity_logs` con: id UUID PK, module_id UUID REFERENCES modules(id), user_id UUID REFERENCES profiles(id), activity_type_code TEXT REFERENCES activity_types(code), product_name TEXT, quantity FLOAT, unit TEXT, notes TEXT, occurred_at TIMESTAMPTZ, created_at TIMESTAMPTZ, remote definido como FK por code (no por integer id).
    - Aplicar políticas RLS consistentes con las tablas existentes.
    - Seed idempotente del catálogo activity_types (mismo contenido que INITIAL_ACTIVITY_TYPES). Usar INSERT ... ON CONFLICT (code) DO NOTHING.
    - Verificar con query de prueba que RLS permite acceso con JWT de usuario authenticated.
    - _Requirements: 27.1–27.5_

- [ ] 11. Adaptadores Data y Storage
  - [ ] 11.1 Crear `SupabaseDataAdapter` en `src/infrastructure/supabase/supabase_data_adapter.py`
    - Implementa RemoteDataPort
    - upsert(access_token, table, data): POST /rest/v1/{table} con Prefer: resolution=merge-duplicates
    - Payload incluye campo `id` = UUID pre-generado
    - Headers: Authorization Bearer, apikey
    - Timeout: 30s
    - Clasificar errores Data: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, UNKNOWN
    - _Requirements: 9.1, 10.3, 15.1, 18.8_

  - [ ] 11.2 Escribir tests para SupabaseDataAdapter
    - Test: upsert exitoso → retorna remote_id
    - Test: payload incluye campo `id` con UUID pre-generado
    - Test: header incluye Prefer: resolution=merge-duplicates
    - Test: error de red → CONNECTIVITY
    - Test: HTTP 403 → RLS_DENIED
    - Usar httpx.MockTransport
    - _Requirements: 10.3, 15.1, 17.1_

  - [ ] 11.3 Crear `SupabaseStorageAdapter` en `src/infrastructure/supabase/supabase_storage_adapter.py`
    - Implementa RemoteStoragePort
    - upload_file(access_token, local_file_path, remote_path): POST con x-upsert: true
    - build_snapshot_path estático: determinístico
    - Timeout: 60s
    - Si archivo no existe → retorna error STORAGE_ERROR
    - _Requirements: 14.1–14.8, 15.2–15.3_

  - [ ] 11.4 Escribir tests: rutas Storage determinísticas
    - Test: build_snapshot_path formato correcto
    - Test: frame_index padding 06d
    - Test: misma entrada → misma salida
    - Test: upload exitoso → retorna object_path
    - Test: archivo no existe → error sin crash
    - Test: timeout → error
    - Test: x-upsert header presente
    - **Property 2: Rutas de Storage determinísticas**
    - _Requirements: 14.2, 14.3, 15.2_

- [ ] 12. SyncRuntimeState
  - [ ] 12.1 Crear `src/application/services/sync_runtime_state.py`
    - Threading.Lock para concurrencia
    - try_acquire() → bool, release(result) en finally, update_progress(), get_status()
    - Al reiniciar app → estado idle (solo memoria)
    - _Requirements: 8.5, 8.9, 21.3_

  - [ ] 12.2 Escribir tests para SyncRuntimeState
    - Test: try_acquire → True primera vez, False segunda
    - Test: release → siguiente try_acquire retorna True
    - Test: excepción durante uso → release en finally → is_syncing=False
    - Test: update_progress + get_status → valores correctos
    - **Property 15: SyncRuntimeState siempre se libera tras excepción**
    - _Requirements: 8.9_

- [ ] 13. RemoteSyncService
  - [ ] 13.1 Crear `RemoteSyncService` en `src/application/services/remote_sync_service.py`
    - Constructor: RemoteDataPort, RemoteStoragePort, SyncStatePort, SyncRuntimeState (solo para update_progress)
    - NO importa httpx ni adaptadores concretos
    - NO llama try_acquire() ni release() — eso lo hace la ruta sync_api
    - SÍ llama runtime_state.update_progress() para reportar progreso real
    - execute_sync(access_token, user_remote_id) con orden:
      1. greenhouses → 2. modules → 3. monitorings (created_by_user_id = user.remote_user_id, nullable para históricos) → 4. monitoring_metrics → 5. snapshots + Storage → 6. inspection_results → 7. activity_logs (activity_type_code desde ActivityType.code, user_id = user.remote_user_id, module_id = module.remote_id)
    - UUID pre-generado: get_remote_id → si None → uuid4 → reserve_remote_id → mark_syncing → upsert → mark_synced/error
    - Snapshot flow: padre synced → reserve UUID → read file → upload raw → upload annotated → upsert PostgREST → solo si todo OK → mark_synced + set_storage_paths
    - Si raw no existe → mark_error
    - Si Storage OK pero PostgREST falla → NO eliminar uploads → mark_error
    - Hijo con padre no synced → permanece pending
    - Continuar ante errores individuales
    - Progreso real vía SyncRuntimeState.update_progress()
    - Entidades con remote_sync_status="syncing" stale → retryable
    - Si lanza excepción, la ruta se encarga de release() en finally
    - _Requirements: 8.1–8.9, 9.1–9.5, 10.2–10.7, 11.4–11.7, 12.1–12.6, 13.1–13.4, 14.1–14.9, 15.1–15.5, 17.3–17.7, 27.4_

  - [ ] 13.2 Escribir tests: dependencias padre-hijo
    - Test: sync exitoso en orden correcto
    - Test: padre sin remote_id synced → hijo permanece pending
    - Test: padre falla → hijo permanece pending
    - Test: snapshot con monitoring no synced → no se sincroniza
    - Test: inspection_result con snapshot no synced → no se sincroniza
    - Test: activity_log con module no synced → no se sincroniza
    - **Property 5: Orden de dependencia padre-hijo**
    - _Requirements: 12.1–12.6_

  - [ ] 13.3 Escribir tests: integridad local ante errores
    - Test: error de red → remote_id reservado NO se pierde
    - Test: error durante sync → image_path NO se modifica
    - Test: error → archivos locales NO se eliminan
    - Test: entidad previamente con remote_id → re-sync usa mismo UUID
    - Test: Storage OK + PostgREST falla → uploads NO eliminados
    - Test: raw file no existe → mark_error, no synced
    - **Property 4: Preservación de integridad local ante errores de red**
    - _Requirements: 13.1, 13.2, 13.4, 17.3, 17.4_

  - [ ] 13.4 Escribir tests: crash-recovery y UUID reuse
    - Test: entidad con remote_id reservado + status error → reintento usa MISMO UUID
    - Test: entidad con remote_sync_status="syncing" stale → retryable con mismo UUID
    - Test: payload de upsert siempre incluye id=remote_id reservado
    - **Property 3: Idempotencia de sincronización via UUID pre-generado**
    - **Property 11: Crash-recovery entre upsert remoto y confirmación local**
    - _Requirements: 10.4, 10.6, 15.1, 15.4, 15.5_

  - [ ] 13.5 Escribir tests: bloqueo monitoreo activo y sync concurrente
    - Test: monitoreo "running" → sync rechazado
    - Test: monitoreo "analyzing" → sync rechazado
    - Test: monitoreo "completed" → sync permitido
    - Test: segundo sync mientras primero activo → rechazado (409)
    - Test: excepción durante sign_in (antes de RemoteSyncService) → SyncRuntimeState liberado → siguiente sync permitido
    - Test: excepción durante RemoteSyncService → SyncRuntimeState liberado
    - **Property 12: Sync bloqueado durante monitoreo activo**
    - **Property 15: SyncRuntimeState siempre se libera**
    - _Requirements: 8.1, 8.2, 8.9_

  - [ ] 13.6 Escribir tests: dirty tracking
    - Test: entidad synced + modificación local → remote_sync_status pasa a "pending", remote_id conservado
    - Test: entidad pending (dirty) + sync → usa MISMO UUID → upsert → synced
    - Test: mark_synced no se revierte inmediatamente por dirty tracking
    - **Property 16: Dirty tracking preserva remote_id**
    - _Requirements: 10.6, 15.4_

- [ ] 14. Rutas de sincronización y UI
  - [ ] 14.1 Crear `app/routes/sync_api.py`
    - POST /api/sync/trigger: La ruta es ÚNICO dueño del lifecycle de SyncRuntimeState:
      ```
      if not runtime_state.try_acquire(): return 409
      try:
          verificar auth + remote_user_id (400)
          verificar no monitoreo activo via MonitoringRuntimeRegistry (409)
          sign_in con password del body → JWT efímero
          result = run_in_threadpool(sync_service.execute_sync, token, user_remote_id)
      finally:
          runtime_state.release(result if exists else None)
      ```
    - GET /api/sync/status: conteos reales de SyncRuntimeState + SyncStatePort
    - Password vive solo en request, nunca almacenada ni logueada
    - RemoteSyncService NO adquiere ni libera runtime — solo la ruta lo hace
    - _Requirements: 8.1–8.9, 18.3, 18.4, 18.9, 21.1–21.5_

  - [ ] 14.2 Registrar router sync_api en `app/main.py`
    - _Requirements: 7.5, 21.5_

  - [ ] 14.3 Agregar indicadores sync en templates (no intrusivos)
    - Dashboard: "Pendientes: N" si Supabase configurado
    - Botón "Sincronizar ahora" (solicita password)
    - Si no configurado: ocultar
    - _Requirements: 20.4, 20.5, 21.1–21.5_

  - [ ] 14.4 Escribir tests para rutas de sincronización
    - Test: POST sin auth → 401
    - Test: POST sin remote_user_id → 400
    - Test: POST con monitoreo activo → 409
    - Test: POST con sync en curso → 409
    - Test: GET sin Supabase → supabase_configured: false
    - Test: password NO aparece en logs
    - _Requirements: 8.1, 8.2, 8.8, 8.9, 18.6, 21.1–21.5_

- [ ] 15. Checkpoint — Sync
  - Ejecutar `python -m pytest -q`.
  - Verificar que app inicia sin Supabase → funciona igual que antes.
  - Verificar que no hay imports de httpx en src/domain/ ni src/application/.
  - **Property 8: Aplicación inicia sin Supabase configurado**

- [ ] 16. Test de inicio offline y observabilidad
  - [ ] 16.1 Escribir test de inicio sin Supabase
    - App FastAPI inicia sin errores cuando variables ausentes
    - Login local, monitoreo, exportación permanecen disponibles
    - Rutas sync retornan supabase_configured: false
    - _Requirements: 1.2, 7.1, 7.5_

  - [ ] 16.2 Verificar logging
    - Operaciones sync loguean inicio/fin, entidades, errores
    - NUNCA se loguean passwords, JWT, refresh tokens, claves
    - Usar caplog fixture
    - _Requirements: 23.1–23.4_

  - [ ] 16.3 Verificar protección de componentes estables
    - Ningún archivo modificado toca CaptureWorker, SnapshotAnalysisService, MonitoringService, etc.
    - Test boundaries arquitectónicas existente pasa
    - src/application/ no importa httpx ni clases Supabase concretas
    - _Requirements: 20.1–20.5, 25.3_

- [ ] 17. Suite completa local
  - Ejecutar `python -m pytest -q` y confirmar suite completa pasa incluyendo todos los tests nuevos.
  - Verificar conteo de tests creció significativamente.

- [ ] 18. Smoke tests reales contra Supabase (PC)
  - [ ] 18.1 Auth real
    - Registro de usuario de test (o login de usuario existente)
    - Verificar UUID retornado
    - Verificar profile creado automáticamente por trigger
    - Login real exitoso
    - Marcar con @pytest.mark.supabase
    - _Requirements: 24.4_

  - [ ] 18.2 Data real
    - Crear/sincronizar árbol temporal: greenhouse → module → monitoring → metrics → snapshot → inspection_result → activity_log
    - Verificar registros en PostgreSQL remoto
    - Verificar UUIDs coinciden
    - _Requirements: 24.4_

  - [ ] 18.3 Storage real
    - Upload raw a ruta determinística
    - Upload annotated si aplica
    - Verificar object paths en bucket
    - Descargar/verificar contenido
    - Cleanup de archivos temporales
    - _Requirements: 24.4_

  - [ ] 18.4 Idempotencia real
    - Ejecutar la misma sincronización dos veces
    - Confirmar que no aparecen duplicados en PostgreSQL
    - Confirmar que Storage no tiene archivos duplicados
    - _Requirements: 15.1–15.5_

  - [ ] 18.5 Seguridad
    - Verificar que ninguna admin/service_role key fue usada
    - Verificar que RLS bloquea acceso sin JWT
    - _Requirements: 18.1, 18.2_

- [ ] 19. Validación en Raspberry Pi 5
  - [ ] 19.1 Preparación
    - Sincronizar código a Raspberry Pi
    - Backup de SQLite antes de migración
    - Instalar/verificar httpx runtime en venv Raspberry
    - Configurar .env real localmente (sin versionarlo)
    - _Requirements: 19.1_

  - [ ] 19.2 Migración en RPi
    - Ejecutar DatabaseManager.init_db()
    - Verificar integrity_check, foreign_key_check
    - Verificar columnas nuevas presentes
    - Verificar datos históricos preservados
    - Ejecutar segunda vez (idempotente)
    - _Requirements: 26.1–26.5_

  - [ ] 19.3 Flujo completo en RPi
    - A. Login online Supabase
    - B. Registro online (si usuario nuevo)
    - C. Cerrar app / quitar Internet
    - D. Login offline del usuario cacheado
    - E. Crear datos offline (greenhouse, module)
    - F. Ejecutar monitoring capture-first real con cámara
    - G. Análisis y reporte
    - H. Restaurar Internet
    - I. "Sincronizar ahora" (proporcionar password)
    - J. Verificar PostgreSQL remoto (datos llegaron)
    - K. Verificar raw/annotated en Storage
    - L. Volver a sincronizar y verificar idempotencia
    - M. Provocar error de red durante sync (quitar Internet a mitad)
    - N. Verificar que pipeline, temperatura, cámara y export ZIP siguen funcionando
    - _Requirements: 7.1, 8.1–8.9, 14.1–14.9, 19.1, 20.1–20.5, 29.1_

- [ ] 20. Documentación final
  - [ ] 20.1 Documentar arquitectura hybrid auth y flujo sync
    - Actualizar docs/architecture.md con componentes Spec 017
    - Documentar variables de entorno
    - Documentar comportamiento offline
    - Documentar seguridad (JWT efímero, no service_role)
    - _Requirements: 29.2_

  - [ ] 20.2 Registrar resultados smoke y Raspberry
    - En docs/thesis-notes/ o docs/benchmarks/: fecha, device, OS, commit, tiempos de sync, tamaños, errores
    - Documentar limitaciones conocidas
    - _Requirements: 29.2_

- [ ] 21. Checkpoint final
  - Ejecutar `python -m pytest -q` (sin @pytest.mark.supabase).
  - Confirmar que toda funcionalidad offline permanece intacta.
  - Confirmar que ningún componente estable fue modificado.
  - NO configurar systemd, kiosk, autostart todavía.

## Notes

- Cada fase (A–E) debe revisarse y aprobarse antes de continuar con la siguiente.
- NO usar Run All Tasks.
- Tests críticos son OBLIGATORIOS (no marcados con `*`): INVALID_CREDENTIALS sin fallback, primer login crea caché, conflicto identidad, migración idempotente, UUID reuse/crash-recovery, padre-hijo, Storage paths, integridad local, offline startup, monitoreo activo bloquea, concurrency guard, SyncRuntimeState release, Storage failure handling, syncing stale reconciliation.
- NO se modifican componentes del pipeline capture-first.
- httpx es dependencia directa de runtime — se agrega a requirements.txt y requirements-raspberry.txt en Task 2.
- ActivityLog se sincroniza como parte del flujo (no es fase futura indefinida). Prerequisito cloud (Task 10) se ejecuta antes de implementar sync de actividades.
- ActivityType se mapea por `code` (identificador natural estable), no por integer id local.
- JWT es efímero: operario da password → sign_in → sync → descartar. Nunca persistido.
- Perfiles remotos creados por trigger — NO por código de la app.
- remote_id es UUID v4 pre-generado y reservado localmente ANTES del envío remoto.
- SyncRuntimeState.release() siempre en finally. Al reiniciar app → idle.
- Entidades con remote_sync_status="syncing" persistido (stale de crash) son retryable.
- Clasificación de errores separada por contexto (Auth adapter vs Data/Storage adapter).
- La capa de aplicación SOLO importa puertos de src/application/interfaces/. Nunca clases concretas de infraestructura.
- Smoke tests reales contra Supabase marcados con @pytest.mark.supabase y NO ejecutados en suite principal.
- Validación Raspberry Pi incluye flujo completo físico con cámara real.
- systemd, kiosk, autostart queda DESPUÉS de aceptar completamente Spec 017.
- **SyncRuntimeState ownership:** La ruta sync_api es el ÚNICO dueño de try_acquire/release. RemoteSyncService solo usa update_progress(). release() acepta None para excepciones pre-sync.
- **Dirty tracking:** Entidad synced modificada localmente → preserva remote_id + pasa a pending. No usar SQLAlchemy events globales. Integrar en puntos de persistencia local (repositories update).
- **DELETE remoto:** NO se propagan eliminaciones locales a Supabase. Limitación explícita del MVP documentada.
- **Monitoring FK remoto:** El campo remoto es `created_by_user_id` (UUID, references profiles.id), no "user_id".
- **ActivityLog FK remoto:** Usa `activity_type_code` (TEXT, references activity_types.code), no integer activity_type_id.
- **full_name fallback:** Si user_metadata no contiene full_name válido, usar email como display name temporal. NO rechazar login por esto.

## Task Dependency Graph

```json
{
  "phases": {
    "A_base": {
      "tasks": ["1", "2", "3", "4", "5"],
      "description": "Puertos, configuración, httpx, migración, SyncStateRepository"
    },
    "B_auth": {
      "tasks": ["6", "7", "8", "9"],
      "description": "Auth adapter, HybridAuthService, routes, registro"
    },
    "C_cloud": {
      "tasks": ["10"],
      "description": "Prerequisito cloud: activity_types/activity_logs en Supabase"
    },
    "D_sync": {
      "tasks": ["11", "12", "13", "14", "15", "16", "17"],
      "description": "Data/Storage adapters, SyncRuntimeState, RemoteSyncService, API/UI"
    },
    "E_validation": {
      "tasks": ["18", "19", "20", "21"],
      "description": "Smoke tests, validación RPi, documentación"
    }
  },
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1"] },
    { "id": 3, "tasks": ["3.1", "3.2"] },
    { "id": 4, "tasks": ["3.3"] },
    { "id": 5, "tasks": ["4.1"] },
    { "id": 6, "tasks": ["4.2"] },
    { "id": 7, "tasks": ["6.1"] },
    { "id": 8, "tasks": ["6.2"] },
    { "id": 9, "tasks": ["7.1"] },
    { "id": 10, "tasks": ["7.2", "7.3", "7.4", "7.5"] },
    { "id": 11, "tasks": ["8.1", "8.4"] },
    { "id": 12, "tasks": ["8.2", "8.3"] },
    { "id": 13, "tasks": ["10.1"] },
    { "id": 14, "tasks": ["11.1", "11.3", "12.1"] },
    { "id": 15, "tasks": ["11.2", "11.4", "12.2"] },
    { "id": 16, "tasks": ["13.1"] },
    { "id": 17, "tasks": ["13.2", "13.3", "13.4", "13.5"] },
    { "id": 18, "tasks": ["14.1"] },
    { "id": 19, "tasks": ["14.2", "14.3", "14.4"] },
    { "id": 20, "tasks": ["16.1", "16.2", "16.3"] },
    { "id": 21, "tasks": ["18.1", "18.2", "18.3", "18.4", "18.5"] },
    { "id": 22, "tasks": ["19.1"] },
    { "id": 23, "tasks": ["19.2"] },
    { "id": 24, "tasks": ["19.3"] },
    { "id": 25, "tasks": ["20.1", "20.2"] }
  ]
}
```
