# Requirements Document

## Introduction

Este documento define los requisitos para mejorar la experiencia de usuario durante el monitoreo en Tomato Monitor. Actualmente la pantalla de ejecución del monitoreo muestra únicamente "Iniciando..." sin retroalimentación sobre lo que ocurre internamente. Esta spec aborda: un registro visual de actividad, vista previa de cámara antes de iniciar, una pantalla de ejecución mejorada con contadores en vivo e imagen capturada, separación en servicios dedicados (CameraService, ModelService, LogService) para mantenibilidad, y una abstracción preparatoria para integración futura de movimiento robótico.

La prioridad es mejorar la experiencia de usuario, la trazabilidad del proceso y la estructura interna — no cambiar el modelo de IA.

## Glossary

- **Monitoring_System**: El sistema de monitoreo de tomates Tomato Monitor ejecutándose en Raspberry Pi 5
- **Activity_Log_Panel**: Panel compacto en la interfaz que muestra entradas de registro con marca de tiempo, nivel y mensaje
- **Log_Entry**: Registro individual con estructura `{timestamp, level, source, message}` almacenado en memoria por sesión
- **Camera_Preview**: Componente de interfaz que muestra un frame JPEG estático de la cámara para verificación visual
- **CameraService**: Servicio de aplicación que encapsula la verificación de disponibilidad, captura de frames y reporte de errores de la cámara
- **ModelService**: Servicio de aplicación que encapsula la carga y verificación del estado del modelo de inferencia
- **LogService**: Servicio de aplicación que gestiona las entradas de registro estructuradas para la interfaz, almacenadas en memoria por sesión de monitoreo
- **RobotMovementService**: Interfaz abstracta (sin implementación) que define el contrato para futuro control de movimiento robótico
- **DecisionService**: Interfaz abstracta (sin implementación) que define el contrato para decisiones basadas en frames (avanzar, pausar, esperar)
- **Preparation_Screen**: Pantalla "Preparar Monitoreo" que reemplaza el formulario actual de configuración, añadiendo vista previa de cámara y estado de servicios
- **Execution_Screen**: Pantalla de ejecución del monitoreo mejorada con registro de actividad, contadores en vivo, última imagen capturada y tiempo transcurrido
- **Farmer**: Usuario principal del sistema que opera la pantalla táctil en el invernadero

## Requirements

### Requirement 1: Activity Log Service

**User Story:** As a Farmer, I want to see what the system is doing during monitoring, so that I can understand the process and identify problems without technical knowledge.

#### Acceptance Criteria

1. THE LogService SHALL store Log_Entry objects in memory with the structure `{timestamp, level, source, message}` where level is one of: `info`, `success`, `warning`, `error`
2. THE LogService SHALL associate all Log_Entry objects with a specific monitoring session identifier
3. WHEN a monitoring session ends or is aborted, THE LogService SHALL retain the Log_Entry objects until the Farmer navigates away from the Execution_Screen
4. THE LogService SHALL accept a maximum of 200 Log_Entry objects per monitoring session and discard the oldest entries when the limit is exceeded
5. THE LogService SHALL provide a method to retrieve all Log_Entry objects for a given monitoring session ordered by timestamp ascending

### Requirement 2: Activity Log Panel UI

**User Story:** As a Farmer, I want to see a visual log of what the system is doing, so that I have confidence the monitoring is progressing correctly.

#### Acceptance Criteria

1. THE Execution_Screen SHALL display the Activity_Log_Panel as a compact scrollable panel with a maximum height of 200 pixels
2. WHEN a new Log_Entry is added, THE Activity_Log_Panel SHALL auto-scroll to display the most recent entry
3. THE Activity_Log_Panel SHALL display each Log_Entry with: a timestamp formatted as `HH:MM:SS`, a color-coded level indicator (blue for info, green for success, amber for warning, red for error), and the message text
4. THE Activity_Log_Panel SHALL render all text in Spanish language
5. WHEN the monitoring is in `initializing` state, THE Activity_Log_Panel SHALL display entries for: camera initialization, model loading start, and model loading completion or failure
6. WHEN the monitoring is in `running` state, THE Activity_Log_Panel SHALL display entries for: each image capture event, each inference result with tomato count, and any warnings
7. IF an error occurs during monitoring, THEN THE Activity_Log_Panel SHALL display the error entry with red color coding and a human-readable message

### Requirement 3: Camera Service

**User Story:** As a Farmer, I want to verify that the camera works before starting a monitoring session, so that I do not waste time on a failed session.

#### Acceptance Criteria

1. THE CameraService SHALL provide a method to check camera availability that returns one of: `available`, `not_detected`, `error`
2. THE CameraService SHALL provide a method to capture a single JPEG frame for preview purposes without starting a continuous capture session
3. WHEN the CameraService captures a preview frame, THE CameraService SHALL release the camera resource immediately after capture
4. IF the camera is not physically connected, THEN THE CameraService SHALL return status `not_detected` within 5 seconds
5. IF an unexpected error occurs during camera check, THEN THE CameraService SHALL return status `error` with a descriptive reason string

