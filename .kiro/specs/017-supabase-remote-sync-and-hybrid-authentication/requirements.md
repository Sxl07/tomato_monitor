# Requirements Document

## Introduction

Este documento especifica los requisitos para integrar Supabase como backend remoto **opcional** en Tomato Monitor, sin reemplazar la arquitectura offline-first existente basada en SQLite. La integración abarca:

1. Autenticación online vía Supabase Auth con fallback local por conectividad.
2. Registro de usuarios remoto (perfiles creados automáticamente por trigger en Supabase) con caché local segura.
3. Sincronización manual unidireccional de datos agrícolas (SQLite → Supabase PostgreSQL).
4. Almacenamiento remoto de snapshots (raw + anotados) vía Supabase Storage.

### Principios rectores

- SQLite permanece como fuente de verdad operacional.
- La pérdida de Internet NUNCA bloquea operaciones locales.
- La integración con Supabase es OPCIONAL (configurable vía variables de entorno).
- Sincronización manual solamente (sin background sync).
- Dirección de sync MVP: SQLite → Supabase (unidireccional).
- PK entero local preservado; UUID remoto pre-generado como identidad adicional estable.
- Clean Architecture: la capa de aplicación depende de abstracciones (puertos), no de implementaciones concretas.

### Decisiones cerradas

1. Supabase es el proveedor remoto seleccionado para esta iteración.
2. Se usa Supabase Auth como autoridad de identidad cuando hay conectividad.
3. No se usa `service_role` en Raspberry Pi; solo la Supabase publishable key.
4. La sincronización es manual, explícita, idempotente y retriable.
5. Los crops no se sincronizan en esta versión.
6. Las rutas de Storage son determinísticas (basadas en UUID pre-generado de monitoreo + frame_index).
7. PostgreSQL almacena solo rutas de objetos, nunca URLs firmadas.
8. No se implementa OAuth, login social, MFA ni recuperación de contraseña por SMTP.
9. No se implementa sincronización bidireccional ni resolución avanzada de conflictos.
10. No se configura systemd, kiosk ni autostart hasta validar el flujo completo en RPi.
11. Los perfiles remotos son creados por un trigger `AFTER INSERT` en `auth.users` — NO se crean manualmente via POST.
12. El `remote_id` (UUID) se pre-genera localmente antes del primer envío remoto (reservación).
13. Los JWT de Supabase NUNCA se persisten (ni en SQLite, ni en cookies, ni en filesystem).
14. Para sincronizar, el operario proporciona su contraseña al momento — tokens descartados al finalizar.
15. `httpx` es una dependencia runtime DIRECTA de Spec 017 (los adapters de producción la requieren). Debe agregarse a `requirements.txt` y `requirements-raspberry.txt` ANTES de implementar los adapters.
16. No se verifica alcanzabilidad (health check) ANTES de intentar `sign_in` — se intenta directamente y se clasifica la respuesta.
17. El sync se bloquea si hay un monitoreo en estado `running` o `analyzing`.
18. Solo un proceso de sync puede ejecutarse a la vez por dispositivo.

### Componentes estables (no romper)

Los siguientes componentes deben mantenerse estables durante la implementación de esta spec:

- `src/application/services/capture_worker.py`
- `src/application/services/snapshot_analysis_service.py`
- `src/application/services/monitoring_service.py`
- `src/application/services/monitoring_runtime_registry.py`
- `src/domain/value_objects/monitoring_status.py`
- `src/infrastructure/vision/*`
- `src/infrastructure/camera/*`
- `src/infrastructure/monitoring/thermal_monitor.py`
- `app/static/js/monitoring.js`
- `app/templates/agricultural/monitoring_execution.html`
- `app/templates/agricultural/monitoring_report.html`
- Pipeline capture-first completo
- Exportación ZIP local (ExportService, ExportPackage)
- UI portrait existente (excepto elementos estrictamente nuevos de sync/registro)

## Objetivo

Extender Tomato Monitor con capacidad de autenticación híbrida (remota + local) y sincronización manual de datos e imágenes hacia Supabase, manteniendo la operación completamente funcional sin Internet y preservando la integridad de los datos locales y los flujos existentes.

## Alcance

### Dentro del alcance

- Configuración de conexión a Supabase (opcional, vía .env)
- Registro de usuarios vía Supabase Auth (perfiles creados por trigger remoto)
- Login híbrido: intento directo contra Supabase Auth + fallback local solo por conectividad/indisponibilidad
- Caché local segura de credenciales tras login/registro exitoso
- Sincronización manual unidireccional SQLite → Supabase PostgreSQL
- Subida de snapshots raw y anotados a Supabase Storage
- Pre-generación de UUID remotos (reservación local antes de envío)
- Persistencia de estado de sync via `SyncStatePort` (tabla dedicada o columnas)
- Bloqueo de sync si hay monitoreo activo (running/analyzing)
- Prevención de sync concurrente (un solo proceso a la vez)
- UI de registro, estado de sync y errores
- Manejo de errores de red con clasificación precisa
- Tests unitarios sin Internet (mocks/fakes)
- Puertos/protocolos abstractos para Clean Architecture
- Adición de httpx como dependencia directa de runtime
- Creación de esquema cloud para activity_types y activity_logs (prerequisito de sync)
- Sincronización de activity_logs (después de modules/identity, cuando esquema remoto exista)
- Primer login remoto crea caché local automáticamente (usuario nuevo en dispositivo)
- Protección contra conflicto de identidad (remote_user_id binding)

### Fuera del alcance

- Multitenancy
- Sincronización bidireccional de datos agrícolas
- Resolución avanzada de conflictos
- Sync automático en background, cron, scheduler, polling
- Suscripciones realtime, WebSockets
- Sincronización de crops
- `service_role` en Raspberry Pi
- OAuth, login social, MFA
- Recuperación de contraseña vía SMTP
- Procesamiento AI en la nube
- Almacenamiento de imágenes en PostgreSQL (solo rutas)
- Eliminar SQLite como fuente operacional
- Reemplazar autenticación local existente
- Configuración de kiosk, systemd, autostart
- Persistencia de JWT/refresh_token en cualquier medio
- Propagación de eliminaciones locales hacia Supabase (tombstones, delete queue, DELETE remoto automático)

## Contexto actual

### Autenticación existente

- `AuthService` en `src/application/services/auth_service.py`
- PBKDF2-SHA256 para hashing de contraseñas
- HMAC-SHA256 para tokens de sesión
- Login local offline para usuarios registrados
- Bootstrap de admin vía variables de entorno
- Cookie de sesión firmada

### Modelo de datos relevante

- `User` (dominio): campos `remote_user_id` (nullable) y `sync_status` ya existen
- `MonitoringModel`: campo `sync_status` (default "pending") ya existe
- `ActivityLogModel`: campo `sync_status` (default "pending") ya existe
- `ExportPackage`: maneja exportación ZIP local (independiente de sync remoto)
- Modelos SIN campos de sync remoto: GreenhouseModel, ModuleModel, SnapshotModel, MonitoringMetricsModel, InspectionResultModel

