# Requirements Document

## Introduction

Redesign of the farmer-facing web interface for the Tomato Monitor system. The interface is rendered via FastAPI + Jinja2 templates and displayed on a Raspberry Pi DSI 7" touchscreen (800×480, landscape). The redesign introduces six screens covering the complete workflow: greenhouse management, module management, monitoring execution, and report visualization. All text is in Spanish using simple agricultural terminology. The interface is optimized for touch interaction in greenhouse conditions (humidity, gloves, varying light) by a farmer with basic computer literacy.

## Glossary

- **UI**: The FastAPI + Jinja2 web interface rendered on the Raspberry Pi DSI 7" touchscreen
- **Farmer**: The primary user who operates the touchscreen in the greenhouse
- **Greenhouse_List_Screen**: Screen 1 — displays all registered greenhouses as cards
- **Greenhouse_Detail_Screen**: Screen 2 — displays all modules within a selected greenhouse
- **Module_Detail_Screen**: Screen 3 — displays module information and monitoring history
- **Monitoring_Setup_Screen**: Screen 4 — form for configuring a new monitoring session
- **Monitoring_Execution_Screen**: Screen 5 — live status display during robot traversal
- **Monitoring_Report_Screen**: Screen 6 — aggregated metrics and snapshot gallery for a completed monitoring
- **Touch_Target**: Any interactive element the farmer can tap on the touchscreen
- **Primary_Action_Button**: The main action button on a screen, visually prominent and large
- **Empty_State**: The visual state shown when a list contains no items
- **Live_Counter**: A numeric value updated via polling during monitoring execution
- **Metric_Card**: A large visual element displaying a single agricultural metric prominently
- **Maturity_Bar**: A horizontal color-coded bar representing USDA maturity stage distribution
- **Snapshot_Gallery**: A grid of thumbnail images captured during a monitoring session
- **USDA_Maturity_Stage**: One of six ripeness levels: green, breaker, turning, pink, light_red, red

## Requirements

### Requirement 1: Greenhouse List Screen

**User Story:** As a farmer, I want to see all my greenhouses on the home screen, so that I can select which one to work with.

#### Acceptance Criteria

1. WHEN the Farmer navigates to the home screen, THE Greenhouse_List_Screen SHALL display a header with the text "Mis Invernaderos"
2. WHEN greenhouses exist in the database, THE Greenhouse_List_Screen SHALL display one card per greenhouse showing the greenhouse name, module count, and date of the last monitoring
3. WHEN no greenhouses exist in the database, THE Greenhouse_List_Screen SHALL display an Empty_State with the message "No hay invernaderos registrados. Crea el primero." and a "Crear Invernadero" button
4. WHEN the Farmer taps a greenhouse card, THE UI SHALL navigate to the Greenhouse_Detail_Screen for that greenhouse
5. WHEN the Farmer taps the "+" button, THE UI SHALL initiate the greenhouse creation flow

### Requirement 2: Greenhouse Detail Screen

**User Story:** As a farmer, I want to see all modules in a greenhouse, so that I can select the module to monitor.

#### Acceptance Criteria

1. WHEN the Farmer navigates to a greenhouse detail, THE Greenhouse_Detail_Screen SHALL display the greenhouse name as a header with edit and delete action icons
2. WHEN modules exist for the greenhouse, THE Greenhouse_Detail_Screen SHALL display one card per module showing crop type, dimensions (if configured), and date of the last monitoring
3. WHEN no modules exist for the greenhouse, THE Greenhouse_Detail_Screen SHALL display an Empty_State with the message "Este invernadero no tiene módulos. Agrega el primero." and an "Agregar Módulo" button
4. WHEN the Farmer taps a module card, THE UI SHALL navigate to the Module_Detail_Screen for that module
5. WHEN the Farmer taps the "+" button or "Agregar Módulo" button, THE UI SHALL initiate the module creation flow
6. WHEN the Farmer taps the delete icon on the greenhouse header, THE UI SHALL display a confirmation dialog before deleting the greenhouse

### Requirement 3: Module Detail Screen

**User Story:** As a farmer, I want to see module information and past monitoring results, so that I can start a new monitoring or review history.

#### Acceptance Criteria

