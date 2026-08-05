# Requirements Document

## Introduction

Este documento define los requisitos para implementar la capa de persistencia SQLite con SQLAlchemy ORM siguiendo principios de Clean Architecture. El modelo de datos soporta la jerarquía agrícola (Invernadero → Módulo → Monitoreo → Snapshot → ResultadoInspección → MétricasMonitoreo) que constituye la base para el flujo de monitoreo en tiempo real del sistema Tomato Monitor.

## Glossary

- **Sistema_Persistencia**: Capa de infraestructura responsable de almacenar y recuperar entidades del dominio utilizando SQLite y SQLAlchemy ORM.
- **Capa_Dominio**: Capa arquitectónica que contiene entidades, value objects e interfaces de repositorio, sin dependencias de frameworks externos.
- **Capa_Infraestructura**: Capa arquitectónica que implementa las interfaces de dominio con tecnologías concretas (SQLAlchemy, SQLite).
- **Repositorio**: Interfaz abstracta definida en la capa de dominio que establece el contrato para operaciones de persistencia.
- **Implementación_Repositorio**: Clase concreta en la capa de infraestructura que implementa la interfaz de repositorio usando SQLAlchemy.
- **Invernadero**: Entidad de dominio que representa la infraestructura física del invernadero.
- **Módulo**: Entidad de dominio que representa un área de cultivo rectangular dentro de un invernadero.
- **Monitoreo**: Entidad de dominio que representa una sesión de recorrido del robot con captura de imágenes e inferencia.
- **Snapshot**: Entidad de dominio que representa una imagen capturada durante un monitoreo cuando el detector de cambios se activó.
- **Resultado_Inspección**: Entidad de dominio que representa un tomate detectado dentro de un snapshot con sus evaluaciones de salud y madurez.
- **Métricas_Monitoreo**: Entidad de dominio que almacena métricas agregadas pre-computadas para un monitoreo completado.
- **Máquina_Estados**: Conjunto de transiciones válidas para el campo status de un Monitoreo (initializing → running → paused → finishing → completed/aborted/error).
- **Eliminación_Cascada**: Comportamiento donde la eliminación de una entidad padre elimina automáticamente sus entidades hijas dependientes.
- **Modo_WAL**: Write-Ahead Logging de SQLite que permite lecturas concurrentes seguras durante escrituras.
- **Sesión_BD**: Instancia de SQLAlchemy Session que gestiona una unidad de trabajo contra la base de datos.

## Requirements

### Requirement 1: Entidades de dominio puras

**User Story:** Como desarrollador, quiero que las entidades de dominio estén definidas sin dependencias de SQLAlchemy, para que la lógica de negocio permanezca desacoplada del framework de persistencia.

#### Acceptance Criteria

1. THE Capa_Dominio SHALL definir las entidades Invernadero, Módulo, Monitoreo, Snapshot, Resultado_Inspección y Métricas_Monitoreo como dataclasses o clases Python puras en `src/domain/entities/`, donde cada entidad expone atributos de instancia con type hints que coinciden en nombre, tipo Python y restricciones de nulabilidad con la definición correspondiente en el steering file `data-model.md`.
2. THE Capa_Dominio SHALL definir el value object BoundingBox (x1: int, y1: int, x2: int, y2: int) como instancia inmutable (frozen dataclass o equivalente que lance excepción ante asignación de atributos tras construcción) y el value object MonitoringStatus con los estados válidos: initializing, running, paused, finishing, completed, aborted, error, y las transiciones permitidas: initializing→running, running→finishing, running→paused, running→aborted, paused→running, paused→aborted, finishing→completed, y cualquier estado→error.
3. IF se intenta una transición de MonitoringStatus no incluida en el conjunto de transiciones permitidas, THEN THE Capa_Dominio SHALL rechazar la operación lanzando una excepción que indique el estado actual y el estado destino inválido.
4. THE Capa_Dominio SHALL utilizar exclusivamente tipos nativos de Python (str, int, float, bool, datetime, Optional, List), dataclasses del módulo estándar `dataclasses`, o Enum del módulo estándar `enum`, sin importar SQLAlchemy, FastAPI, PyTorch ni OpenCV en ningún archivo dentro de `src/domain/`.

### Requirement 2: Interfaces de repositorio

