# Integración de Cámara en Vivo — Tomato Monitor

**Estado:** Adaptador de software creado — pendiente de validación en vivo en Raspberry Pi.

---

## Descripción general

Este documento describe la integración de la Raspberry Pi AI Camera como fuente de entrada en vivo para el sistema Tomato Monitor. La integración es incremental: el modo de video offline se mantiene como predeterminado y la cámara en vivo es una fuente alternativa configurable.

---

## Arquitectura

### Abstracción FrameSource

El sistema utiliza una interfaz abstracta `FrameSource` que desacopla el pipeline de visión de la fuente de frames específica:

```
src/domain/interfaces/frame_source.py    → Interfaz abstracta (capa de dominio)
src/infrastructure/camera/               → Implementaciones concretas
```

**Interfaz:**

```python
class FrameSource(ABC):
    def read(self) -> Tuple[bool, Optional[Any]]:
        """Lee el siguiente frame. Retorna (éxito, frame_bgr)."""
        ...

    def release(self) -> None:
        """Libera los recursos (archivo, cámara)."""
        ...

    def is_available(self) -> bool:
        """Verifica si la fuente está disponible."""
        ...
```

### Implementaciones

| Implementación | Archivo | Descripción |
|---|---|---|
| `VideoFileFrameSource` | `src/infrastructure/camera/video_file_frame_source.py` | Envuelve `cv2.VideoCapture` para archivos de video offline |
| `RaspberryCameraFrameSource` | `src/infrastructure/camera/raspberry_camera_frame_source.py` | Captura frames desde la AI Camera vía `picamera2` |

### Diagrama de componentes

```
┌─────────────────────────────────────────────────┐
│                Pipeline de Visión                │
│           (detector, tracker, etc.)             │
└──────────────────────┬──────────────────────────┘
                       │ read() → (bool, frame)
                       ▼
┌─────────────────────────────────────────────────┐
│              FrameSource (interfaz)              │
└──────┬──────────────────────────────┬───────────┘
       │                              │
       ▼                              ▼
┌──────────────────┐    ┌──────────────────────────┐
│ VideoFileFrame   │    │ RaspberryCameraFrame     │
│ Source           │    │ Source                   │
│ (cv2.VideoCapture)│   │ (picamera2.Picamera2)    │
└──────────────────┘    └──────────────────────────┘
```

---

## Configuración

### Parámetros en `src/infrastructure/config/settings.py`

```python
INPUT_SOURCE = "offline_video"  # Opciones: "offline_video", "live_camera"
CAMERA_ENABLED = False
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 5
```

### Cómo cambiar el modo de entrada

1. **Para usar video offline (predeterminado):**
   ```python
   INPUT_SOURCE = "offline_video"
   ```

2. **Para usar la cámara en vivo:**
   ```python
   INPUT_SOURCE = "live_camera"
   CAMERA_ENABLED = True
   ```

> **Nota:** Cambiar a `live_camera` solo tiene efecto en Raspberry Pi con la AI Camera conectada. En otros entornos, `RaspberryCameraFrameSource` reportará que picamera2 no está disponible.

---

## Procedimiento de validación

### Requisitos previos

- Raspberry Pi 5 con AI Camera conectada
- Raspberry Pi OS instalado (picamera2 viene pre-instalado)
- Ventilador activo funcionando

### Paso 1: Verificar picamera2

```bash
python -c "from picamera2 import Picamera2; print('OK')"
```

### Paso 2: Verificar cámara a nivel de sistema

```bash
rpicam-hello --timeout 2000
```

### Paso 3: Ejecutar script de validación

```bash
python scripts/camera/validate_ai_camera.py
```

**Resultado esperado:**
- Confirma que picamera2 está disponible
- Confirma acceso a la cámara
- Captura un frame y lo guarda en `outputs/camera_test/test_frame.jpg`
- Reporta dimensiones, formato y tiempo de captura

### Paso 4: Ejecutar inferencia de frame único (opcional)

```bash
python scripts/camera/single_frame_inference.py
```

O con una imagen de fallback (funciona sin cámara):

```bash
python scripts/camera/single_frame_inference.py --image data/images/IMG_2771.jpg
```

---

## Escenarios de error y recuperación

| Error | Mensaje | Acción de recuperación |
|---|---|---|
| picamera2 no instalado | "picamera2 no está disponible" | Verificar que se usa Raspberry Pi OS. No requiere pip install. |
| Cámara no detectada | "La cámara no está disponible" | Verificar conexión física del cable. Ejecutar `rpicam-hello`. |
| Cámara ocupada | "La cámara no está disponible" | Cerrar otros procesos que usen la cámara. Reiniciar si persiste. |
| Fallo en captura de frame | "No se pudo capturar el frame" | Reintentar. Si persiste, verificar cable y reiniciar cámara. |
| Timeout en captura | "Camera frame capture failed" | Verificar que la cámara responde. Desconectar y reconectar. |
| Permiso denegado | Error de acceso | Verificar que el usuario pertenece al grupo `video`. |

### Comando para verificar permisos

```bash
groups $USER | grep video
```

Si no aparece `video`, agregar el usuario:

```bash
sudo usermod -aG video $USER
# Reiniciar sesión para aplicar cambios
```

---

## Notas de seguridad térmica

### Reglas obligatorias

1. **No ejecutar inferencia continua sin ventilador activo.**
2. **Monitorear temperatura antes y después de cada prueba.**
3. **Si la temperatura supera 80°C, detener toda inferencia y esperar enfriamiento.**
4. **El script `single_frame_inference.py` ejecuta UNA sola inferencia — no usar en bucle.**

### Monitoreo de temperatura

```bash
vcgencmd measure_temp
```

### Rangos de temperatura

| Rango | Estado | Acción |
|---|---|---|
| < 60°C | Normal | Operación segura |
| 60–70°C | Elevada | Monitorear, reducir carga si es sostenida |
| 70–80°C | Alta | Considerar pausar inferencia |
| > 80°C | Crítica | Detener inmediatamente, esperar enfriamiento |

---

## Instalación

### picamera2

`picamera2` viene **pre-instalado** en Raspberry Pi OS. **No requiere `pip install`.**

Verificación:

```bash
python -c "from picamera2 import Picamera2; print('OK')"
```

Si por alguna razón no está disponible:

```bash
sudo apt install -y python3-picamera2
```

> **Nota:** picamera2 NO se agrega a `requirements.txt` del proyecto porque es una dependencia específica de Raspberry Pi OS y no está disponible en PyPI para otras plataformas.

### Dependencia de libcamera

picamera2 depende de `libcamera`, que también viene pre-instalado en Raspberry Pi OS:

```bash
rpicam-hello --timeout 1000  # Verifica que libcamera funciona
```

---

## Limitaciones actuales

- La integración con el pipeline completo (detector + tracker + health + maturity) en modo live no está habilitada por defecto.
- No se ha medido formalmente el FPS de captura en vivo (pendiente de benchmark).
- La AI Camera **no** acelera la inferencia de Detectron2 automáticamente (ver ADR-002).
- El modo live solo funciona en Raspberry Pi con la AI Camera conectada físicamente.

---

## Documentos relacionados

- `docs/hardware.md` — inventario de hardware y estado de validación
- `docs/raspberry-setup.md` — procedimiento de instalación en RPi
- `docs/decisions/ADR-002` — estrategia de integración de AI Camera
- `.kiro/specs/002-camera-live-integration/` — spec completa (requisitos, diseño, tareas)