1. WHEN the Farmer navigates to a module detail, THE Module_Detail_Screen SHALL display the module name as a header with edit and delete action icons
2. THE Module_Detail_Screen SHALL display an info panel showing crop type and dimensions (or "No configuradas" if dimensions are not set)
3. THE Module_Detail_Screen SHALL display a large Primary_Action_Button with the text "Iniciar Nuevo Monitoreo"
4. WHEN monitoring history exists for the module, THE Module_Detail_Screen SHALL display a list of past monitorings showing date, time, tomato count, and percentage of healthy tomatoes
5. WHEN no monitoring history exists, THE Module_Detail_Screen SHALL display the message "No hay monitoreos registrados. Inicia el primero."
6. WHEN the Farmer taps the "Iniciar Nuevo Monitoreo" button, THE UI SHALL navigate to the Monitoring_Setup_Screen
7. WHEN the Farmer taps a monitoring entry in the history list, THE UI SHALL navigate to the Monitoring_Report_Screen for that monitoring

### Requirement 4: Monitoring Setup Screen

**User Story:** As a farmer, I want to confirm module dimensions and add notes before starting a monitoring, so that the session is properly configured.

#### Acceptance Criteria

1. WHEN the Farmer navigates to monitoring setup, THE Monitoring_Setup_Screen SHALL display a header with "Nuevo Monitoreo — {Module Name}"
2. THE Monitoring_Setup_Screen SHALL display numeric input fields for width (meters) and length (meters), pre-filled with the module's saved dimensions if available
3. THE Monitoring_Setup_Screen SHALL display an optional notes text area
4. WHEN the Farmer taps "Continuar" with valid dimensions (both greater than zero), THE UI SHALL start the monitoring session and navigate to the Monitoring_Execution_Screen
5. WHEN the Farmer taps "Continuar" with invalid dimensions (zero or negative), THE UI SHALL display a validation error message without navigating
6. WHEN the Farmer taps "Cancelar", THE UI SHALL navigate back to the Module_Detail_Screen without starting a monitoring
7. THE Monitoring_Setup_Screen SHALL auto-save entered dimension values to the module record so the Farmer does not re-enter them next time

### Requirement 5: Monitoring Execution Screen

**User Story:** As a farmer, I want to see live progress of the monitoring, so that I know the robot is working and can stop it if needed.

#### Acceptance Criteria

1. WHEN monitoring is in progress, THE Monitoring_Execution_Screen SHALL display a header with "Monitoreando — {Module Name}"
2. WHILE monitoring status is "initializing", THE Monitoring_Execution_Screen SHALL display a spinner with the text "Iniciando..."
3. WHILE monitoring status is "running", THE Monitoring_Execution_Screen SHALL display Live_Counters for snapshots captured and tomatoes detected
4. WHILE monitoring status is "running", THE Monitoring_Execution_Screen SHALL display a "Detener Monitoreo" button
5. WHEN the Farmer taps "Detener Monitoreo", THE UI SHALL display a confirmation dialog before aborting the monitoring
6. WHILE monitoring status is "paused", THE Monitoring_Execution_Screen SHALL display an amber warning banner with the pause reason
7. IF the system reports a temperature above the configured threshold, THEN THE Monitoring_Execution_Screen SHALL display a warning banner with text "Temperatura alta: {temp}°C — El sistema puede pausarse"
8. WHILE monitoring status is "finishing", THE Monitoring_Execution_Screen SHALL display a progress indicator with the text "Finalizando..."
9. WHEN monitoring status transitions to "completed", THE UI SHALL automatically navigate to the Monitoring_Report_Screen
10. THE Monitoring_Execution_Screen SHALL poll the monitoring status endpoint at a regular interval using vanilla JavaScript to update the Live_Counters and detect state transitions

### Requirement 6: Monitoring Report Screen

**User Story:** As a farmer, I want to see the results of a monitoring session, so that I can understand the health and maturity of my tomatoes.

#### Acceptance Criteria

