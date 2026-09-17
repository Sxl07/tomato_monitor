# Spec 025 — Gestión segura de exportaciones

## Introducción

El sistema Tomato Monitor ya genera paquetes de exportación ZIP (local-first,
offline) con datos e imágenes del dispositivo. Actualmente existen las rutas
`GET /exportar`, `POST /exportar`, `GET /exportar/{id}` y
`GET /exportar/{id}/descargar`, junto con la entidad `ExportPackage`, su modelo
SQLAlchemy y el repositorio (`create`, `get_by_id`, `list_by_user`, `update`,
`list_pending`). No existe eliminación de paquetes.

Esta Spec convierte la vista de exportaciones en una gestión clara y segura,
cuyo objetivo principal es añadir **eliminación manual** de exportaciones
generadas. No se reconstruye el sistema de exportación, no se cambia el formato
ZIP, ni la generación, manifest o descarga, salvo que exista un bug real e
independiente.

### Definición operativa de "eliminar una exportación"

Eliminar una exportación significa **exclusivamente**:

1. eliminar el archivo ZIP local asociado, si existe y es seguro; y
2. eliminar el registro `ExportPackage` de SQLite.

Una exportación es una copia/paquete de respaldo. Eliminar el paquete **nunca**
elimina sus datos fuente.

---

## Requisitos

### Requisito 1 — Eliminación manual de una exportación

**Historia de usuario:** Como operario, quiero eliminar manualmente una
exportación que ya generé, para liberar espacio y mantener ordenada la lista de
respaldos sin afectar mis datos agrícolas.

#### Criterios de aceptación

1. CUANDO el operario confirma la eliminación de una exportación propia
   ENTONCES el sistema DEBERÁ eliminar el archivo ZIP local asociado (si existe
   y es seguro) y eliminar el registro `ExportPackage` de SQLite.
2. CUANDO la eliminación se completa con éxito ENTONCES el sistema DEBERÁ
   redirigir a `/exportar` mostrando un mensaje de confirmación claro, si el
   patrón de mensajes de la aplicación lo permite.
3. CUANDO se elimina un `ExportPackage` ENTONCES el sistema NO DEBERÁ eliminar
   ni modificar ninguna de las siguientes entidades o artefactos: `User`,
   `Greenhouse`, `Module`, `Monitoring`, `MonitoringMetrics`, `Snapshot`,
   `InspectionResult`, `ActivityLog`, `ActivityType`, imágenes originales de
   snapshots, annotated snapshots, recordings, pipeline metrics, sync outbox,
   datos sincronizados, datos remotos de Supabase ni objetos de Supabase
   Storage.
4. LA eliminación DEBERÁ usar una operación HTTP explícita `POST
   /exportar/{id}/eliminar`. El sistema NO DEBERÁ exponer acciones destructivas
   mediante `GET`.

### Requisito 2 — Aislamiento multiusuario (owner-scoped)

**Historia de usuario:** Como operario, quiero que mis exportaciones solo sean
accesibles y gestionables por mí, para que otro usuario no pueda ver, descargar
ni eliminar mis respaldos.

#### Criterios de aceptación

1. UN usuario solo DEBERÁ poder listar, ver, descargar y eliminar exportaciones
   cuyo `created_by_user_id` coincida con su propio id.
2. CUANDO un usuario referencia un `id` de exportación que pertenece a otro
   usuario ENTONCES el sistema DEBERÁ comportarse como si el recurso no
   existiera y NO DEBERÁ revelar metadata del otro usuario.
3. CUANDO un usuario intenta eliminar una exportación ajena ENTONCES el sistema
   NO DEBERÁ eliminar el archivo ni el registro, y DEBERÁ tratar el recurso como
   no encontrado.
4. LAS rutas existentes de detalle y descarga DEBERÁN mantener el aislamiento
   owner-scoped ya presente; cualquier problema de ownership detectado
   directamente relacionado con exportaciones DEBERÁ documentarse en esta Spec.

### Requisito 3 — Confirmación explícita en la UI

**Historia de usuario:** Como operario que usa una pantalla táctil en el
invernadero, quiero confirmar antes de eliminar, para no borrar un respaldo por
un toque accidental.

