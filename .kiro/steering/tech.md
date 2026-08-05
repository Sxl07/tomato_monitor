# Technology Steering - Tomato Monitor

## Stack activo

| Categoría | Tecnología | Versión en uso |
|---|---|---|
| Lenguaje | Python | 3.10+ |
| Framework web | FastAPI + Uvicorn | 0.115.12 / 0.34.3 |
| Templates | Jinja2 | 3.1.6 |
| ORM / Persistencia | SQLAlchemy + SQLite | 2.0.41 |
| Deep learning | PyTorch + TorchVision | 2.10.0 / 0.25.0 |
| Detección | Detectron2 (RetinaNet R-50-FPN) | desde GitHub |
| Visión | OpenCV | 4.13.0.92 |
| Numérico | NumPy | 2.4.3 |
| Datos | Pandas | 3.0.1 |
| OS objetivo | Raspberry Pi OS / Debian Bookworm 64-bit | — |

## Configuración de perfiles

- `TOMATO_MONITOR_PROFILE=edge` (default): parámetros conservadores para Raspberry Pi 5.
- `TOMATO_MONITOR_PROFILE=full`: parámetros más agresivos para desarrollo en PC.
- Selección via variable de entorno. Valor inválido → fallback a edge con warning.

## Hardware objetivo

- Raspberry Pi 5 (ARM64, CPU únicamente)
- Raspberry Pi AI Camera (IMX500 + NPU; integración live pendiente)
- Fuente oficial 27W, case con ventilador activo
- Pantalla táctil DSI 7"

## Restricciones técnicas no negociables

- `DEVICE = "cpu"` en toda inferencia; sin CUDA, sin MPS
- Sin dependencias que requieran GPU o drivers NVIDIA
- Sin servicios externos obligatorios (sin bases de datos remotas, sin APIs cloud)
- Toda dependencia nueva debe ser compatible con ARM64/aarch64

## Notas de instalación en Raspberry Pi

- `detectron2` no tiene wheels para ARM64; instalar con:
  ```bash
  pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
  ```
- Usar `libopenblas-dev` como dependencia numérica base (`libatlas-base-dev` no disponible)
- `opencv-python-headless` si se corre sin display; `opencv-python` si hay pantalla
- `requirements-raspberry.txt` está pendiente de completar con versiones exactas validadas en RPi 5

## Advertencias de compatibilidad

- NumPy 2.x tiene breaking changes respecto a 1.x; Detectron2 fue construido originalmente contra 1.x — monitorear incompatibilidades silenciosas
- PyTorch 2.10.0 es reciente; los wheels para ARM64 pueden no existir en PyPI; puede requerir compilación o wheel de terceros
- `pandas 3.0.1` tiene cambios semánticos respecto a 2.x; no usar `.applymap()` ni otras APIs deprecadas

## Restricciones para hardware robótico (futuro)

- No agregar `gpiozero`, `gpiod`, `RPi.GPIO` ni drivers de motor hasta que exista una spec aprobada de hardware.
- El hardware real de motores (BTS7960) aún NO está implementado en software.
- Toda librería de hardware debe validarse en ARM64 antes de agregarla a requirements.