**User Story:** Como desarrollador, quiero interfaces de repositorio definidas en la capa de dominio, para que la capa de aplicación pueda operar sin conocer la tecnología de persistencia subyacente.

#### Acceptance Criteria

1. THE Capa_Dominio SHALL definir una interfaz GreenhouseRepository como clase abstracta (ABC) con los métodos: create(greenhouse) retornando el Invernadero creado con id asignado, get_all() retornando una lista de Invernaderos (lista vacía si no hay registros), get_by_id(id) retornando Optional[Greenhouse] (None si no existe), update(id, name, location) retornando el Invernadero actualizado, y delete(id) eliminando el Invernadero indicado.
2. THE Capa_Dominio SHALL definir una interfaz ModuleRepository como clase abstracta (ABC) con los métodos: create(greenhouse_id, module) retornando el Módulo creado con id asignado, get_by_greenhouse(greenhouse_id) retornando una lista de Módulos del invernadero indicado (lista vacía si no hay registros), get_by_id(id) retornando Optional[Module] (None si no existe), update(id, fields) retornando el Módulo actualizado, y delete(id) eliminando el Módulo indicado.
3. THE Capa_Dominio SHALL definir una interfaz MonitoringRepository como clase abstracta (ABC) con los métodos: create(module_id, monitoring) retornando el Monitoreo creado con id asignado, get_by_module(module_id) retornando una lista de Monitoreos del módulo indicado (lista vacía si no hay registros), get_by_id(id) retornando Optional[Monitoring] (None si no existe), update_status(id, status) actualizando el estado del Monitoreo, update_counters(id, total_snapshots, total_detections) actualizando los contadores, y delete(id) eliminando el Monitoreo indicado.
4. THE Capa_Dominio SHALL definir una interfaz SnapshotRepository como clase abstracta (ABC) con los métodos: create(monitoring_id, snapshot) retornando el Snapshot creado con id asignado, get_by_monitoring(monitoring_id) retornando una lista de Snapshots del monitoreo indicado (lista vacía si no hay registros), y get_by_id(id) retornando Optional[Snapshot] (None si no existe).
5. THE Capa_Dominio SHALL definir una interfaz InspectionResultRepository como clase abstracta (ABC) con los métodos: create(snapshot_id, result) retornando el InspectionResult creado con id asignado, get_by_snapshot(snapshot_id) retornando una lista de resultados del snapshot indicado (lista vacía si no hay registros), y get_by_monitoring(monitoring_id) retornando una lista de resultados agregados de todos los snapshots del monitoreo (lista vacía si no hay registros).
6. THE Capa_Dominio SHALL definir una interfaz MonitoringMetricsRepository como clase abstracta (ABC) con los métodos: create(monitoring_id, metrics) retornando el MonitoringMetrics creado con id asignado, y get_by_monitoring(monitoring_id) retornando Optional[MonitoringMetrics] (None si no existe).
7. THE Capa_Dominio SHALL definir todas las interfaces de repositorio exclusivamente con imports de la biblioteca estándar de Python y de entidades/value objects del propio dominio, sin importar SQLAlchemy, FastAPI, PyTorch ni OpenCV.
8. IF un método get_by_id o get_by_monitoring recibe un identificador que no corresponde a ningún registro existente, THEN THE interfaz de repositorio SHALL especificar en su contrato que el método retorna None en lugar de lanzar una excepción.
9. IF se invoca delete en GreenhouseRepository, ModuleRepository o MonitoringRepository, THEN THE interfaz de repositorio SHALL especificar en su contrato que la eliminación aplica en cascada a todas las entidades hijas según la jerarquía Greenhouse → Module → Monitoring → Snapshot → InspectionResult → MonitoringMetrics.

### Requirement 3: Modelos SQLAlchemy

**User Story:** Como desarrollador, quiero modelos SQLAlchemy declarativos que mapeen las entidades de dominio a tablas SQLite, para que la persistencia sea gestionada por el ORM.

#### Acceptance Criteria