1. WHEN the Farmer views a monitoring report, THE Monitoring_Report_Screen SHALL display a header with "{Module Name} — {Date} {Time}"
2. THE Monitoring_Report_Screen SHALL display Metric_Cards showing total tomatoes detected, count and percentage of healthy tomatoes, and count and percentage of unhealthy tomatoes
3. THE Monitoring_Report_Screen SHALL display a Maturity_Bar showing the distribution across all six USDA_Maturity_Stages using a color gradient from green to red
4. WHEN snapshots with detections exist for the monitoring, THE Monitoring_Report_Screen SHALL display a Snapshot_Gallery with thumbnail images
5. WHEN a snapshot image file has been deleted from storage, THE Monitoring_Report_Screen SHALL display a placeholder instead of the missing thumbnail
6. WHEN no tomatoes were detected in the monitoring, THE Monitoring_Report_Screen SHALL display the message "No se detectaron tomates en este monitoreo." instead of metric cards
7. THE Monitoring_Report_Screen SHALL display a "Volver al Módulo" button that navigates back to the Module_Detail_Screen

### Requirement 7: Touch Target Sizing

**User Story:** As a farmer wearing gloves in a greenhouse, I want all interactive elements to be large enough to tap accurately, so that I can operate the system without frustration.

#### Acceptance Criteria

1. THE UI SHALL render all Touch_Targets with a minimum size of 44×44 pixels
2. THE UI SHALL render all Primary_Action_Buttons with a minimum size of 60×48 pixels
3. THE UI SHALL render all list item rows (greenhouse cards, module cards, monitoring entries) with a minimum height of 56 pixels and full available width
4. THE UI SHALL maintain a minimum spacing of 8 pixels between adjacent interactive elements

### Requirement 8: Typography and Readability

**User Story:** As a farmer viewing the screen from varying distances, I want text to be large and clear, so that I can read information without straining.

#### Acceptance Criteria

1. THE UI SHALL render body text at a minimum font size of 16 pixels
2. THE UI SHALL render primary headings at 24 pixels bold
3. THE UI SHALL render secondary headings at 20 pixels semibold
4. THE UI SHALL render small metadata labels at no less than 14 pixels
5. THE UI SHALL use a line height of 1.5× the font size for body text

### Requirement 9: Layout and Viewport

**User Story:** As a farmer using the 7-inch touchscreen, I want the interface to fit the screen without scrolling horizontally, so that all content is accessible.

#### Acceptance Criteria

1. THE UI SHALL render in landscape orientation optimized for 800×480 pixel resolution
2. THE UI SHALL apply safe margins of 16 pixels on all edges
3. THE UI SHALL constrain content width to a maximum of 768 pixels
4. THE UI SHALL render scrollable content regions with a minimum height of 200 pixels before activating scroll indicators

### Requirement 10: Color System and Contrast

**User Story:** As a farmer working in varying light conditions, I want sufficient contrast and meaningful colors, so that I can distinguish elements and understand status at a glance.

#### Acceptance Criteria