#### Criterios de aceptación

1. EN la lista de exportaciones CADA exportación DEBERÁ ofrecer una acción de
   eliminación visible mediante icono/botón de basura.
2. LA acción de eliminación NO DEBERÁ borrar con un solo toque; DEBERÁ requerir
   una confirmación explícita mostrada al operario.
3. EL diálogo de confirmación DEBERÁ comunicar de forma clara el alcance de la
   acción, indicando que se eliminarán el archivo ZIP y el registro de la
   exportación, y que los monitoreos, imágenes y demás datos originales no se
   eliminarán.
4. EL diálogo DEBERÁ ofrecer dos acciones diferenciadas: "Cancelar" y una acción
   destructiva visualmente distinta (p. ej. "Eliminar exportación").
5. LOS objetivos táctiles de la acción DEBERÁN cumplir un mínimo de 44×44 px y
   ser usables en una pantalla 800×480 touch-first.
6. LA acción de eliminación DEBERÁ existir **únicamente en la lista**
   (`/exportar`). El detalle NO DEBERÁ incluir botón de eliminación en esta
   Spec, para mantener el MVP compacto; el detalle solo recibe el ajuste de
   feedback `error`/`success`.
7. LA acción de basura DEBERÁ usar la clase de stylesheet correcta
   `btn-icon btn-icon--danger` (no `btn-icon-danger`), con `icon('delete')` y
   `aria-label="Eliminar exportación"`.

### Requisito 4 — Seguridad del `file_path` (fail-closed)

**Historia de usuario:** Como responsable de la integridad del dispositivo,
quiero que la eliminación física nunca pueda borrar un archivo arbitrario del
sistema, para proteger datos del dispositivo ante un `file_path` corrupto o
manipulado.

#### Criterios de aceptación

1. EL sistema NO DEBERÁ ejecutar `unlink()` sobre `file_path` sin validar
   previamente su contención.
2. LA eliminación física SOLO DEBERÁ permitirse dentro del directorio autorizado
   de exportaciones (`outputs/exports`), validado con `Path.resolve()` y una
   comprobación de contención (containment).
3. CUANDO `file_path` apunta a un ZIP válido dentro de `outputs/exports`
   ENTONCES el sistema DEBERÁ eliminar ese ZIP y luego el registro.
4. CUANDO `file_path` es `None` ENTONCES el sistema NO DEBERÁ intentar `unlink()`
   y DEBERÁ permitir eliminar el registro.
5. CUANDO `file_path` apunta a un archivo inexistente dentro del directorio
   autorizado ENTONCES el sistema DEBERÁ tratar el archivo como ya ausente y
   permitir limpiar el registro.
6. CUANDO `file_path` intenta escapar del directorio autorizado (ruta con `../`,
   ruta absoluta externa, o symlink que resuelve fuera de `outputs/exports`)
   ENTONCES el sistema DEBERÁ **abortar toda la eliminación (fail-closed)**, NO
   DEBERÁ borrar ningún archivo, DEBERÁ conservar el registro `ExportPackage` y
   DEBERÁ mostrar un mensaje de error.
7. CUANDO `file_path` resuelve dentro del directorio autorizado pero apunta a un
   nodo que existe y NO es un archivo regular (p. ej. un directorio) ENTONCES el
   sistema NO DEBERÁ eliminarlo, DEBERÁ conservar el registro y DEBERÁ devolver
   un error controlado.
8. LA validación de filesystem DEBERÁ vivir en `ExportDeletionService` y usar
   `Path.resolve()` + `relative_to(exports_root.resolve())` + sufijo `.zip`. El
   helper existente `_is_safe_export_path` (usado por la descarga) NO DEBERÁ
   modificarse salvo necesidad estricta.

### Requisito 5 — Comportamiento por estado

**Historia de usuario:** Como operario, quiero que la eliminación respete el
estado de la exportación, para no interrumpir una generación en curso.

#### Criterios de aceptación

1. SOLO los estados terminales DEBERÁN ser eliminables, mediante una whitelist
   fail-closed: `completed` y `error`.
2. UNA exportación en estado `completed` DEBERÁ ser eliminable.
3. UNA exportación en estado `error` DEBERÁ ser eliminable (limpieza de
   registro).