1. THE Capa_Infraestructura SHALL definir modelos SQLAlchemy declarativos para las entidades Greenhouse, Module, Monitoring, Snapshot, InspectionResult y MonitoringMetrics en `src/infrastructure/persistence/models/`.
2. THE Capa_Infraestructura SHALL mapear cada modelo a una tabla SQLite con los campos, tipos y restricciones definidos en el steering file `data-model.md`.
3. THE modelo Module SHALL aplicar una restricción de unicidad compuesta (UniqueConstraint) sobre la combinación de columnas (greenhouse_id, name).
4. THE modelo MonitoringMetrics SHALL aplicar una restricción UNIQUE sobre la columna monitoring_id para garantizar la relación uno-a-uno con Monitoring.
5. THE Capa_Infraestructura SHALL configurar relaciones SQLAlchemy con cascade="all, delete-orphan" en las cinco relaciones padre-hijo de la jerarquía: Greenhouse→Module, Module→Monitoring, Monitoring→Snapshot, Snapshot→InspectionResult, y Monitoring→MonitoringMetrics.
6. THE Capa_Infraestructura SHALL almacenar las columnas created_at, updated_at, started_at, completed_at, captured_at y computed_at como DateTime UTC sin información de zona horaria (timezone-naive).
7. IF se intenta crear un Module con una combinación (greenhouse_id, name) que ya existe, THEN THE Capa_Infraestructura SHALL rechazar la operación elevando un error de integridad de base de datos.
8. IF se elimina un Greenhouse, THEN THE Capa_Infraestructura SHALL eliminar en cascada todos los Module, Monitoring, Snapshot, InspectionResult y MonitoringMetrics asociados en la jerarquía sin dejar registros huérfanos.

### Requirement 4: Implementación de repositorios

**User Story:** Como desarrollador, quiero implementaciones concretas de los repositorios usando SQLAlchemy, para que las operaciones CRUD funcionen contra la base de datos SQLite.

#### Acceptance Criteria

1. THE Implementación_Repositorio SHALL implementar una clase de repositorio por cada entidad del modelo de datos (Greenhouse, Module, Monitoring, Snapshot, InspectionResult, MonitoringMetrics) usando SQLAlchemy Session para todas las operaciones de lectura y escritura.
2. THE Implementación_Repositorio SHALL residir en `src/infrastructure/persistence/repositories/`.
3. WHEN se elimina un Invernadero, THE Implementación_Repositorio SHALL eliminar en cascada todos los Módulos, Monitoreos, Snapshots, Resultados de Inspección y Métricas asociados en una única transacción.
4. WHEN se elimina un Módulo, THE Implementación_Repositorio SHALL eliminar en cascada todos los Monitoreos, Snapshots, Resultados de Inspección y Métricas asociados en una única transacción.
5. WHEN se elimina un Monitoreo, THE Implementación_Repositorio SHALL eliminar en cascada todos los Snapshots, Resultados de Inspección y Métricas asociados en una única transacción.
6. IF se intenta crear un Módulo con un nombre que ya existe para el mismo invernadero, THEN THE Implementación_Repositorio SHALL rechazar la operación lanzando una excepción que indique el nombre duplicado y el invernadero afectado, sin modificar el estado de la base de datos.
7. WHEN se crea un Monitoreo, THE Implementación_Repositorio SHALL asignar el status inicial "initializing" y registrar el timestamp UTC actual como `started_at`.
8. IF se solicita una entidad por ID y no existe un registro con ese identificador, THEN THE Implementación_Repositorio SHALL retornar None sin lanzar excepción.
9. WHEN se consultan entidades hijas por ID del padre (módulos por invernadero, monitoreos por módulo, snapshots por monitoreo), THE Implementación_Repositorio SHALL retornar una lista vacía si no existen registros asociados, ordenada por fecha de creación descendente.

### Requirement 5: Máquina de estados del monitoreo

**User Story:** Como desarrollador, quiero que las transiciones de estado del monitoreo estén validadas, para que el sistema garantice consistencia en el ciclo de vida de una sesión de monitoreo.

#### Acceptance Criteria