1. THE UI SHALL use green (#4CAF50) for primary actions (start, continue, confirm)
2. THE UI SHALL use red (#F44336) for destructive actions (delete, cancel, abort)
3. THE UI SHALL use amber (#FFC107) for warning states (paused, high temperature)
4. THE UI SHALL use dark gray (#212121) text on light neutral (#F5F5F5) backgrounds
5. THE UI SHALL maintain a minimum contrast ratio of 4.5:1 for all text elements (WCAG AA compliance)

### Requirement 11: Language and Terminology

**User Story:** As a Spanish-speaking farmer with basic literacy, I want the interface in simple Spanish with agricultural terms, so that I can understand all labels and messages without technical knowledge.

#### Acceptance Criteria

1. THE UI SHALL display all labels, buttons, headings, and messages in Spanish
2. THE UI SHALL use simple agricultural terminology (e.g., "Tomates detectados" instead of "Detecciones únicas")
3. THE UI SHALL display actionable error messages in plain language (e.g., "La cámara no está conectada. Revisa el cable." instead of exception names)
4. THE UI SHALL display agricultural metrics with contextual labels (e.g., "45 tomates detectados — 78% sanos" instead of "45 / 78%")

### Requirement 12: Navigation Depth and Flow

**User Story:** As a farmer, I want to navigate the system with minimal screen transitions, so that I can reach my goal quickly.

#### Acceptance Criteria

1. THE UI SHALL maintain a maximum navigation depth of 4 levels from the home screen to any report
2. THE UI SHALL provide a visible back navigation mechanism on every screen except the home screen
3. WHEN the Farmer is on the Monitoring_Report_Screen, THE UI SHALL provide a direct "Volver al Módulo" action to return to the Module_Detail_Screen without traversing intermediate screens

### Requirement 13: Destructive Action Confirmation

**User Story:** As a farmer, I want the system to confirm before deleting data, so that I do not lose information by accidental taps.

#### Acceptance Criteria

1. WHEN the Farmer requests deletion of a greenhouse, THE UI SHALL display a confirmation dialog with the message "¿Seguro que deseas eliminar este invernadero? Se eliminarán todos sus módulos y monitoreos."
2. WHEN the Farmer requests deletion of a module, THE UI SHALL display a confirmation dialog with the message "¿Seguro que deseas eliminar este módulo? Se eliminarán todos sus monitoreos."
3. WHEN the Farmer requests abortion of an active monitoring, THE UI SHALL display a confirmation dialog with the message "¿Seguro que deseas detener el monitoreo? Se guardarán los resultados parciales."
4. THE UI SHALL render confirmation dialog buttons with the destructive action in red and the cancel action in neutral color

### Requirement 14: Offline-First Operation

**User Story:** As a farmer working in a greenhouse without internet, I want the interface to load and operate using only local resources, so that I am never blocked by connectivity issues.

#### Acceptance Criteria

1. THE UI SHALL load all pages from the local FastAPI server without requiring external network requests
2. THE UI SHALL serve all CSS and JavaScript assets from local static files
3. THE UI SHALL render complete page content without loading spinners that depend on remote data

### Requirement 15: Monitoring State Display

**User Story:** As a farmer, I want the interface to clearly show me what the system is doing during each monitoring phase, so that I know whether to wait or take action.

#### Acceptance Criteria

1. WHILE monitoring status is "initializing", THE UI SHALL display a spinner icon and the text "Iniciando..."
2. WHILE monitoring status is "running", THE UI SHALL display an active indicator and the text "Monitoreando..."
3. WHILE monitoring status is "paused", THE UI SHALL display an amber banner and the text "Pausado" with available actions "Reanudar" and "Cancelar"
4. WHILE monitoring status is "finishing", THE UI SHALL display a progress indicator and the text "Finalizando..."
5. WHEN monitoring status is "completed", THE UI SHALL display a green checkmark icon with the text "Monitoreo completado"
6. WHEN monitoring status is "aborted", THE UI SHALL display an orange icon and the text "Monitoreo cancelado"
7. WHEN monitoring status is "error", THE UI SHALL display a red icon and an actionable error message describing the problem

### Requirement 16: Error Handling User Experience

**User Story:** As a farmer, I want clear guidance when something goes wrong, so that I know what happened and what to do next.

#### Acceptance Criteria

1. IF the camera is not available when starting a monitoring, THEN THE UI SHALL display "La cámara no está disponible. Verifica la conexión y vuelve a intentar." and return the Farmer to the Module_Detail_Screen
2. IF the camera connection is lost during monitoring, THEN THE UI SHALL display "Se perdió la conexión con la cámara. Se guardará el progreso hasta este momento." and offer "Guardar Monitoreo Parcial" or "Descartar"
3. IF disk space is below the minimum threshold, THEN THE UI SHALL display "Espacio en disco bajo ({space} MB restantes). Libera espacio antes de continuar." and block monitoring start
4. IF inference models fail to load, THEN THE UI SHALL display "No se pudieron cargar los modelos de inferencia. Verifica que los archivos estén en su lugar." and return the Farmer to the Module_Detail_Screen

### Requirement 17: Single Primary Action Per Screen

**User Story:** As a farmer, I want one clear main action on each screen, so that I always know what the next step is.

#### Acceptance Criteria

1. THE Greenhouse_List_Screen SHALL have tapping a greenhouse card as the primary action
2. THE Greenhouse_Detail_Screen SHALL have tapping a module card as the primary action
3. THE Module_Detail_Screen SHALL have the "Iniciar Nuevo Monitoreo" button as the sole Primary_Action_Button
4. THE Monitoring_Setup_Screen SHALL have the "Continuar" button as the sole Primary_Action_Button
5. THE Monitoring_Execution_Screen SHALL have automatic completion and navigation as the primary flow (with "Detener Monitoreo" as a secondary action)
6. THE Monitoring_Report_Screen SHALL have the "Volver al Módulo" button as the primary navigation action