4. UNA exportación en estado `pending` NO DEBERÁ ser eliminable; el sistema
   DEBERÁ rechazar la eliminación (sin filesystem I/O y sin `repository.delete`)
   y conservar el registro con un mensaje de error.
5. UNA exportación en estado `generating` NO DEBERÁ ser eliminable; el sistema
   DEBERÁ rechazar la eliminación (sin filesystem I/O y sin `repository.delete`)
   y conservar el registro con un mensaje de error.
6. LA razón del bloqueo de `pending` DEBERÁ documentarse: tanto
   `POST /exportar` como `POST /sincronizacion/local` crean un `ExportPackage` y
   luego ejecutan `ExportService.generate_export` de forma síncrona dentro de la
   misma request; `POST /sincronizacion/local` usa concretamente `pending`
   durante la generación. Por tanto `pending` puede representar un paquete en
   uso y no existe señal fiable para distinguir un `pending` inactivo de uno en
   uso. No se creará worker, cola, runtime registry ni heurística de "stale
   jobs". `POST /sincronizacion/local` NO se modifica.
7. COMO limitación conocida: un paquete `pending`/`generating` que quede
   huérfano por una interrupción NO puede limpiarse manualmente en esta Spec; se
   prioriza la integridad sobre el cleanup automático.

### Requisito 6 — Robustez ante archivo ausente

**Historia de usuario:** Como operario, quiero que la lista y el detalle no se
rompan si el ZIP físico ya no existe, para poder limpiar registros huérfanos.

#### Criterios de aceptación

1. CUANDO una exportación tiene `status = completed` y `file_path` registrado
   pero el ZIP está físicamente ausente ENTONCES la lista y el detalle DEBERÁN
   seguir siendo renderizables sin error.
2. CUANDO el ZIP está ausente ENTONCES la eliminación manual DEBERÁ permitir
   limpiar el registro (tratando el archivo como ya ausente).
3. CUANDO se intenta descargar una exportación cuyo ZIP está ausente ENTONCES la
   descarga DEBERÁ gestionar el caso de forma segura (sin error 500),
   redirigiendo con un mensaje.
4. ESTE requisito NO DEBERÁ convertirse en un sistema de reparación automática.

### Requisito 7 — Contrato del repositorio

**Historia de usuario:** Como desarrollador, quiero un método de repositorio que
elimine únicamente el registro `ExportPackage`, para respetar la separación de
capas.

#### Criterios de aceptación

1. EL contrato `ExportPackageRepository` DEBERÁ incorporar `delete(id)` y su
   implementación SQL correspondiente.
2. `delete(id)` SOLO DEBERÁ eliminar el registro `ExportPackage`; NO DEBERÁ
   realizar ninguna operación de sistema de archivos.
3. LA semántica de `delete(id)` para un id inexistente DEBERÁ ser un no-op
   idempotente (no lanzar excepción por un id ya ausente).
4. `delete(id)` NO DEBERÁ afectar a otras entidades ni a otros paquetes de
   exportación distintos del id objetivo.

### Requisito 11 — Servicio de aplicación y ruta delgada

**Historia de usuario:** Como desarrollador, quiero que la coordinación de la
eliminación viva en un servicio de aplicación dedicado, para que la ruta HTTP
sea delgada y la lógica sea testeable sin `TestClient`.

#### Criterios de aceptación

1. LA coordinación (carga del paquete, ownership, guard de estado, validación de
   filesystem, `unlink`, `repository.delete`) DEBERÁ residir en un nuevo
   servicio `ExportDeletionService`
   (`src/application/services/export_deletion_service.py`).
2. LA ruta HTTP DEBERÁ limitarse a: autenticación, obtener el repositorio,
   invocar `ExportDeletionService`, y convertir resultados/errores en un
   `RedirectResponse`.
3. `ExportDeletionService` NO DEBERÁ reutilizar el `DeletionService` agrícola
   (Spec 021); la eliminación de `ExportPackage` NO usa outbox, NO sincroniza,
   NO llama a Supabase y NO borra datos remotos.
