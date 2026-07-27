# UX Design Steering - Tomato Monitor

## Target user profile

- **Primary user:** Farmer with basic computer literacy
- **Environment:** Greenhouse, operational conditions (humidity, gloves, varying light)
- **Device:** Raspberry Pi 5 with DSI 7" touchscreen (800×480 resolution)
- **Interaction mode:** Touch-first (no mouse/keyboard assumed during operation)
- **Language:** Spanish (interface text, labels, error messages)
- **Context:** The farmer needs quick, actionable information with minimal training

---

## Navigation hierarchy

```
Home (Inicio)
  └── Greenhouse List (Listado de Invernaderos)
        └── Greenhouse Detail → Module List (Detalle de Invernadero)
              └── Module Detail → Monitoring History (Detalle de Módulo)
                    └── New Monitoring Setup (Configuración del Monitoreo)
                          └── Monitoring Execution (Ejecución del Monitoreo)
                                └── Monitoring Report (Reporte del Monitoreo)
```

### Screen flow

| # | Screen | Purpose | Primary Action |
|---|---|---|---|
| 1 | Home / Greenhouse List | Select which greenhouse to work with | Tap greenhouse → go to Screen 2 |
| 2 | Greenhouse Detail | View modules in the greenhouse | Tap module → go to Screen 3 |
| 3 | Module Detail | View module info + monitoring history | Tap "Iniciar Nuevo Monitoreo" → go to Screen 4 |
| 4 | Monitoring Setup | Confirm module dimensions | Tap "Continuar" → go to Screen 5 |
| 5 | Monitoring Execution (live) | Watch robot progress in real time | Automatic → go to Screen 6 when done |
| 6 | Monitoring Report | View results and metrics | Tap "Volver al Módulo" → back to Screen 3 |

---

## Monitoring system states

The system transitions through the following states during a monitoring session:

```
IDLE → INITIALIZING → RUNNING → ANALYZING → COMPLETED
                          ↓           ↓
                       PAUSED       ERROR
                          ↓
                       ABORTED (explicit user cancellation)
                          ↓
                       ERROR (unrecoverable)
```

**Note:** `IDLE` is NOT a persisted state — it means no active monitoring exists for the module. `FINISHING` is retained for backward compatibility but the capture-first flow uses `RUNNING → ANALYZING → COMPLETED`.

| State | Description | UI Indicator | User Actions Available |
|---|---|---|---|
| `IDLE` | System ready, no active monitoring | — | Start new monitoring |
| `INITIALIZING` | Camera starting | Spinner + "Iniciando..." | None (brief transition) |
| `RUNNING` | Camera active, capturing snapshots | Live counter "Snapshots capturados: N" | "Finalizar captura" (primary), "Cancelar monitoreo" (destructive) |
| `PAUSED` | Processing paused (e.g., high temperature) | Yellow banner + "Pausado" | "Reanudar" or "Cancelar" |
| `ANALYZING` | Deferred inference on captured snapshots | Spinner + progress "Analizando snapshots... (X/Y)" | No action buttons; polling continues; thermal pause shows alert |
| `FINISHING` | Legacy: consolidating results | Progress bar + "Finalizando..." | None (brief transition) |
| `COMPLETED` | Monitoring saved, report available | Auto-redirect to report screen | View report, start new |
| `ABORTED` | User explicitly cancelled the monitoring | Orange icon + "Cancelado" | Partial results preserved |
| `ERROR` | Unrecoverable failure (camera lost, etc.) | Red icon + error message | Acknowledge and return |

### ANALYZING state details

- Entered when the farmer finalizes capture (has ≥1 snapshot).
- Camera is released before entering this state.
- UI shows progress bar with X/Y snapshots processed.
- If thermal protection triggers during analysis: yellow alert "Pausado por temperatura ({temp}°C)".
- When analysis completes → auto-transition to `COMPLETED` → redirect to report.
- If analysis fails → transition to `ERROR`.
- No user action buttons during analyzing (no abort, no pause).

---

## Touchscreen design rules (DSI 7", 800×480)

### Touch target sizes

- **Minimum touch target:** 44×44 px (Apple HIG standard)
- **Primary action buttons:** 60×48 px or larger
- **List items (modules, monitorings):** Full-width rows, min 56 px height
- **Spacing between interactive elements:** ≥8 px to prevent mis-taps

### Typography

- **Base font size:** 16 px (minimum for legibility)
- **Primary headings:** 24 px, bold
- **Secondary headings:** 20 px, semibold
- **Body text:** 16 px, regular
- **Small labels (metadata):** 14 px, light
- **Font family:** System default (Roboto on Raspberry Pi OS)
- **Line height:** 1.5× for readability

### Colors and contrast

