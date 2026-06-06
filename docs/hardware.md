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
- Cámara funcionando.
- Teclado y resolución configurados.
- Sistema operativo instalado.
- Entorno Python preparado.
- API validada.
- Detectron2 instalado.
- Modelos probados.

## Observaciones térmicas

Durante pruebas con inferencia, carga de modelos, crops, detecciones, snapshots, aplicación web y reconstrucción de video, la temperatura aumentó significativamente. Se considera obligatorio usar refrigeración activa durante pruebas y operación.

El hardware disponible incluye el case oficial de Raspberry Pi con ventilador activo (refrigeración activa), que debe estar instalado y en funcionamiento antes de ejecutar cualquier benchmark de inferencia sostenida. La verificación física del funcionamiento del ventilador debe realizarse manualmente en la Raspberry Pi previo a cada sesión de benchmark.