### Infraestructura Supabase (validada)

- URL del proyecto: configurada y operativa
- Trigger `AFTER INSERT` en `auth.users` → función `handle_new_auth_user()` → crea `public.profiles` automáticamente
- Rol remoto por defecto: "operator" (asignado por trigger, NO por la app)
- Tablas remotas existentes: profiles, greenhouses, modules, monitorings, monitoring_metrics, snapshots, inspection_results
- Tablas remotas NO existentes aún: activity_types, activity_logs (prerequisito cloud para fase futura)
- Storage bucket: `tomato-monitor-snapshots` (privado, max 6 MiB, image/jpeg + image/png)
- Políticas RLS activas para rol `authenticated`
- Flujo Auth + JWT + RLS + Storage validado manualmente

### Convención de rutas Storage

```
tomato-monitor-snapshots/
└── monitorings/
    └── <remote_monitoring_uuid>/
        ├── raw/
        │   └── snapshot_<frame_index:06d>.jpg
        └── annotated/
            └── snapshot_<frame_index:06d>.jpg
```

### Dependencia httpx

- `requirements.txt` actual NO contiene httpx
- `requirements-test.txt` SÍ declara `httpx>=0.27.0` para testing
- httpx NO está garantizado como dependencia transitiva de runtime
- Spec 017 REQUIERE httpx como dependencia directa de runtime (los adapters Supabase ejecutan `import httpx`)
- httpx es pure Python — compatible con ARM64
- Su adición a `requirements.txt` y `requirements-raspberry.txt` es una tarea DENTRO de esta spec, ANTES de implementar adapters

## Glossary

| Término | Definición |
|---|---|
| Supabase_Auth | Servicio de autenticación de Supabase basado en JWT que actúa como autoridad de identidad remota |
| Publishable_Key | Supabase publishable key usada por la aplicación para autenticación y comunicación con API; no otorga acceso admin |
| Service_Role_Key | Clave administrativa de Supabase que bypasea RLS; prohibida en dispositivo de campo |
| Login_Híbrido | Proceso que intenta sign_in directo contra Supabase y recurre a local solo si hay error de conectividad/indisponibilidad |
| Fallback_Local | Autenticación contra hash local PBKDF2 cuando Supabase es inalcanzable por red o indisponible (5xx) |
| Caché_Local | Copia local segura (hash PBKDF2) de credenciales para permitir login offline |
| Sync_Manual | Proceso de sincronización iniciado explícitamente por el operario via botón en UI |
| Sync_Unidireccional | Flujo de datos exclusivamente desde SQLite local hacia Supabase remoto |
| Remote_UUID | Identificador UUID v4 pre-generado localmente y reservado como identidad remota estable |
| UUID_Reservation | Proceso de generar y persistir localmente un UUID antes del primer envío remoto |
| Sync_Status | Estado de sincronización por entidad: pending, syncing, synced, error |
| RLS | Row Level Security de Supabase que restringe acceso a datos por usuario autenticado |
| Supabase_Storage | Servicio de almacenamiento de objetos (S3-compatible) de Supabase para imágenes |
| Object_Path | Ruta relativa dentro del bucket de Storage; se almacena en PostgreSQL |
| Idempotencia_Sync | Propiedad que garantiza que reintentar una sincronización no produce duplicados gracias a UUIDs pre-generados |
| Operario | Usuario autenticado que opera el dispositivo portátil en el invernadero |
| RemoteAuthPort | Puerto abstracto que define operaciones de autenticación remota |
| RemoteDataPort | Puerto abstracto que define operaciones de push de datos remotos |
| RemoteStoragePort | Puerto abstracto que define operaciones de subida de archivos remotos |
| SyncStatePort | Puerto abstracto que define persistencia del estado de sincronización |
| Entidad_Sincronizable | Cualquier registro local (greenhouse, module, monitoring, etc.) que puede sincronizarse a Supabase |
| Trigger_Profiles | Trigger PostgreSQL `AFTER INSERT` en `auth.users` que crea automáticamente el perfil en `public.profiles` |

## Requirements

### Requirement 1: Configuración de Supabase

**User Story:** Como desarrollador, quiero que la integración con Supabase sea completamente opcional y configurable vía variables de entorno, para que el sistema funcione sin modificaciones cuando Supabase no está configurado.

#### Acceptance Criteria

1. THE Sistema_Auth SHALL leer la configuración de Supabase desde las variables de entorno `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY`.
2. WHEN las variables `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` no están definidas o están vacías, THE Sistema_Auth SHALL iniciar la aplicación en modo offline-only sin errores ni advertencias bloqueantes.
3. WHEN las variables `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` están definidas con valores válidos, THE Sistema_Auth SHALL habilitar las funcionalidades de autenticación remota y sincronización.
4. THE Sistema_Storage SHALL leer el nombre del bucket desde la variable de entorno `SUPABASE_STORAGE_BUCKET` con valor por defecto `tomato-monitor-snapshots`.
5. IF la variable `SUPABASE_URL` está definida pero `SUPABASE_PUBLISHABLE_KEY` está ausente, THEN THE Sistema_Auth SHALL registrar un warning en logs y operar en modo offline-only.
6. THE Sistema_Auth SHALL NO aceptar ni procesar ninguna variable de entorno de tipo `service_role`. La aplicación no soporta configuración administrativa. Si se detecta una variable `SUPABASE_SERVICE_ROLE_KEY` definida, registrar un ERROR crítico y operar en modo offline-only.
7. THE Sistema_Auth SHALL validar `SUPABASE_PUBLISHABLE_KEY` verificando que no esté vacía. SHALL validar `SUPABASE_URL` verificando que sea una URL con esquema https en producción. No se basa en heurísticas de contenido de la cadena ni en longitud arbitraria para determinar el tipo de clave.

### Requirement 2: Registro de usuarios vía Supabase Auth

**User Story:** Como operario nuevo, quiero registrar mi cuenta con nombre, email y contraseña cuando hay Internet disponible, para poder usar el sistema tanto online como offline.

#### Acceptance Criteria

1. WHEN el operario envía el formulario de registro con nombre, email y contraseña válidos y hay conectividad a Supabase, THE Sistema_Auth SHALL crear el usuario en Supabase Auth mediante `sign_up(email, password, metadata={"full_name": full_name})` y obtener el UUID remoto.
2. WHEN Supabase Auth confirma la creación del usuario, THE Sistema_Auth SHALL esperar a que el trigger remoto `handle_new_auth_user()` cree automáticamente el perfil en `public.profiles`. El sistema NO ejecuta POST manual a la tabla profiles.
3. WHEN el registro remoto es exitoso, THE Sistema_Auth SHALL crear o actualizar el registro local en SQLite con el password_hash (PBKDF2-SHA256), el `remote_user_id` (UUID de Supabase) y `sync_status = "synced"`.
4. IF el registro en Supabase Auth falla por email duplicado (HTTP 422), THEN THE Sistema_Auth SHALL informar al operario con el mensaje "El email ya está registrado" sin revelar detalles técnicos.
5. IF no hay conectividad a Supabase durante el intento de registro (timeout, DNS failure, connection refused), THEN THE Sistema_Auth SHALL informar al operario con el mensaje "Se requiere conexión a Internet para crear una cuenta" y no crear usuario local.
6. THE Sistema_Auth SHALL NEVER almacenar la contraseña en texto plano durante el proceso de registro.
7. THE Sistema_Auth SHALL NEVER enviar el password_hash local a Supabase; solo el password original viaja al endpoint de Auth para crear la cuenta remota.
8. THE Sistema_Auth SHALL NEVER enviar el campo `role` durante el registro. El rol remoto ("operator") es asignado exclusivamente por el trigger de Supabase. No es posible elevar privilegios desde el formulario de registro.