- **Background:** Light neutral (#F5F5F5 or white)
- **Text:** Dark gray (#212121) for high contrast
- **Primary action:** Green (#4CAF50) for "start", "continue", "confirm"
- **Destructive action:** Red (#F44336) for "delete", "cancel"
- **Warning state:** Amber (#FFC107) for "paused", "high temperature"
- **Disabled state:** Gray (#9E9E9E) with reduced opacity
- **WCAG AA contrast ratio:** Minimum 4.5:1 for text

### Layout

- **Orientation:** Landscape (800×480)
- **Safe margins:** 16 px on all edges
- **Content max width:** 768 px (centered if needed)
- **Scrollable regions:** Minimum 200 px height before scroll indicators appear
- **Cards/panels:** 8 px padding, 4 px border radius

---

## UX principles for the farmer

### 1. Simple language

- Use agricultural terminology the farmer understands
- Avoid technical jargon (e.g., say "Tomates detectados" not "Detecciones únicas")
- Error messages must be actionable, not cryptic (e.g., "La cámara no está conectada. Revisa el cable." not "CameraNotAvailableException")

### 2. Clear actions

- One primary action per screen (e.g., "Iniciar Nuevo Monitoreo")
- Secondary actions visually distinct (smaller, less saturated color)
- Destructive actions require confirmation ("¿Seguro que deseas eliminar este módulo?")
- Progress is always visible during long operations

### 3. Agricultural metrics first

- Display what matters to the farmer: count, maturity %, health %
- Technical metrics (FPS, inference time, temperature) are secondary or hidden
- Use visual aids: color-coded maturity stages (green → red gradient), pie charts for percentages
- Provide context: "45 tomates detectados — 78% sanos" is better than just "45 / 78%"

### 4. Fault tolerance

- If inference fails on one snapshot, continue with the next (don't crash the monitoring)
- If a monitoring is aborted, save partial results — don't lose the farmer's time
- Auto-save form inputs (module dimensions) so the farmer doesn't re-enter them

### 5. Offline-first

- No assumptions about internet connectivity
- No loading spinners that wait for remote data
- All operations complete using local resources only

---

## Screen components (high-level)

### Screen 1: Greenhouse List

- **Header:** "Mis Invernaderos"
- **List:** Card per greenhouse with name, module count, last monitoring date
- **Empty state:** "No hay invernaderos registrados. Crea el primero." + "Crear Invernadero" button
- **Actions:** Tap card → navigate to greenhouse detail; "+" button → create new greenhouse

### Screen 2: Greenhouse Detail

- **Header:** Greenhouse name + edit/delete icons
- **Subheader:** "Módulos"
- **List:** Card per module with crop type, dimensions (if set), last monitoring date
- **Empty state:** "Este invernadero no tiene módulos. Agrega el primero." + "Agregar Módulo" button
- **Actions:** Tap module → navigate to module detail; "+" button → create new module

### Screen 3: Module Detail

- **Header:** Module name + edit/delete icons
- **Info panel:** Crop type, dimensions (or "No configuradas")
- **Primary action (large, centered):** "Iniciar Nuevo Monitoreo" button
- **Subheader:** "Historial de Monitoreos"
- **List:** Card per monitoring with date, time, tomato count, % sanos
- **Empty state:** "No hay monitoreos registrados. Inicia el primero." (only if no history)
- **Actions:** Tap "Iniciar Nuevo Monitoreo" → navigate to setup; tap monitoring card → view report

### Screen 4: Monitoring Setup

- **Header:** "Nuevo Monitoreo — {Module Name}"
- **Form:**
  - Width (m): numeric input, pre-filled if saved
  - Length (m): numeric input, pre-filled if saved
  - Notes (optional): text area
- **Actions:** "Continuar" (validates dimensions > 0) → navigate to execution; "Cancelar" → back to module

### Screen 5: Monitoring Execution (live update)

- **Header:** "Monitoreando — {Module Name}"
- **Status indicator:** Large icon + text (e.g., spinner + "Capturando imágenes...")
- **Live counters:**
  - Snapshots capturados: {count}
  - Tomates detectados: {count}
- **Optional:** Last captured snapshot thumbnail
- **Temperature warning banner (if > threshold):** "Temperatura alta: {temp}°C — El sistema puede pausarse"
- **Actions:** "Detener Monitoreo" button (confirmation required)
- **Auto-advance:** When status → COMPLETED, navigate to report

### Screen 6: Monitoring Report

- **Header:** "{Module Name} — {Date} {Time}"
- **Metric cards (large, visual):**
  - Total de tomates: {count}
  - Tomates sanos: {count} ({pct}%)
  - Tomates enfermos: {count} ({pct}%)
- **Maturity chart:** Horizontal bar or pie chart with 6 USDA stages color-coded
- **Snapshot gallery:** Grid of thumbnails (tap to expand)
- **Actions:** "Volver al Módulo", optional "Exportar Reporte" (future)

---

## Error handling UX

| Error | User-Facing Message | Recovery Action |
|---|---|---|
| Camera not available on start | "La cámara no está disponible. Verifica la conexión y vuelve a intentar." | Return to module detail |
| Camera lost during monitoring | "Se perdió la conexión con la cámara. Se guardará el progreso hasta este momento." | Offer: "Guardar Monitoreo Parcial" or "Descartar" |
| High temperature during monitoring | "Temperatura alta ({temp}°C). El monitoreo se pausó automáticamente." | Offer: "Reanudar" or "Finalizar y Guardar" |
| Model load failure | "No se pudieron cargar los modelos de inferencia. Verifica que los archivos estén en su lugar." | Return to module detail |
| No tomatoes detected in entire monitoring | (Not an error) Report shows: "No se detectaron tomates en este monitoreo." | Normal report view |
| Disk space low | "Espacio en disco bajo ({space} MB restantes). Libera espacio antes de continuar." | Block monitoring start |

---

## Rules for Kiro

- When designing new screens or flows, consult this steering file first
- Use the defined state machine for monitoring status — do not invent new states
- All touch targets must meet the 44×44 px minimum
- All primary text must be ≥16 px
- Do not use technical language in user-facing strings
- Do not assume the farmer has internet access
- Do not show technical metrics (FPS, RAM, inference time) on farmer-facing screens — log them only
- Keep navigation depth ≤4 levels (current max: Home → Greenhouse → Module → Report)
