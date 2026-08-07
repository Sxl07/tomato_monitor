# Rationale de ajuste de alcance — Plataforma portátil

## 1. Contexto inicial

El proyecto se orientó originalmente a una plataforma robótica con movilidad autónoma para recorrido de invernaderos. Los componentes de software (visión, monitoreo, persistencia) avanzaron más rápido que la disponibilidad del hardware robótico (chasis, motores, controladores).

## 2. Riesgo identificado

- Dependencia de adquisición y ensamble de chasis/motores/control de movimiento.
- Incertidumbre en plazos de validación mecánica/eléctrica.
- Riesgo de comprometer la integración final y la evaluación del proyecto de grado.

## 3. Ajuste aplicado

- Plataforma portátil operada manualmente por el operario.
- Raspberry Pi 5 con cámara y pantalla táctil DSI 7".
- Monitoreo visual capture-first (captura → análisis diferido → reporte).
- Trazabilidad agrícola local-first.

## 4. Qué se conserva

- Captura visual con Scene Gate.
- Procesamiento embarcado (detección, salud, madurez).
- Monitoreos con progreso y reportes.
- Historial combinado de monitoreos y actividades.
- Exportación ZIP local.
- Base de sincronización manual provider-agnostic.
- Operación en invernadero sin internet obligatorio.

## 5. Qué queda fuera

- Chasis y motores.
- Navegación autónoma.
- Control de movimiento (BTS7960, GPIO movement).
- RobotOrchestrator activo.
- Proveedores cloud obligatorios.

## 6. Justificación académica

- Reduce riesgo de hardware sin eliminar el núcleo de investigación.
- Preserva la evaluación de visión por computador embebida.
- Mantiene validación de bajo costo y operación local-first.
- Facilita cierre verificable del prototipo dentro del plazo del proyecto de grado.

## 7. Estado de verificación

- Pruebas automatizadas ejecutadas según ejecución de Kiro.
- Validación visual portrait preparada con checklist (pendiente de verificación manual en dispositivo).
- Verificación en campo/invernadero debe documentarse según disponibilidad real.