### Requirement 4: Camera Preview on Preparation Screen

**User Story:** As a Farmer, I want to see what the camera sees before starting the monitoring, so that I can confirm it is pointing at the right area.

#### Acceptance Criteria

1. THE Preparation_Screen SHALL display the Camera_Preview component showing a single JPEG frame from the camera
2. THE Preparation_Screen SHALL display the camera status as one of: "Cámara activa" (green indicator), "Cámara no detectada" (red indicator), "Imagen no disponible" (gray indicator)
3. THE Preparation_Screen SHALL display a "Actualizar cámara" button that triggers a new camera availability check and frame capture
4. THE Preparation_Screen SHALL disable the "Iniciar Monitoreo" button when camera status is not `available`
5. THE Preparation_Screen SHALL enable the "Iniciar Monitoreo" button only when camera status is `available`
6. THE Preparation_Screen SHALL display the module name, crop type, and configured dimensions alongside the camera preview
7. THE Preparation_Screen SHALL display the model loading status as one of: "Modelo disponible" (green), "Modelo no encontrado" (red)
8. WHEN the Farmer loads the Preparation_Screen, THE Monitoring_System SHALL automatically perform a camera availability check

### Requirement 5: Model Service

**User Story:** As a Farmer, I want the system to verify that the detection model is ready before starting, so that I know the monitoring will produce results.

#### Acceptance Criteria

1. THE ModelService SHALL provide a method to verify that the detection model file exists at the configured path
2. THE ModelService SHALL return a status of `available` when the model file exists and `not_found` when the model file does not exist
3. THE ModelService SHALL not load the model into memory during the verification check to avoid unnecessary resource consumption

### Requirement 6: Enhanced Monitoring Execution Screen

**User Story:** As a Farmer, I want to see live progress during the monitoring, so that I know the system is working and how much it has processed.

#### Acceptance Criteria

1. THE Execution_Screen SHALL display an elapsed time counter formatted as `MM:SS` that updates every second while monitoring status is `running`
2. THE Execution_Screen SHALL display the total snapshots captured counter updated via polling
3. THE Execution_Screen SHALL display the total tomatoes detected counter updated via polling
4. THE Execution_Screen SHALL display a thumbnail of the last captured snapshot image updated via polling when a new snapshot is available
5. THE Execution_Screen SHALL display the Activity_Log_Panel with real-time log entries
6. THE Execution_Screen SHALL display a "Detener Monitoreo" button that requires confirmation before aborting
7. WHEN the monitoring status transitions to `completed`, THE Execution_Screen SHALL automatically navigate to the monitoring report screen
8. WHEN the temperature reported by the thermal monitor exceeds 70°C, THE Execution_Screen SHALL display a warning banner with the current temperature value in Spanish

### Requirement 7: Log Events During Monitoring Lifecycle

**User Story:** As a Farmer, I want the activity log to reflect every important step of the monitoring process, so that I have full traceability.

#### Acceptance Criteria

1. WHEN the monitoring session starts initialization, THE Monitoring_System SHALL emit an info Log_Entry with message "Inicializando cámara..."
2. WHEN the camera is successfully initialized, THE Monitoring_System SHALL emit a success Log_Entry with message "Cámara detectada correctamente."
3. WHEN the model begins loading, THE Monitoring_System SHALL emit an info Log_Entry with message "Cargando modelo de detección..."
4. WHEN the model finishes loading successfully, THE Monitoring_System SHALL emit a success Log_Entry with message "Modelo cargado correctamente."
5. WHEN the monitoring transitions to running state, THE Monitoring_System SHALL emit an info Log_Entry with message "Monitoreo iniciado."
6. WHEN a snapshot is captured, THE Monitoring_System SHALL emit an info Log_Entry with the message "Capturando imagen... (snapshot N)" where N is the snapshot sequence number
7. WHEN inference completes on a snapshot, THE Monitoring_System SHALL emit a success Log_Entry with the message "Tomates detectados: X" where X is the detection count for that snapshot
8. WHEN the monitoring completes successfully, THE Monitoring_System SHALL emit a success Log_Entry with message "Monitoreo finalizado correctamente."
9. IF the camera becomes unavailable during monitoring, THEN THE Monitoring_System SHALL emit an error Log_Entry with message "No se pudo acceder a la cámara."
10. IF the model fails to load, THEN THE Monitoring_System SHALL emit an error Log_Entry with message "No se pudo cargar el modelo de detección."
11. IF the thermal monitor reports temperature above 70°C, THEN THE Monitoring_System SHALL emit a warning Log_Entry with message "Temperatura elevada (X°C)." where X is the temperature value

### Requirement 8: API Endpoint for Camera Preview

**User Story:** As a Farmer, I want the camera preview to load quickly on the preparation screen, so that I can verify camera alignment without delay.