### Requirement 3: Identidad remota y relación local

**User Story:** Como desarrollador, quiero que cada usuario local tenga una referencia al UUID de Supabase sin reemplazar su PK entero, para mantener compatibilidad con el esquema existente.

#### Acceptance Criteria

1. THE Sistema_Auth SHALL utilizar el campo existente `remote_user_id` (String, nullable) de la entidad User para almacenar el UUID asignado por Supabase Auth.
2. THE Sistema_Auth SHALL preservar el campo `id` (Integer, PK autoincrement) como identificador primario en todas las relaciones locales (monitorings, activities, exports).
3. WHEN un usuario se registra o inicia sesión remotamente por primera vez, THE Sistema_Auth SHALL almacenar el UUID de Supabase en `remote_user_id` y no modificarlo en sesiones posteriores.
4. IF un usuario local no tiene `remote_user_id` asignado (usuario legacy pre-Spec017), THEN THE Sistema_Sync SHALL tratar ese usuario como no sincronizable hasta que complete un login o registro remoto.
5. THE Sistema_Auth SHALL NEVER usar el UUID remoto como clave foránea en tablas locales de SQLite.
6. WHEN un sign_in remoto es exitoso y no existe un User local con ese email, THE Sistema_Auth SHALL crear un nuevo User local con: full_name obtenido de user_metadata de Supabase (si es string no vacío; de lo contrario usar email como display name temporal), email autenticado, password_hash = hash PBKDF2-SHA256 de la contraseña proporcionada, role = "operator", remote_user_id = UUID de Supabase. Desde ese momento el usuario puede iniciar sesión offline.
7. WHEN un sign_in remoto es exitoso y existe un User local con ese email cuyo `remote_user_id` es NULL, THE Sistema_Auth SHALL asociar el UUID remoto autenticado a ese usuario local.
8. WHEN un sign_in remoto es exitoso y existe un User local con ese email cuyo `remote_user_id` es DISTINTO al UUID autenticado, THE Sistema_Auth SHALL rechazar la asociación, registrar un error de conflicto de identidad (IDENTITY_CONFLICT), y no sobrescribir silenciosamente el `remote_user_id` existente.

### Requirement 4: Login híbrido con intento directo

**User Story:** Como operario, quiero que el sistema valide mis credenciales contra Supabase cuando hay Internet y use mi contraseña local cuando no hay conexión, para poder trabajar en cualquier condición.

#### Acceptance Criteria

1. WHEN el operario envía credenciales de login y Supabase está configurado, THE Sistema_Auth SHALL intentar `sign_in` directamente contra Supabase Auth sin verificación previa de alcanzabilidad (no health check previo).
2. WHEN Supabase Auth responde con éxito de autenticación, THE Sistema_Auth SHALL actualizar el `password_hash` local del usuario con PBKDF2-SHA256 de la contraseña proporcionada, actualizar `last_login_at`, y crear la sesión local (cookie HMAC-SHA256). El JWT obtenido se descarta inmediatamente.
3. WHEN Supabase Auth responde con credenciales inválidas (HTTP 400/401 con mensaje de credenciales incorrectas), THE Sistema_Auth SHALL rechazar el login inmediatamente sin intentar fallback a autenticación local, independientemente de si el usuario tiene hash local.
4. WHEN Supabase Auth es inalcanzable por error de conectividad (timeout, DNS failure, connection refused, network unreachable), THE Sistema_Auth SHALL intentar autenticación local contra el `password_hash` almacenado en SQLite.
5. IF el fallback local se activa y el usuario no tiene `password_hash` local (nunca se registró ni inició sesión desde este dispositivo), THEN THE Sistema_Auth SHALL rechazar el login con el mensaje "Se requiere conexión a Internet para el primer inicio de sesión".
6. WHEN el login local (fallback) es exitoso, THE Sistema_Auth SHALL crear la sesión local normalmente y registrar en logs que se usó autenticación offline.
7. THE Sistema_Auth SHALL clasificar respuestas de Supabase Auth según la siguiente tabla: (a) timeout/DNS/connection refused/network unreachable → CONNECTIVITY → fallback local; (b) HTTP 500/502/503 → REMOTE_UNAVAILABLE → fallback local; (c) respuesta explícita de credenciales inválidas (validar body/código de error de Auth, no solo status code) → INVALID_CREDENTIALS → rechazo sin fallback; (d) HTTP 429 → RATE_LIMITED → rechazo sin fallback con mensaje apropiado; (e) HTTP 403 en contexto Auth → AUTH_FORBIDDEN → rechazo sin fallback; (f) otros errores 4xx → rechazo sin fallback.
8. THE Sistema_Auth SHALL NEVER realizar un health check (HEAD/GET a endpoint de salud) como paso previo al sign_in. El sign_in se intenta directamente y la clasificación se hace sobre la respuesta o excepción.

### Requirement 5: Login offline para usuarios cacheados

**User Story:** Como operario, quiero poder iniciar sesión sin Internet en el dispositivo donde previamente me registré o inicié sesión, para no depender de conectividad en campo.

#### Acceptance Criteria

1. WHEN el operario intenta login y Supabase es inalcanzable (CONNECTIVITY o REMOTE_UNAVAILABLE) y el usuario tiene `password_hash` local válido, THE Sistema_Auth SHALL verificar la contraseña contra el hash PBKDF2-SHA256 almacenado.
2. WHEN la verificación local es exitosa, THE Sistema_Auth SHALL crear la sesión local y permitir acceso completo a todas las funcionalidades offline.
3. THE Sistema_Auth SHALL NEVER replicar ni consultar hashes internos de Supabase Auth; solo usa el hash local generado durante registro o login remoto exitoso.
4. IF un usuario existe solo remotamente (nunca inició sesión desde este dispositivo), THEN THE Sistema_Auth SHALL rechazar el login offline con mensaje claro: "Se requiere conexión a Internet para el primer inicio de sesión".
5. WHILE el operario está autenticado con sesión local válida, THE Sistema_Auth SHALL permitir todas las operaciones locales sin verificar conectividad.

### Requirement 6: Preservación de AuthService existente

