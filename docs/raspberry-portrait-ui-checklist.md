# Checklist de verificación manual — UI Portrait (480×800)

Este documento lista las verificaciones manuales a realizar en la pantalla DSI 7" del Raspberry Pi
en orientación vertical (portrait, 480×800 píxeles).

## Pre-requisitos

- Raspberry Pi 5 con pantalla DSI 7" en orientación vertical
- Navegador Chromium en modo kiosk o pantalla completa
- Aplicación Tomato Monitor ejecutándose localmente

## Verificaciones por pantalla

### 1. Login

- [ ] Campos email/contraseña ocupan ancho completo
- [ ] Botón "Iniciar sesión" ocupa ancho completo
- [ ] Touch targets ≥44×44px
- [ ] Texto legible sin zoom (≥16px body)
- [ ] No hay scroll horizontal

### 2. Dashboard

- [ ] Tarjetas métricas apiladas en columna única
- [ ] Quick links apilados en columna única
- [ ] Bottom navigation visible con 4 ícono/labels
- [ ] Alertas operativas visibles sin truncar
- [ ] Contenido no se oculta detrás de bottom nav
- [ ] Scroll vertical fluido

### 3. Activity Form (Registrar Actividad)

- [ ] Campos de formulario al 100% de ancho
- [ ] Selector de tipo de actividad visible completo
- [ ] Campos condicionales (producto, cantidad) al ancho completo
- [ ] Botones Guardar/Cancelar al ancho completo y apilados
- [ ] Teclado virtual no oculta campo enfocado

### 4. Monitoring Execution

- [ ] Spinner y "Monitoreando..." centrado
- [ ] Contador de snapshots legible
- [ ] Thumbnail última captura visible
- [ ] Botón "Finalizar captura" ocupa ancho completo
- [ ] Botón "Cancelar monitoreo" ocupa ancho completo
- [ ] Ambos botones apilados con separación ≥8px
- [ ] Diálogos de confirmación legibles

### 5. Module Detail / History

- [ ] Timeline de historial combinado en columna única
- [ ] Cards de monitoreos y actividades apiladas
- [ ] Botones "Iniciar Monitoreo" / "Registrar Actividad" al ancho completo

### 6. Navigation (Bottom Nav)

- [ ] 4 items visibles: Inicio, Módulos, Exportar, Sync
- [ ] Cada item touch target ≥44×44px
- [ ] Nav no se superpone al contenido (hay padding-bottom)
- [ ] Nav fija en la parte inferior al hacer scroll

### 7. Export / Sync

- [ ] Botones de exportación al ancho completo
- [ ] Status de sincronización legible
- [ ] Contadores de pendientes visibles

## Criterios de aceptación

- Todos los touch targets miden ≥44×44px
- No hay scroll horizontal en ninguna pantalla
- Bottom nav visible y funcional en portrait
- Bottom nav oculto en landscape (800×480)
- Texto body ≥16px en todas las pantallas
- Cards stack en single-column en portrait

## Herramienta de prueba alternativa (PC)

En ausencia del dispositivo físico, usar Chrome DevTools:
1. F12 → Toggle device toolbar
2. Dimensiones: 480×800
3. Verificar las mismas condiciones arriba listadas