#### Acceptance Criteria

1. THE Monitoring_System SHALL expose a GET endpoint that returns a single JPEG frame from the camera with content-type `image/jpeg`
2. THE Monitoring_System SHALL expose a GET endpoint that returns the current camera availability status as a JSON response with field `status` containing one of: `available`, `not_detected`, `error`
3. IF the camera is not available when the preview endpoint is called, THEN THE Monitoring_System SHALL return HTTP 503 with a JSON error message in Spanish

### Requirement 9: API Endpoint for Activity Log

**User Story:** As a Farmer, I want the activity log to update in real time during monitoring, so that I can follow the system progress.

#### Acceptance Criteria

1. THE Monitoring_System SHALL expose a GET endpoint that returns all Log_Entry objects for a given monitoring session as a JSON array
2. THE Monitoring_System SHALL support an optional `since` query parameter (ISO 8601 timestamp) to return only Log_Entry objects created after the specified time
3. THE Execution_Screen SHALL poll the activity log endpoint every 2 seconds to fetch new entries

### Requirement 10: API Endpoint for Last Snapshot Thumbnail

**User Story:** As a Farmer, I want to see the most recent image captured during monitoring, so that I can visually verify the camera is capturing relevant content.

#### Acceptance Criteria

1. THE Monitoring_System SHALL expose a GET endpoint that returns the most recently captured snapshot image for a given monitoring session as JPEG with content-type `image/jpeg`
2. IF no snapshots have been captured yet, THEN THE Monitoring_System SHALL return HTTP 404 with a JSON message indicating no snapshots are available
3. THE Execution_Screen SHALL display a placeholder image when no snapshot has been captured yet and replace the placeholder with the actual thumbnail when the first snapshot becomes available

### Requirement 11: Service Layer Separation

**User Story:** As a Thesis_Team member, I want the monitoring system organized into dedicated services, so that each component has a single responsibility and the codebase is maintainable.

#### Acceptance Criteria

1. THE CameraService SHALL encapsulate all camera interaction logic including availability check, single frame capture, and error reporting without exposing FrameSource internals to callers
2. THE ModelService SHALL encapsulate model file verification logic without loading the model into memory
3. THE LogService SHALL encapsulate log entry creation, storage, retrieval, and lifecycle management independent of the monitoring worker
4. THE CameraService, ModelService, and LogService SHALL be instantiable via the existing manual dependency injection pattern in `app/dependencies.py`
5. THE CameraService, ModelService, and LogService SHALL reside in the `src/application/services/` directory following existing project conventions

### Requirement 12: Robot Movement Service Abstraction

**User Story:** As a Thesis_Team member, I want an abstract interface for robot movement control, so that the system is prepared for future motor integration without coupling to specific hardware.

#### Acceptance Criteria

1. THE RobotMovementService SHALL be defined as a Python abstract class (ABC) with methods: `advance()`, `pause()`, `stop()`, and `get_position()` returning type stubs
2. THE DecisionService SHALL be defined as a Python abstract class (ABC) with a method: `decide(frame_context)` returning one of: `advance`, `pause`, `wait`
3. THE RobotMovementService and DecisionService SHALL reside in `src/domain/interfaces/` following the ports pattern
4. THE RobotMovementService and DecisionService SHALL contain no implementation logic — only abstract method signatures with docstrings describing the expected behavior

### Requirement 13: Preparation Screen Replaces Current Setup

**User Story:** As a Farmer, I want a single preparation screen that shows me camera status, model status, dimensions form, and module info, so that I can prepare everything in one place before starting.

#### Acceptance Criteria

1. THE Preparation_Screen SHALL replace the existing monitoring setup form screen at the same URL route
2. THE Preparation_Screen SHALL include the dimension inputs (width and length in meters) and optional notes field from the current setup form
3. THE Preparation_Screen SHALL preserve the existing form validation behavior requiring width and length values greater than zero
4. THE Preparation_Screen SHALL pre-fill width and length values from the module entity when available
5. THE Preparation_Screen SHALL maintain backward compatibility with the existing POST endpoint for starting a monitoring session

### Requirement 14: Performance Constraints

**User Story:** As a Thesis_Team member, I want the UX enhancements to operate within the resource constraints of the Raspberry Pi 5, so that inference and capture performance are not degraded.

#### Acceptance Criteria

1. THE LogService SHALL store Log_Entry objects exclusively in memory without writing to the SQLite database
2. THE Camera_Preview SHALL serve a single static JPEG frame per request without maintaining a continuous video stream
3. THE Activity_Log_Panel polling SHALL not exceed one HTTP request every 2 seconds to the log endpoint
4. THE Execution_Screen polling for status, log entries, and last snapshot SHALL be consolidated into a maximum of 3 HTTP requests per polling cycle (one for status, one for logs, one for thumbnail)
5. THE CameraService preview capture SHALL release the camera resource within 3 seconds of completing the capture to avoid blocking the monitoring start