**User Story:** Como desarrollador, quiero que los cambios de autenticación extiendan el AuthService existente sin reescribirlo, para mantener compatibilidad con tests y funcionalidades actuales.

#### Acceptance Criteria

1. THE Sistema_Auth SHALL preservar los métodos existentes de `AuthService`: `hash_password()`, `verify_password()`, `authenticate()`, `create_session_token()`, `verify_session_token()`.
2. THE Sistema_Auth SHALL preservar la función `maybe_bootstrap_admin()` sin modificaciones funcionales.
3. THE Sistema_Auth SHALL mantener PBKDF2-SHA256 como algoritmo de hashing local.
4. THE Sistema_Auth SHALL mantener HMAC-SHA256 como mecanismo de firma de tokens de sesión.
5. WHEN se agrega funcionalidad de autenticación remota, THE Sistema_Auth SHALL implementarla como extensión (nuevo servicio o métodos adicionales) sin alterar las firmas existentes.
6. THE Sistema_Auth SHALL mantener compatibilidad con el mecanismo de `Depends()` para protección de rutas en FastAPI.

### Requirement 7: Arquitectura offline-first

**User Story:** Como operario, quiero que todas las operaciones agrícolas funcionen sin Internet, para no perder productividad por problemas de conectividad en el invernadero.

#### Acceptance Criteria

1. WHILE no hay conectividad a Internet, THE Sistema_Auth SHALL permitir: login (usuarios cacheados), gestión de invernaderos, gestión de módulos, inicio/ejecución/finalización de monitoreos, análisis diferido de snapshots, consulta de resultados y métricas, registro de actividades agrícolas, consulta de historial combinado, consulta de reportes, y exportación ZIP local.
2. WHEN Supabase es inalcanzable durante una sesión activa, THE Sistema_Sync SHALL NO interrumpir ninguna operación local en curso.
3. WHEN un monitoreo está en estado `running` o `analyzing`, IF Supabase pierde conectividad, THEN THE Sistema_Sync SHALL no interferir con el pipeline capture-first.
4. THE Sistema_Sync SHALL NEVER bloquear la UI esperando respuesta de Supabase durante operaciones locales normales.
5. WHEN la aplicación inicia sin configuración de Supabase, THE Sistema_Auth SHALL operar de forma idéntica a la versión pre-Spec017 (modo offline-only completo).

### Requirement 8: Sincronización manual con bloqueo de monitoreo activo

**User Story:** Como operario, quiero sincronizar mis datos hacia Supabase manualmente cuando tenga Internet y no haya monitoreo en curso, para respaldar mi información sin interferir con capturas activas.

#### Acceptance Criteria

1. WHEN el operario presiona "Sincronizar ahora" en la UI, THE Sistema_Sync SHALL verificar que no haya un monitoreo en estado `running` ni `analyzing` antes de iniciar la sincronización.
2. IF existe un monitoreo en estado `running` o `analyzing`, THEN THE Sistema_Sync SHALL rechazar la sincronización con HTTP 409 y mensaje: "Finaliza el monitoreo en curso antes de sincronizar".
3. THE Sistema_Sync SHALL ejecutar la sincronización de forma manual, explícita, retriable e idempotente.
4. THE Sistema_Sync SHALL NEVER implementar sincronización automática mediante cron, scheduler, polling, hilos permanentes, suscripciones realtime ni WebSockets.
5. WHEN la sincronización está en progreso, THE Sistema_Sync SHALL mostrar progreso real con contadores thread-safe (entidades procesadas vs total).
6. IF la sincronización se interrumpe por pérdida de conectividad, THEN THE Sistema_Sync SHALL preservar el progreso parcial (entidades ya sincronizadas mantienen su estado) y permitir reintento desde el punto de interrupción.
7. WHEN la sincronización completa exitosamente, THE Sistema_Sync SHALL actualizar los `remote_sync_status` de las entidades procesadas a `synced` y registrar `last_synced_at`.
8. THE Sistema_Sync SHALL requerir un usuario autenticado con sesión válida y `remote_user_id` asignado para ejecutar la sincronización.
9. THE Sistema_Sync SHALL permitir solo un proceso de sincronización a la vez por dispositivo. Si ya hay uno en curso, retornar HTTP 409 con mensaje: "Sincronización en curso, espera a que finalice".

### Requirement 9: Dirección de sincronización (MVP unidireccional)

**User Story:** Como desarrollador, quiero que el MVP sincronice datos solo desde SQLite hacia Supabase, para simplificar la implementación y evitar conflictos complejos.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL implementar sincronización exclusivamente en dirección SQLite → Supabase (push unidireccional).
2. THE Sistema_Sync SHALL tratar la Raspberry Pi como escritor primario de datos operacionales agrícolas.
3. THE Sistema_Sync SHALL NOT implementar descarga de datos desde Supabase hacia SQLite en esta versión.
4. THE Sistema_Sync SHALL NOT implementar resolución de conflictos bidireccional en esta versión.
5. IF un registro remoto fue modificado externamente en Supabase, THEN THE Sistema_Sync SHALL sobrescribirlo con los datos locales durante la próxima sincronización (last-write-wins local).

### Requirement 10: Pre-generación de UUID remotos (reservación)

**User Story:** Como desarrollador, quiero que los UUIDs remotos se pre-generen localmente antes del envío, para eliminar la ventana de crash entre escritura remota y confirmación local.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL mantener el campo `id` (Integer, PK autoincrement) como identificador primario en SQLite para todas las entidades.
2. WHEN una entidad necesita sincronizarse y no tiene `remote_id`, THE Sistema_Sync SHALL generar un UUID v4 localmente y persistirlo con `remote_sync_status = "pending"` (reservación) ANTES de intentar el envío remoto.
3. THE Sistema_Sync SHALL enviar el `remote_id` pre-generado como campo `id` explícito en el payload de upsert a PostgREST.
4. IF el upsert remoto falla, THEN THE Sistema_Sync SHALL conservar el `remote_id` reservado y marcar `remote_sync_status = "error"`. El siguiente reintento reutilizará EXACTAMENTE el mismo UUID.
5. IF el upsert remoto tiene éxito, THEN THE Sistema_Sync SHALL marcar `remote_sync_status = "synced"` y registrar `last_synced_at`.
6. THE Sistema_Sync SHALL NEVER generar un nuevo UUID para una entidad que ya tiene uno reservado. El `remote_id` es una identidad estable.
7. THE Sistema_Sync SHALL NOT reemplazar PKs enteros locales por UUIDs en tablas SQLite ni en relaciones de clave foránea.

### Requirement 11: Persistencia de estado de sync (SyncStatePort)