1. THE Capa_Dominio SHALL definir las transiciones válidas de la Máquina_Estados: initializing→running, running→paused, running→finishing, running→aborted, paused→running, paused→aborted, finishing→completed, y desde cualquier estado no terminal (initializing, running, paused, finishing)→error.
2. IF se solicita una transición de estado no válida según la Máquina_Estados, THEN THE Capa_Dominio SHALL rechazar la operación y retornar un error que indique el estado actual, el estado destino solicitado y la lista de transiciones permitidas desde el estado actual.
3. IF un Monitoreo se encuentra en estado "completed" o "aborted" o "error", THEN THE Capa_Dominio SHALL rechazar cualquier solicitud de transición de estado para ese monitoreo.
4. WHEN un Monitoreo transiciona a "completed" o "aborted", THE Capa_Dominio SHALL registrar el timestamp de finalización (completed_at) en UTC.
5. WHILE un Monitoreo tiene status "initializing", "running", "paused", "finishing" o "error", THE Sistema_Persistencia SHALL rechazar la creación de Métricas_Monitoreo para ese monitoreo.
6. WHEN un Monitoreo transiciona a "completed" o "aborted", THE Sistema_Persistencia SHALL permitir la creación de exactamente un registro de Métricas_Monitoreo asociado a ese monitoreo.

### Requirement 6: Gestión de la conexión a base de datos

**User Story:** Como desarrollador, quiero una gestión centralizada de la conexión SQLite, para que el sistema configure correctamente el motor de base de datos y las sesiones.

#### Acceptance Criteria

1. THE Sistema_Persistencia SHALL crear el motor SQLAlchemy apuntando al archivo `data/tomato_monitor.db` usando el driver SQLite.
2. WHEN se establece una conexión a la base de datos, THE Sistema_Persistencia SHALL ejecutar `PRAGMA foreign_keys = ON` y `PRAGMA journal_mode = WAL` para habilitar integridad referencial y modo Write-Ahead Logging.
3. THE Sistema_Persistencia SHALL proveer una fábrica de sesiones (SessionLocal) que genere instancias de Sesión_BD con autocommit deshabilitado y autoflush deshabilitado, de modo que cada unidad de trabajo requiera commit explícito.
4. WHEN se inicializa la conexión y el directorio `data/` no existe, THE Sistema_Persistencia SHALL crearlo incluyendo directorios padres necesarios.
5. IF el archivo de base de datos no existe al iniciar, THEN THE Sistema_Persistencia SHALL crearlo automáticamente al establecer la conexión.
6. IF la creación del directorio `data/` o del archivo de base de datos falla por permisos insuficientes o espacio en disco agotado, THEN THE Sistema_Persistencia SHALL lanzar un error indicando la causa del fallo sin iniciar la aplicación.

### Requirement 7: Creación idempotente del esquema

**User Story:** Como desarrollador, quiero que la creación del esquema sea idempotente, para que pueda ejecutarse múltiples veces sin errores ni pérdida de datos.

#### Acceptance Criteria

1. WHEN la aplicación se inicializa, THE Sistema_Persistencia SHALL ejecutar la creación de todas las tablas del modelo usando SQLAlchemy `create_all()` con la opción `checkfirst=True`.
2. WHEN las tablas ya existen en la base de datos, THE Sistema_Persistencia SHALL omitir su creación sin generar errores, sin modificar datos existentes y sin eliminar registros previos.
3. THE Sistema_Persistencia SHALL crear las tablas respetando el orden de dependencias por claves foráneas definido en el modelo declarativo de SQLAlchemy.
4. IF la creación del esquema falla por base de datos corrupta o archivo bloqueado, THEN THE Sistema_Persistencia SHALL lanzar un error indicando que el esquema no pudo ser creado, sin dejar tablas parcialmente creadas.

### Requirement 8: Integridad referencial y protección contra huérfanos

**User Story:** Como desarrollador, quiero que la base de datos garantice la integridad referencial, para que no existan registros huérfanos que corrompan la consistencia de los datos.

#### Acceptance Criteria

1. THE Sistema_Persistencia SHALL configurar claves foráneas con restricción NOT NULL para las relaciones obligatorias: Module→Greenhouse, Monitoring→Module, Snapshot→Monitoring, InspectionResult→Snapshot, MonitoringMetrics→Monitoring.
2. WHEN se establece cada conexión a la base de datos, THE Sistema_Persistencia SHALL ejecutar `PRAGMA foreign_keys = ON` para habilitar la verificación de claves foráneas de SQLite.
3. IF se intenta crear un registro hijo (Snapshot, InspectionResult, MonitoringMetrics, Monitoring o Module) con un identificador de padre inexistente o nulo, THEN THE Sistema_Persistencia SHALL rechazar la operación lanzando un error de integridad sin modificar la base de datos.
4. THE Sistema_Persistencia SHALL configurar eliminación en cascada (CASCADE) en las relaciones Greenhouse→Module→Monitoring→Snapshot→InspectionResult y Monitoring→MonitoringMetrics, de modo que al eliminar un registro padre se eliminen automáticamente todos sus registros dependientes.
5. IF se intenta insertar un valor de `monitoring_id`, `snapshot_id`, `greenhouse_id` o `module_id` que no corresponde a un registro existente en la tabla referenciada, THEN THE Sistema_Persistencia SHALL rechazar la operación con un error de integridad referencial.

