# UX Design Steering - Tomato Monitor

## Target user profile

- **Primary user:** Farmer/operator with basic computer literacy
- **Environment:** Greenhouse, operational conditions (humidity, gloves, varying light)
- **Device:** Raspberry Pi 5 with DSI 7" touchscreen, used as portable tablet
- **Primary orientation:** Vertical (portrait, 480×800) — operator carries device upright
- **Alternative orientation:** Horizontal (landscape, 800×480) — supported but secondary
- **Interaction mode:** Touch-first (no mouse/keyboard assumed during operation)
- **Language:** Spanish (interface text, labels, error messages)
- **Context:** The operator carries the device through the greenhouse, needs quick actionable information with minimal training

---

## Navigation hierarchy

```
Login (Inicio de sesión)
  └── Dashboard (Resumen general)
        ├── Greenhouse List (Listado de Invernaderos)
        │     └── Greenhouse Detail → Module List
        │           └── Module Detail → Combined History
        │                 ├── New Monitoring Setup → Execution → Report
        │                 └── Register Activity (Registrar Actividad)
        ├── Alerts (Alertas operativas)
        ├── Activity Log (Bitácora agrícola)
        └── Export / Sync (Exportación / Sincronización)
```

### Screen flow

| # | Screen | Purpose | Primary Action |
|---|---|---|---|
| 0 | Login | Authenticate local operator | Enter credentials → go to Dashboard |
| 1 | Dashboard | Overview of greenhouse status, alerts, pending tasks | Tap module/alert → navigate to detail |
| 2 | Greenhouse List | Select which greenhouse to work with | Tap greenhouse → go to Screen 3 |
| 3 | Greenhouse Detail | View modules in the greenhouse | Tap module → go to Screen 4 |
| 4 | Module Detail | View module info + combined history | Tap "Iniciar Monitoreo" or "Registrar Actividad" |
| 5 | Monitoring Setup | Confirm module dimensions | Tap "Continuar" → go to Screen 6 |
| 6 | Monitoring Execution | Operator manually traverses module with device | "Finalizar captura" → go to Screen 7 when analysis completes |
| 7 | Monitoring Report | View results and metrics | Tap "Volver al Módulo" or "Exportar" |
| 8 | Activity Form | Register agricultural activity | "Guardar" → return to module |
| 9 | Export | Generate ZIP package | "Exportar" → download/confirm |

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
| `RUNNING` | Camera active, operator traversing module | Live counter "Snapshots capturados: N" | "Finalizar captura" (primary), "Cancelar monitoreo" (destructive) |
| `PAUSED` | Processing paused (e.g., high temperature) | Yellow banner + "Pausado" | "Reanudar" or "Cancelar" |
| `ANALYZING` | Deferred inference on captured snapshots | Spinner + progress "Analizando snapshots... (X/Y)" | No action buttons; polling continues; thermal pause shows alert |
| `FINISHING` | Legacy: consolidating results | Progress bar + "Finalizando..." | None (brief transition) |
| `COMPLETED` | Monitoring saved, report available | Auto-redirect to report screen | View report, start new, export |
| `ABORTED` | User explicitly cancelled the monitoring | Orange icon + "Cancelado" | Partial results preserved |
| `ERROR` | Unrecoverable failure (camera lost, etc.) | Red icon + error message | Acknowledge and return |

### ANALYZING state details

- Entered when the operator finalizes capture (has ≥1 snapshot).
- Camera is released before entering this state.
- UI shows progress bar with X/Y snapshots processed.
- If thermal protection triggers during analysis: yellow alert "Pausado por temperatura ({temp}°C)".
- When analysis completes → auto-transition to `COMPLETED` → redirect to report.
- If analysis fails → transition to `ERROR`.
- No user action buttons during analyzing (no abort, no pause).

---

## Touchscreen design rules (DSI 7")

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

### Layout — Portrait mode (primary, 480×800)

- **Orientation:** Portrait (480×800) — primary for field use
- **Safe margins:** 16 px on all edges
- **Content width:** Full width minus margins (448 px usable)
- **Cards:** Stack vertically, full-width, single column
- **Forms:** Full-width inputs, one field per row
- **Navigation:** Compact bottom bar (4–5 icons)
- **Scrollable regions:** Generous vertical space
- **Virtual keyboard:** Forms should not be obscured; scroll to focused input

### Layout — Landscape mode (alternative, 800×480)

- **Orientation:** Landscape (800×480) — alternative, desktop-like
- **Safe margins:** 16 px on all edges
- **Content max width:** 768 px (centered if needed)
- **Scrollable regions:** Minimum 200 px height before scroll indicators appear
- **Cards/panels:** May use 2-column grid where content allows

### Responsive strategy

- Use `@media (orientation: portrait)` and `@media (max-width: 600px)` for portrait rules
- Portrait CSS is **additive** — does not break landscape layout
- Cards switch from grid to single-column stack in portrait
- Navigation switches from top bar to bottom compact bar in portrait
- Forms use full width in portrait
- Buttons become full-width in portrait

---

## UX principles for the operator

### 1. Simple language