**User Story:** Como desarrollador, quiero un mecanismo centralizado para gestionar el estado de sincronización, manteniendo la separación entre la lógica de sync y los repositorios agrícolas existentes.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL definir un puerto abstracto `SyncStatePort` que provea operaciones de: consultar entidades pendientes, reservar UUID, marcar syncing, marcar synced, marcar error, y gestionar rutas de Storage.
2. THE Sistema_Sync SHALL implementar `SyncStatePort` en la capa de infraestructura, sin modificar las interfaces existentes de los repositorios agrícolas (GreenhouseRepository, ModuleRepository, etc.).
3. WHEN una entidad nueva se crea localmente, THE Sistema_Sync SHALL considerar su `remote_sync_status` inicial como "pending" (ya sea por columna con default o por ausencia de remote_id).
4. WHEN una entidad inicia su sincronización, THE Sistema_Sync SHALL actualizar `remote_sync_status = "syncing"` vía `SyncStatePort`.
5. WHEN una entidad se sincroniza exitosamente, THE Sistema_Sync SHALL actualizar `remote_sync_status = "synced"` y `last_synced_at` con timestamp UTC vía `SyncStatePort`.
6. IF una entidad falla al sincronizar, THEN THE Sistema_Sync SHALL actualizar `remote_sync_status = "error"` y `remote_sync_error` con descripción clasificada del fallo vía `SyncStatePort`.
7. THE Sistema_Sync SHALL NOT reutilizar los estados de `ExportPackage` (pending, generating, completed, error) para sincronización remota; los estados de sync son independientes.

### Requirement 12: Orden de sincronización (dependencia padre-hijo)

**User Story:** Como desarrollador, quiero que la sincronización respete dependencias entre entidades, para que los registros hijos siempre tengan su padre remoto disponible.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL sincronizar entidades en el siguiente orden estricto: identidad verificada → greenhouses → modules → monitorings → monitoring_metrics → snapshots + Storage → inspection_results → activity_logs.
2. WHEN una entidad hija necesita sincronizarse, THE Sistema_Sync SHALL verificar que su entidad padre tenga `remote_id` confirmado (synced) antes de intentar la sincronización del hijo.
3. IF una entidad padre no tiene `remote_id` confirmado, THEN THE Sistema_Sync SHALL sincronizar al padre primero o marcar al hijo como `pending` hasta que el padre se sincronice.
4. IF una entidad padre falla al sincronizar, THEN THE Sistema_Sync SHALL marcar a todos sus hijos pendientes como `pending` (no `error`) y registrar la dependencia bloqueada.
5. WHEN se sincronizan snapshots, THE Sistema_Sync SHALL requerir que el monitoring padre tenga `remote_id` confirmado para construir la ruta de Storage correcta.
6. THE Sistema_Sync SHALL NOT hacer CRUD sobre `profiles` como entidad agrícola sincronizable. La identidad se verifica (user.remote_user_id existe + JWT válido + perfil auto-creado por trigger) pero no se sincroniza manualmente.

### Requirement 13: Preservación de rutas locales de snapshots

**User Story:** Como desarrollador, quiero que la sincronización no modifique destructivamente las rutas locales de imágenes, para que el sistema siga funcionando offline con sus archivos intactos.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL preservar el campo `image_path` del Snapshot como referencia autoritativa al archivo local sin modificarlo durante la sincronización.
2. THE Sistema_Sync SHALL NEVER eliminar, mover ni renombrar archivos locales de snapshots como parte del proceso de sincronización.
3. WHEN un snapshot se sincroniza a Storage, THE Sistema_Sync SHALL almacenar adicionalmente la ruta del objeto remoto (e.g., `raw_storage_path`, `annotated_storage_path`) vía `SyncStatePort` sin sobrescribir `image_path`.
4. IF la sincronización de un snapshot falla, THEN THE Sistema_Sync SHALL preservar el archivo local intacto y el `image_path` sin cambios.

### Requirement 14: Supabase Storage para imágenes (flujo por snapshot)

**User Story:** Como operario, quiero que mis snapshots de monitoreo se respalden en la nube cuando sincronizo, para tener una copia remota accesible desde otros dispositivos.

#### Acceptance Criteria

1. THE Sistema_Storage SHALL seguir el siguiente orden por snapshot: (a) verificar que el monitoring padre tiene `remote_id` confirmado, (b) reservar/obtener `remote_id` estable del snapshot, (c) leer archivo local desde `image_path`, (d) subir raw a ruta determinística, (e) si existe anotado, subir anotado, (f) upsert metadata del snapshot en PostgREST con remote_id, monitoring_id remoto y rutas de Storage, (g) solo después de todo exitoso: marcar snapshot como synced.
2. THE Sistema_Storage SHALL subir snapshots raw al bucket `tomato-monitor-snapshots` con la ruta: `monitorings/{remote_monitoring_uuid}/raw/snapshot_{frame_index:06d}.jpg`.
3. WHEN existen snapshots anotados para un monitoreo, THE Sistema_Storage SHALL subirlos al bucket con la ruta: `monitorings/{remote_monitoring_uuid}/annotated/snapshot_{frame_index:06d}.jpg`.
4. THE Sistema_Storage SHALL almacenar en PostgreSQL remoto SOLO la ruta del objeto (object path relativa al bucket), NEVER URLs firmadas ni tokens.
5. THE Sistema_Storage SHALL usar el `remote_monitoring_uuid` (UUID pre-generado del monitoreo) para construir las rutas, NEVER el ID entero local.
6. THE Sistema_Storage SHALL autenticar todas las solicitudes de subida usando el JWT del usuario autenticado (RLS), NEVER con service_role key.
7. WHEN un snapshot raw no existe localmente (archivo eliminado manualmente), THE Sistema_Storage SHALL marcar ese snapshot como error (no synced) y registrar un warning sin interrumpir la sincronización del resto.
8. IF Storage upload tiene éxito pero PostgREST upsert falla, THEN THE Sistema_Storage SHALL NO eliminar los objetos ya subidos. El reintento reutilizará las mismas rutas determinísticas.
9. THE Sistema_Storage SHALL sincronizar `inspection_results` SOLO después de que el snapshot padre está confirmado como synced.

### Requirement 15: Idempotencia de sincronización (UUID pre-generados)

**User Story:** Como operario, quiero poder reintentar la sincronización sin crear registros ni archivos duplicados en la nube, para no desperdiciar almacenamiento ni generar confusión.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL usar UUIDs pre-generados como campo `id` explícito en el payload de upsert, con header `Prefer: resolution=merge-duplicates` y `on_conflict` apuntando a la columna `id`.
2. THE Sistema_Storage SHALL construir rutas de objetos de forma determinística usando: `{remote_monitoring_uuid}` + `{frame_index:06d}` + tipo (raw/annotated).
3. WHEN se reintenta la subida de un snapshot que ya existe en Storage (misma ruta), THE Sistema_Storage SHALL sobrescribir el objeto existente sin crear duplicados.
4. WHEN se reintenta la sincronización de una entidad cuyo `remote_id` ya fue enviado, THE Sistema_Sync SHALL enviar exactamente el mismo UUID — produciendo un upsert (actualización) en vez de inserción duplicada.
5. IF el retry falla, THEN THE Sistema_Sync SHALL preservar el `remote_id` reservado sin modificarlo. Nunca se genera un UUID nuevo para reintento.