### Requirement 9: Almacenamiento de rutas de imagen

**User Story:** Como desarrollador, quiero que las rutas de imagen se almacenen como rutas relativas en la base de datos, para que el sistema sea portable entre instalaciones.

#### Acceptance Criteria

1. THE Sistema_Persistencia SHALL almacenar las rutas de imagen (image_path en Snapshot) como rutas relativas desde la raíz del proyecto, sin incluir prefijos absolutos dependientes del sistema operativo.
2. THE Sistema_Persistencia SHALL seguir el patrón `outputs/monitorings/{monitoring_id}/snapshots/snapshot_{frame_index}.jpg` para las rutas de snapshots, donde `monitoring_id` corresponde al identificador entero del monitoreo y `frame_index` al índice secuencial del frame (base 0, sin padding).
3. THE Sistema_Persistencia SHALL almacenar exclusivamente rutas de archivo en la base de datos (columna image_path de tipo String con longitud máxima de 500 caracteres), sin almacenar datos binarios de imagen.
4. IF el Sistema_Persistencia recibe una ruta de imagen que excede 500 caracteres o contiene secuencias de navegación relativa (`../`), THEN THE Sistema_Persistencia SHALL rechazar el almacenamiento e indicar un error de validación de ruta.

### Requirement 10: Operación exclusivamente local

**User Story:** Como agricultor, quiero que el sistema funcione completamente sin conexión a internet, para poder monitorear mis cultivos sin depender de servicios externos.

#### Acceptance Criteria

1. THE Sistema_Persistencia SHALL utilizar exclusivamente SQLite como motor de base de datos, sin configurar conexiones a bases de datos remotas en la cadena de conexión de SQLAlchemy.
2. THE Sistema_Persistencia SHALL completar todas las operaciones de lectura y escritura sin realizar conexiones de red salientes, de modo que funcione completamente sin conexión a internet.
3. THE Sistema_Persistencia SHALL almacenar el archivo de base de datos en una ubicación local configurable mediante variable de entorno o archivo de configuración, con valor por defecto `data/tomato_monitor.db` relativo a la raíz del proyecto.
4. IF la ruta configurada para el archivo de base de datos no existe o no tiene permisos de escritura, THEN THE Sistema_Persistencia SHALL indicar un error descriptivo al iniciar, sin crear directorios fuera de la raíz del proyecto.
5. THE Sistema_Persistencia SHALL excluir el archivo de base de datos del control de versiones mediante una entrada en `.gitignore` que cubra el patrón `data/*.db`.

### Requirement 11: Compatibilidad con Raspberry Pi 5

**User Story:** Como desarrollador, quiero que la capa de persistencia sea compatible con Raspberry Pi 5, para que el sistema funcione en el hardware objetivo sin degradación.

#### Acceptance Criteria

1. THE Sistema_Persistencia SHALL ser compatible con la arquitectura ARM64 (aarch64) de Raspberry Pi 5, ejecutándose sin errores de importación ni de ejecución sobre Python 3.10+ en Raspberry Pi OS / Debian Bookworm 64-bit.
2. THE Sistema_Persistencia SHALL utilizar SQLAlchemy como única dependencia ORM adicional, sin requerir librerías nativas compiladas que no estén disponibles como wheel para ARM64 o incluidas en la biblioteca estándar de Python.
3. THE Sistema_Persistencia SHALL completar operaciones individuales de escritura (INSERT de un Snapshot con sus InspectionResults) en un tiempo no superior a 200 ms y operaciones de lectura (SELECT de un Monitoring con sus Snapshots) en un tiempo no superior a 500 ms, medidos en Raspberry Pi 5 con 8 GB de RAM compartida y ejecución CPU-only.