- Use agricultural terminology the operator understands
- Avoid technical jargon (e.g., say "Tomates detectados" not "Detecciones únicas")
- Error messages must be actionable, not cryptic (e.g., "La cámara no está conectada. Revisa el cable." not "CameraNotAvailableException")
- Use portable/manual traversal language: "Recorra el módulo con el dispositivo"
- Do NOT use robot/autonomous language: never "El robot recorrerá", "Navegación automática", etc.

### 2. Clear actions

- One primary action per screen (e.g., "Iniciar Nuevo Monitoreo")
- Secondary actions visually distinct (smaller, less saturated color)
- Destructive actions require confirmation ("¿Seguro que deseas cancelar el monitoreo?")
- "Finalizar captura" is separate from "Cancelar monitoreo"
- Progress is always visible during long operations

### 3. Agricultural metrics first

- Display what matters to the operator: count, maturity %, health %
- Technical metrics (FPS, inference time, temperature) are secondary or hidden
- Use visual aids: color-coded maturity stages (green → red gradient), pie charts for percentages
- Provide context: "45 tomates detectados — 78% sanos" is better than just "45 / 78%"
- Dashboard shows only data backed by real system state (no invented indicators)

### 4. Fault tolerance

- If inference fails on one snapshot, continue with the next (don't crash the monitoring)
- If a monitoring is aborted, save partial results — don't lose the operator's time
- Auto-save form inputs (module dimensions) so the operator doesn't re-enter them

### 5. Offline-first

- No assumptions about internet connectivity
- No loading spinners that wait for remote data
- All operations complete using local resources only
- Login works offline for previously registered users
- Export generates local ZIP without internet

### 6. Traceability-aware

- Every monitoring and activity records who did it and when
- "Pendiente de exportar/sincronizar" is visible after report
- Combined history shows both monitorings and activities per module

---

## Screen components (high-level)

### Login

- **Simple form:** Email + password
- **Large input fields** for touch
- **Clear error:** "Credenciales incorrectas" (no technical details)
- **No registration from this screen** (registration is online-oriented/future)

### Dashboard

- **Greeting:** "Hola, {nombre}" + date
- **Metric cards:** Invernaderos, Módulos, Módulos pendientes, Último monitoreo
- **Alerts section:** Modules overdue, exports pending
- **Recent activities:** Last 5 agricultural activities
- **Quick actions:** "Iniciar monitoreo", "Registrar actividad", "Exportar datos"

### Module Detail (extended)

- **Header:** Module name + edit/delete icons
- **Info panel:** Crop type, dimensions, monitoring frequency, last monitored
- **Status badge:** "Al día" / "Pendiente" / "Vencido" based on frequency
- **Primary actions:** "Iniciar Monitoreo", "Registrar Actividad"
- **Combined history:** Timeline with monitorings and activities, ordered by date

### Monitoring Execution

- **Header:** "Monitoreando — {Module Name}"
- **Status indicator:** Spinner + "Recorra el módulo con el dispositivo..."
- **Live counters:** Snapshots capturados: {count}
- **Last snapshot thumbnail**
- **Temperature warning banner** if applicable
- **Actions:** "Finalizar captura" (primary), "Cancelar monitoreo" (destructive, with confirmation)
- **Auto-advance:** When status → COMPLETED, navigate to report

### Activity Form

- **Header:** "Registrar Actividad — {Module Name}"
- **Fields:** Activity type (selector from catalog), date/time, product (conditional), quantity (conditional), unit, notes
- **Actions:** "Guardar", "Cancelar"
- **Behavior:** Dynamic fields based on activity type (requires_product, allows_quantity)

### Export

- **Status:** Pending exports count, last export date
- **Action:** "Generar exportación ZIP"
- **Progress:** Generating → completed → download link
- **Error handling:** Retry button on failure

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
| Login failure | "Credenciales incorrectas. Verifica tu email y contraseña." | Retry login |
| Export failure | "No se pudo generar la exportación. Verifica el espacio en disco." | Retry button |

---

## Navigation patterns

### Portrait (primary — bottom bar)

```
[🏠 Inicio] [📸 Monitoreo] [📋 Bitácora] [⚠️ Alertas] [⋯ Más]
```

"Más" expands to: Invernaderos, Exportar, Configuración, Cerrar sesión.

### Landscape (alternative — top bar)

```
[Dashboard] [Invernaderos] [Bitácora] [Alertas] [Exportar] [Usuario ▾]
```

---

## Rules for Kiro

- When designing new screens or flows, consult this steering file first
- Use the defined state machine for monitoring status — do not invent new states
- All touch targets must meet the 44×44 px minimum
- All primary text must be ≥16 px
- Do not use technical language in user-facing strings
- Do not assume the operator has internet access
- Do not show technical metrics (FPS, RAM, inference time) on operator-facing screens — log them only
- Keep navigation depth ≤4 levels (current max: Dashboard → Greenhouse → Module → Report)
- Use portrait-first responsive design — landscape as fallback
- Never use robot/autonomous/chassis/motor language in UI-facing text
- Use manual traversal language: "Recorra el módulo", "Lleve el dispositivo"
- "Finalizar captura" and "Cancelar monitoreo" are always separate actions
- Dashboard indicators must be based on real system data — no invented metrics