### Requirement 16: Compatibilidad con datos históricos

**User Story:** Como operario, quiero que mis monitoreos y datos existentes (pre-Spec017) puedan sincronizarse eventualmente, sin que la migración los invalide o elimine.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL soportar sincronización tanto de registros creados post-Spec017 como de registros pre-existentes en SQLite que no tengan `remote_id`.
2. WHEN se sincronizan registros históricos sin `remote_id`, THE Sistema_Sync SHALL pre-generar un UUID, reservarlo localmente, y usarlo para el upsert remoto.
3. THE Sistema_Sync SHALL NEVER eliminar ni invalidar registros locales existentes como parte de migraciones de esquema.
4. IF se agregan nuevas columnas de metadatos de sync a tablas existentes, THEN THE Sistema_Sync SHALL definirlas como nullable o con defaults para preservar compatibilidad con registros históricos.

### Requirement 17: Manejo de errores con clasificación precisa

**User Story:** Como operario, quiero que los errores de Internet o de Supabase no corrompan mis datos locales ni interrumpan mi trabajo, para operar con confianza en condiciones de conectividad variable.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL clasificar errores según contexto. Para Auth adapter: CONNECTIVITY, REMOTE_UNAVAILABLE, INVALID_CREDENTIALS, EMAIL_EXISTS, RATE_LIMITED, AUTH_FORBIDDEN, UNKNOWN. Para Data/Storage adapters: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, STORAGE_ERROR, UNKNOWN. La clasificación debe basarse en el body/código de error de Supabase (no solo HTTP status code) cuando esté disponible.
2. THE Sistema_Auth SHALL habilitar fallback local SOLO para: CONNECTIVITY y REMOTE_UNAVAILABLE. NEVER para: INVALID_CREDENTIALS, RATE_LIMITED, AUTH_FORBIDDEN, ni otros errores 4xx de autenticación.
3. WHEN ocurre un error de red durante sincronización, THE Sistema_Sync SHALL preservar la integridad de SQLite sin modificar registros ya confirmados ni remote_ids ya reservados.
4. IF ocurre un error durante sincronización, THEN THE Sistema_Sync SHALL NEVER: corromper SQLite, eliminar archivos locales, perder un `remote_id` ya reservado, detener un monitoreo en curso, ni marcar falsamente datos como sincronizados.
5. WHEN un error de sincronización ocurre, THE Sistema_Sync SHALL permitir reintento manual sin requerir reinicio de la aplicación.
6. THE Sistema_Sync SHALL producir feedback comprensible en la UI para cada categoría de error.
7. IF ocurre un error durante la subida de un snapshot específico, THEN THE Sistema_Storage SHALL registrar el fallo para ese snapshot y continuar con el siguiente sin abortar la sincronización completa.

### Requirement 18: Seguridad y JWT efímero

**User Story:** Como desarrollador, quiero que la integración con Supabase siga las mejores prácticas de seguridad, protegiendo credenciales y sin persistir tokens.

#### Acceptance Criteria

1. THE Sistema_Auth SHALL usar exclusivamente la Supabase publishable key (`SUPABASE_PUBLISHABLE_KEY`) en la aplicación.
2. THE Sistema_Auth SHALL NEVER almacenar, usar ni aceptar una `service_role` key en el dispositivo.
3. THE Sistema_Auth SHALL NEVER persistir access_token ni refresh_token en ningún medio: no en SQLite, no en filesystem, no en cookies, no en .env, no en request.state entre requests.
4. WHEN el operario presiona "Sincronizar ahora", THE Sistema_Sync SHALL solicitar la contraseña de Supabase al operario, ejecutar `sign_in` para obtener un JWT efímero, usar ese JWT exclusivamente durante la ejecución del sync, y descartarlo al finalizar.
5. THE Sistema_Auth SHALL NEVER almacenar contraseñas en texto plano en SQLite, logs, variables de entorno con el valor real, ni archivos de configuración.
6. THE Sistema_Auth SHALL NEVER registrar en logs: contraseñas, JWT completos, refresh tokens, ni claves secretas.
7. THE Sistema_Auth SHALL NEVER incluir secretos reales en `.env.example` ni en archivos versionados en Git.
8. WHEN se realizan solicitudes remotas autenticadas, THE Sistema_Sync SHALL usar el JWT efímero del operario para que las políticas RLS de Supabase apliquen correctamente.
9. La contraseña proporcionada por el operario para sync vive solo en memoria durante la duración del request. NEVER se almacena ni se loguea.

### Requirement 19: Compatibilidad con Raspberry Pi 5

**User Story:** Como desarrollador, quiero que cualquier nueva dependencia sea compatible con ARM64 y justificada, para mantener el sistema funcional en Raspberry Pi 5.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL ser compatible con Raspberry Pi 5 (ARM64/aarch64) sin requerir compilación especial de dependencias.
2. THE Sistema_Sync SHALL usar `httpx` (pure Python, compatible ARM64) como cliente HTTP para comunicarse con Supabase. httpx debe agregarse como dependencia directa a `requirements.txt` y `requirements-raspberry.txt` dentro de esta spec, ANTES de implementar los adapters de infraestructura.
3. IF se agrega una nueva dependencia de Python, THEN SHALL estar explícitamente justificada, disponible como wheel para ARM64, y documentada en el commit.
4. THE Sistema_Sync SHALL operar dentro del perfil de recursos del Raspberry Pi 5 sin degradar el rendimiento del pipeline de visión ni saturar RAM durante sincronización.
5. THE Sistema_Sync SHALL NEVER ejecutar operaciones de sync que bloqueen el event loop principal de FastAPI.

### Requirement 20: Protección de componentes estables

**User Story:** Como desarrollador, quiero que la integración de Supabase no modifique componentes críticos del pipeline existente, para evitar regresiones en funcionalidad validada.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL NOT modificar la implementación interna de: `CaptureWorker`, `SnapshotAnalysisService`, `MonitoringService`, `MonitoringRuntimeRegistry`, `MonitoringState` FSM, módulos de `src/infrastructure/vision/`, módulos de `src/infrastructure/camera/`, ni `ThermalMonitor`.
2. THE Sistema_Sync SHALL NOT modificar la lógica del pipeline capture-first (captura → finalizar → liberar cámara → análisis diferido → reporte).
3. THE Sistema_Sync SHALL NOT modificar la funcionalidad de exportación ZIP local (ExportService, ExportPackage).
4. THE Sistema_Sync SHALL NOT modificar templates de monitoreo existentes (`monitoring_execution.html`, `monitoring_report.html`) excepto para agregar indicadores de sync_status no intrusivos.
5. WHEN se agregan elementos de UI para sync/registro, THE Sistema_Sync SHALL implementarlos como adiciones que no alteren el flujo existente de navegación portrait.

### Requirement 21: UI de sincronización con progreso real