4. `ExportDeletionService` DEBERÁ aceptar una raíz de exportaciones
   (`exports_root`) inyectable, con valor por defecto en producción
   `outputs/exports`, para permitir su uso con `tmp_path` en tests.
5. EL orden DEBERÁ ser estricto: (1) validar paquete/ownership/estado/path; (2)
   si el ZIP seguro existe, intentar `unlink`; (3) solo después ejecutar
   `repository.delete(id)`. El orden NO DEBERÁ invertirse.

### Requisito 12 — Fallos parciales (sin transacción atómica FS+SQLite)

**Historia de usuario:** Como responsable de la integridad de datos, prefiero un
registro reintentable con ZIP ausente antes que un ZIP huérfano sin registro.

#### Criterios de aceptación

1. SI `unlink` lanza `OSError` ENTONCES el sistema NO DEBERÁ ejecutar
   `repository.delete`, DEBERÁ conservar el registro y DEBERÁ devolver un error
   controlado.
2. SI `unlink` tiene éxito pero `repository.delete` falla ENTONCES el archivo ya
   estará eliminado, el registro DEBERÁ permanecer, y el sistema DEBERÁ devolver
   un error controlado. Este estado es RECUPERABLE: un intento posterior tratará
   el caso como `SAFE_MISSING` y podrá eliminar el registro.
3. EL sistema NO DEBERÁ implementar tombstones ni un protocolo transaccional
   adicional.

### Requisito 8 — Offline / local-first

**Historia de usuario:** Como operario en un invernadero sin Internet, quiero
que eliminar exportaciones funcione completamente offline.

#### Criterios de aceptación

1. LA eliminación de exportaciones DEBERÁ funcionar sin Internet.
2. LA eliminación NO DEBERÁ llamar a Supabase, tocar RLS, usar sync, crear
   outbox ni depender de red.
3. LA implementación NO DEBERÁ introducir CDN, nueva librería frontend, servicio
   externo ni nueva dependencia Python salvo necesidad demostrable; DEBERÁ
   preferir stdlib/`pathlib` y la arquitectura existente.
4. LA UI NO DEBERÁ introducir frameworks JS ni `fetch` solo para borrar; DEBERÁ
   preferir form POST + confirmación local.

### Requisito 9 — Sin cambio de esquema

**Historia de usuario:** Como responsable del proyecto, quiero evitar
migraciones innecesarias, porque `ExportPackage` ya tiene la metadata
requerida.

#### Criterios de aceptación

1. LA implementación NO DEBERÁ requerir cambios de esquema.
2. LA implementación NO DEBERÁ introducir soft-delete por defecto; esta Spec
   busca eliminación real del paquete.
3. SI se detecta una razón real que exigiera migración ENTONCES el trabajo
   DEBERÁ detenerse y reportarse antes de diseñar la migración.

### Requisito 10 — Preservación de funcionalidad existente

**Historia de usuario:** Como operario, quiero que generar, listar, ver detalle
y descargar exportaciones siga funcionando igual.

#### Criterios de aceptación

1. LA vista DEBERÁ conservar: generar exportación ZIP, exportaciones anteriores,
   fecha, estado, `records_count`, `images_count`, detalle y descarga.
2. CADA item de la lista PODRÁ mostrar de forma compacta: fecha, estado, tamaño
   (si está disponible), registros, imágenes y las acciones Detalle, Descargar
   (solo `completed`) y Eliminar, sin saturar una pantalla 800×480 (se permite
   scroll vertical).
3. EL detalle DEBERÁ conservar: status, errores, scope, fechas, registros,
   imágenes, tamaño, manifest, warnings y descarga.
4. `GET /exportar` DEBERÁ leer `?success`/`?error` y el template
   `export_list.html` DEBERÁ renderizarlos con los componentes de alerta
   existentes.
5. `GET /exportar/{id}` DEBERÁ leer `?error`/`?success` y `export_detail.html`
   DEBERÁ renderizarlos si están presentes, haciendo visible el error de
   descarga ya usado (`?error=Archivo+no+disponible`). La semántica de la
   descarga NO DEBERÁ cambiar innecesariamente.
6. NINGÚN cambio de esta Spec DEBERÁ romper los tests existentes de export
   service, export repository ni export routes/UI.
