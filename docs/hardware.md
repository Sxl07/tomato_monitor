# Hardware del proyecto

## Hardware disponible

- Raspberry Pi 5.
- Fuente oficial Raspberry Pi 27W.
- Case oficial con ventilador.
- Raspberry Pi AI Camera.
- Pantalla táctil DSI de 7 pulgadas.
- microSD.
- Chasis robótico con motores, ruedas y estructura ensamblada.

## Estado de validación

- Raspberry Pi 5 armada y validada.
- Pantalla táctil funcionando.
- Cámara funcionando (hardware validado con `rpicam-hello`).
- Teclado y resolución configurados.
- Sistema operativo instalado.
- Entorno Python preparado.
- API validada.
- Detectron2 instalado.
- Modelos probados.

## Integración de AI Camera (software)

| Componente | Estado |
|---|---|
| Adaptador de software (`RaspberryCameraFrameSource`) | ✅ Creado |
| Interfaz `FrameSource` (abstracción de dominio) | ✅ Creada |
| Script de validación (`scripts/camera/validate_ai_camera.py`) | ✅ Creado |
| Script de inferencia individual (`scripts/camera/single_frame_inference.py`) | ✅ Creado |
| Configuración de modo de entrada (`INPUT_SOURCE`) | ✅ Implementada |
| Validación en vivo con cámara conectada | ⏳ Pendiente (requiere ejecución manual en RPi) |
| Benchmark de FPS de captura en vivo | ⏳ Pendiente |
| Integración con pipeline completo en modo live | ⏳ Pendiente (Spec 007) |

**Nota:** La AI Camera funciona como dispositivo de captura. **No** acelera automáticamente la inferencia de Detectron2 (ver ADR-002). La aceleración vía NPU del IMX500 requiere conversión de modelos y está fuera del alcance de esta fase.

## Observaciones térmicas

Durante pruebas con inferencia, carga de modelos, crops, detecciones, snapshots, aplicación web y reconstrucción de video, la temperatura aumentó significativamente. Se considera obligatorio usar refrigeración activa durante pruebas y operación.

El hardware disponible incluye el case oficial de Raspberry Pi con ventilador activo (refrigeración activa), que debe estar instalado y en funcionamiento antes de ejecutar cualquier benchmark de inferencia sostenida. La verificación física del funcionamiento del ventilador debe realizarse manualmente en la Raspberry Pi previo a cada sesión de benchmark.