**User Story:** Como operario, quiero ver claramente el estado de sincronización de mis datos con progreso real, para saber qué está respaldado y qué queda pendiente.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL mostrar en la UI el estado real de sincronización distinguiendo: sin conexión configurada, pendiente, sincronizando, sincronizado, error retriable.
2. THE Sistema_Sync SHALL NOT mostrar porcentajes falsos, barras de progreso inventadas ni conteos simulados durante la sincronización.
3. WHEN la sincronización está en progreso, THE Sistema_Sync SHALL mostrar el conteo real de entidades procesadas vs total pendiente, obtenido de un mecanismo thread-safe (`SyncRuntimeState`).
4. WHEN ocurre un error de sincronización, THE Sistema_Sync SHALL mostrar un mensaje comprensible para el operario con opción de reintentar.
5. WHEN Supabase no está configurado (modo offline-only), THE Sistema_Sync SHALL ocultar o deshabilitar los controles de sincronización remota sin generar confusión.

### Requirement 22: UI de registro

**User Story:** Como operario nuevo, quiero entender claramente cuándo puedo registrarme y cuándo solo puedo iniciar sesión offline, para no frustrarme intentando operaciones imposibles.

#### Acceptance Criteria

1. WHEN hay conectividad a Internet y Supabase está configurado, THE Sistema_Auth SHALL mostrar la opción de registro de nuevo usuario.
2. WHEN no hay conectividad a Internet, THE Sistema_Auth SHALL comunicar claramente que el registro requiere conexión y ofrecer solo login offline para usuarios cacheados.
3. WHEN el registro es exitoso, THE Sistema_Auth SHALL informar al operario que puede iniciar sesión offline en el futuro desde este dispositivo.
4. THE Sistema_Auth SHALL usar lenguaje simple y no técnico en los mensajes de registro: "Se requiere conexión a Internet para crear una cuenta nueva", "Tu cuenta fue creada. Ahora puedes iniciar sesión sin Internet."

### Requirement 23: Observabilidad y logging

**User Story:** Como desarrollador, quiero que las operaciones de sync y auth remota sean trazables en logs, para diagnosticar problemas sin exponer datos sensibles.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL registrar en logs: inicio/fin de sincronización, número de entidades procesadas por tipo, número de fallos por tipo, errores de conectividad, subidas de Storage completadas, reintentos ejecutados.
2. THE Sistema_Auth SHALL registrar en logs: tipo de autenticación utilizada (remota/local/fallback), errores clasificados de auth (sin revelar credenciales), registro exitoso de usuario, fallos de registro con código de error.
3. THE Sistema_Sync SHALL NEVER registrar en logs: contraseñas, JWT completos, refresh tokens, claves secretas, ni contenido de respuestas HTTP que contengan tokens.
4. WHEN ocurre un error categorizable, THE Sistema_Sync SHALL usar niveles de log apropiados: WARNING para errores recuperables/retriables, ERROR para fallos que requieren atención.

### Requirement 24: Testing

**User Story:** Como desarrollador, quiero pruebas automatizadas que validen el comportamiento de auth híbrida y sync sin requerir Internet, para mantener la suite ejecutable en CI/desarrollo.

#### Acceptance Criteria

1. THE Sistema_Auth SHALL incluir tests unitarios que verifiquen: inicio de aplicación sin configuración de Supabase, login con intento directo exitoso (mock), fallback local por CONNECTIVITY (mock), fallback local por REMOTE_UNAVAILABLE (mock), NO fallback cuando Supabase responde INVALID_CREDENTIALS (mock), caché local tras login remoto exitoso, registro online exitoso (mock), intento directo sin health check previo, primer login remoto crea usuario local cuando no existe, conflicto de identidad rechazado cuando remote_user_id no coincide.
2. THE Sistema_Sync SHALL incluir tests unitarios que verifiquen: idempotencia de sincronización con UUID pre-generados, respeto de dependencias padre-hijo, manejo de errores de red sin corrupción local, reintentos con mismo UUID, construcción determinística de rutas de Storage, preservación de datos históricos, bloqueo cuando hay monitoreo activo, prevención de sync concurrente, flujo de snapshot (Storage → PostgREST → mark synced), crash recovery (crash entre upsert y mark_synced → reintento usa mismo UUID), reconciliación de estado "syncing" stale, liberación de SyncRuntimeState ante excepciones.
3. THE Sistema_Auth SHALL ejecutar todos los tests unitarios sin requerir conexión a Internet, usando mocks, fakes o stubs para las llamadas a Supabase.
4. WHEN se implementen tests de integración reales contra Supabase, THE Sistema_Sync SHALL separarlos en una categoría marcada con `@pytest.mark.supabase` y no ejecutarlos en la suite principal.
5. THE Sistema_Auth SHALL incluir un test que verifique que la aplicación inicia correctamente sin variables de Supabase definidas (compatibilidad pre-Spec017).
6. THE Sistema_Sync SHALL incluir un test de migración que verifique: DB antigua + migración + segunda ejecución idempotente + integrity_check + foreign_key_check.

### Requirement 25: Puertos abstractos (Clean Architecture)

**User Story:** Como desarrollador, quiero que la capa de aplicación dependa de abstracciones y no de implementaciones concretas de Supabase, para mantener la testabilidad y la separación de capas.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL definir los siguientes puertos abstractos (Protocol o ABC) en `src/domain/interfaces/` o `src/application/interfaces/`: `RemoteAuthPort`, `RemoteDataPort`, `RemoteStoragePort`, `SyncStatePort`.
2. THE Sistema_Sync SHALL implementar cada puerto como un adaptador concreto en `src/infrastructure/supabase/` (para los remotos) y `src/infrastructure/persistence/` (para SyncStatePort).
3. THE aplicación layer (`src/application/`) SHALL NEVER importar clases concretas de infraestructura de Supabase (e.g., `SupabaseAuthAdapter`, `SupabaseDataAdapter`, `SupabaseStorageAdapter`). Solo importa los puertos.
4. THE Sistema_Sync SHALL inyectar los adaptadores concretos desde `app/dependencies.py` hacia los servicios de aplicación.
5. THE Sistema_Sync SHALL permitir reemplazar cualquier adaptador con un fake para testing sin modificar la lógica de aplicación.

### Requirement 26: Migración integrada en DatabaseManager

**User Story:** Como desarrollador, quiero que la migración de columnas de sync se integre en el flujo existente de `init_db()`, para que la base de datos se prepare automáticamente al iniciar la aplicación.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL agregar una función `_migrate_add_sync_columns(engine)` siguiendo el mismo patrón de `_migrate_add_columns(engine)` existente: PRAGMA table_info → detectar columnas ausentes → ALTER TABLE ADD COLUMN.
2. THE Sistema_Sync SHALL invocar `_migrate_add_sync_columns(engine)` desde `DatabaseManager.init_db()` después de las migraciones existentes.
3. THE Sistema_Sync SHALL garantizar que la migración es idempotente (ejecutarla múltiples veces no produce errores ni duplica columnas).
4. THE Sistema_Sync SHALL incluir un script auxiliar opcional en `scripts/migrate_017.py` que reutilice la misma lógica de migración para ejecución manual.
5. THE Sistema_Sync SHALL verificar integridad post-migración con `PRAGMA integrity_check` y `PRAGMA foreign_key_check`.

### Requirement 27: ActivityType y ActivityLog remotos

**User Story:** Como desarrollador, quiero sincronizar actividades agrícolas junto con el resto de datos, para que la trazabilidad agrícola esté respaldada remotamente.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL incluir la creación de tablas remotas `activity_types` y `activity_logs` en Supabase como prerequisito cloud de esta spec. La creación se ejecuta una sola vez antes de habilitar sync de actividades.
2. THE Sistema_Sync SHALL sembrar el catálogo de `activity_types` remotamente de forma idempotente (mismo contenido que `INITIAL_ACTIVITY_TYPES` local). Se usa el campo `code` como identificador natural estable para mapeo entre local y remoto.
3. THE Sistema_Sync SHALL agregar columnas de sync remoto a `activity_logs`: `remote_id` (VARCHAR(36), nullable), `remote_sync_status` (VARCHAR(20), default "pending"), `last_synced_at` (DATETIME, nullable), `remote_sync_error` (TEXT, nullable).
4. THE Sistema_Sync SHALL incluir `activity_logs` en el orden de sincronización DESPUÉS de modules e identity (respetando FKs: module.remote_id, user.remote_user_id, activity_type mapping por `code`).
5. `ActivityType` es un catálogo pre-sembrado; se mapea por `code` (no por integer id local) y no necesita las mismas columnas operacionales de sync que las entidades agrícolas.

### Requirement 28: No crear dependencias fuera de alcance

**User Story:** Como desarrollador, quiero que esta spec no introduzca funcionalidades fuera del alcance definido, para mantener el foco y la calidad del proyecto de tesis.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL NOT implementar multitenancy.
2. THE Sistema_Sync SHALL NOT implementar sincronización bidireccional de datos agrícolas.
3. THE Sistema_Sync SHALL NOT implementar resolución avanzada de conflictos (beyond last-write-wins local).
4. THE Sistema_Sync SHALL NOT implementar sincronización automática en background (cron, scheduler, polling, hilos permanentes).
5. THE Sistema_Sync SHALL NOT implementar suscripciones realtime ni WebSockets.
6. THE Sistema_Sync SHALL NOT sincronizar crops de tomates individuales a Storage.
7. THE Sistema_Auth SHALL NOT implementar OAuth, login social, MFA, ni recuperación de contraseña vía SMTP.
8. THE Sistema_Sync SHALL NOT almacenar imágenes binarias en PostgreSQL.
9. THE Sistema_Sync SHALL NOT eliminar SQLite como fuente de verdad operacional.
10. THE Sistema_Auth SHALL NOT reemplazar la autenticación local existente (solo extenderla).
11. THE Sistema_Sync SHALL NOT configurar systemd, Chromium kiosk, ni autostart del sistema.
12. THE Sistema_Auth SHALL NOT persistir JWT ni refresh tokens en ningún medio.
13. THE Sistema_Sync SHALL NOT implementar propagación automática de eliminaciones locales hacia Supabase (tombstones, delete queue, DELETE remoto). La sincronización se centra en CREATE/UPDATE (upsert).

### Requirement 29: Restricción de despliegue

**User Story:** Como desarrollador, quiero que la configuración de producción (systemd, kiosk, autostart) solo se realice después de validar completamente el flujo de Supabase en Raspberry Pi.

#### Acceptance Criteria

1. THE Sistema_Sync SHALL NOT configurar systemd services, Chromium kiosk mode, ni auto-arranque del sistema hasta que se hayan validado exitosamente en Raspberry Pi 5: autenticación vía Supabase Auth, registro de usuarios, login híbrido, sincronización manual completa, subida a Storage, y operación completa del pipeline capture-first con datos reales.
2. WHEN se documente la validación en RPi, THE Sistema_Sync SHALL registrar en `docs/benchmarks/` o `docs/thesis-notes/` los resultados de la prueba de integración real (fecha, device, OS, commit, tiempos de sync, tamaños de archivos, errores encontrados).

## Criterios de aceptación del spec

La implementación de este spec SOLO será aceptada si se cumple todo lo siguiente:

1. Tomato Monitor inicia completamente sin configuración de Supabase.
2. Toda la funcionalidad offline existente permanece intacta.
3. Un usuario puede registrarse online vía Supabase Auth (perfil creado por trigger remoto).
4. El usuario registrado queda cacheado localmente de forma segura (hash PBKDF2).
5. El usuario puede iniciar sesión offline en el mismo dispositivo.
6. Con Internet: sign_in directo a Supabase (sin health check previo).
7. Credenciales remotas inválidas (INVALID_CREDENTIALS) NUNCA disparan fallback a contraseña local.
8. HTTP 429 (RATE_LIMITED) y HTTP 403 en contexto Auth (AUTH_FORBIDDEN) NUNCA disparan fallback local.
9. SQLite mantiene sus PKs enteros actuales.
10. UUIDs remotos se pre-generan localmente y son identidades estables reutilizables en reintentos.
11. Los datos agrícolas pueden sincronizarse manualmente SQLite → Supabase usando UUIDs pre-generados como PK explícito.
12. El reintento de sincronización no crea duplicados (mismo UUID → upsert).
13. Las dependencias padre-hijo se respetan en el orden de sync.
14. Snapshots raw llegan a Supabase Storage solo después de confirmar monitoring padre synced.
15. Snapshots anotados llegan a Storage cuando existen.
16. Las rutas de Storage son determinísticas.
17. `Snapshot.image_path` permanece como referencia local autoritativa.
18. Errores de la nube no eliminan ni corrompen información local ni remote_ids reservados.
19. Errores de la nube no detienen el pipeline capture-first.
20. No se almacena `service_role` en Raspberry Pi.
21. No se exponen passwords, JWT completos ni secretos en logs.
22. JWT NUNCA se persiste — es efímero solo durante la ejecución del sync.
23. Sync se bloquea si hay monitoreo activo (running/analyzing).
24. Solo un sync a la vez por dispositivo.
25. Migración integrada en DatabaseManager.init_db() (idempotente).
26. La capa de aplicación NO importa clases concretas de infraestructura Supabase.
27. Los tests unitarios ejecutan sin Internet.
28. La implementación final es compatible con RPi 5 ARM64.
29. httpx agregado como dependencia directa de runtime antes de implementar adapters.
30. Un primer login remoto en un dispositivo que no conoce al usuario crea la caché local automáticamente.
31. Un conflicto de identidad (remote_user_id existente ≠ UUID autenticado) se rechaza sin sobrescribir.
32. ActivityLog se sincroniza como parte del flujo de sync (no queda como fase futura indefinida).
33. Entidad synced modificada localmente conserva remote_id y pasa a pending (dirty tracking).
34. Eliminaciones locales NO se propagan a Supabase (limitación explícita del MVP).
35. Monitorings.created_by_user_id remoto mapea correctamente desde User.remote_user_id.
