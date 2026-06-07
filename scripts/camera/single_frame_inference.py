#!/usr/bin/env python3
"""Single-frame inference from camera or fallback image.

Captures one frame from the Raspberry Pi AI Camera (or loads a fallback image)
and passes it through the detector to report basic inference metrics.

IMPORTANT — Safety notes:
- This script runs a SINGLE inference pass. Do NOT use it in a loop.
- Continuous inference without active cooling causes thermal throttling.
- Monitor temperature with: vcgencmd measure_temp
- Ensure the fan is running before executing any inference on Raspberry Pi.
- If temperature exceeds 80°C, stop all inference and let the device cool.

Usage (on Raspberry Pi with camera):
    python scripts/camera/single_frame_inference.py

Usage (fallback with image file, works on any machine):
    python scripts/camera/single_frame_inference.py --image data/images/IMG_2771.jpg

Usage (skip inference, only capture/load frame):
    python scripts/camera/single_frame_inference.py --no-inference
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.config.settings import CAMERA_WIDTH, CAMERA_HEIGHT


def get_temperature() -> str:
    """Read Raspberry Pi temperature via vcgencmd. Returns 'N/A' if unavailable."""
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output format: temp=XX.X'C
            return result.stdout.strip()
        return "N/A (vcgencmd falló)"
    except FileNotFoundError:
        return "N/A (vcgencmd no disponible — no es Raspberry Pi)"
    except subprocess.TimeoutExpired:
        return "N/A (timeout)"
    except Exception:
        return "N/A"


def load_frame_from_image(image_path: str):
    """Load a frame from an image file as fallback."""
    import cv2

    path = Path(image_path)
    if not path.exists():
        print(f"  ERROR: Archivo no encontrado: {image_path}")
        sys.exit(1)

    frame = cv2.imread(str(path))
    if frame is None:
        print(f"  ERROR: No se pudo leer la imagen: {image_path}")
        sys.exit(1)

    return frame


def capture_frame_from_camera():
    """Capture a single frame from the Raspberry Pi AI Camera."""
    try:
        from picamera2 import Picamera2  # noqa: F401
    except ImportError:
        print("  ERROR: picamera2 no está disponible.")
        print("  Use --image para proporcionar una imagen de fallback.")
        print("  Ejemplo: python scripts/camera/single_frame_inference.py --image data/images/IMG_2771.jpg")
        sys.exit(1)

    from src.infrastructure.camera.raspberry_camera_frame_source import (
        RaspberryCameraFrameSource,
    )

    camera = RaspberryCameraFrameSource(width=CAMERA_WIDTH, height=CAMERA_HEIGHT)

    if not camera.is_available():
        print("  ERROR: Cámara no disponible.")
        print("  Verificar conexión o usar --image como fallback.")
        camera.release()
        sys.exit(1)

    success, frame = camera.read()
    camera.release()

    if not success or frame is None:
        print("  ERROR: No se pudo capturar frame de la cámara.")
        sys.exit(1)

    return frame


def run_inference(frame):
    """Run the detector on a single frame. Returns detections count and time."""
    try:
        from src.infrastructure.vision.detectron_detector import DetectronDetector
    except ImportError as e:
        print(f"  ERROR: No se pudo importar el detector: {e}")
        print("  Detectron2 puede no estar instalado en este entorno.")
        return None, None

    from src.infrastructure.config.settings import DETECTION_MODEL_PATH, DEVICE

    if not DETECTION_MODEL_PATH.exists():
        print(f"  ERROR: Modelo no encontrado en {DETECTION_MODEL_PATH}")
        print("  Coloca model.pth en models/modelo_d2/ antes de ejecutar inferencia.")
        return None, None

    print("  Cargando detector (RetinaNet R-50-FPN)...")
    load_start = time.time()

    try:
        detector = DetectronDetector()
    except Exception as e:
        print(f"  ERROR al cargar detector: {e}")
        return None, None

    load_time = time.time() - load_start
    print(f"  Detector cargado en {load_time:.2f}s (device={DEVICE})")

    print("  Ejecutando inferencia en frame único...")
    inference_start = time.time()

    try:
        detections = detector.detect(frame)
    except Exception as e:
        print(f"  ERROR durante inferencia: {e}")
        return None, None

    inference_time = time.time() - inference_start
    num_detections = len(detections) if detections is not None else 0

    return num_detections, inference_time


def main():
    parser = argparse.ArgumentParser(
        description="Inferencia de frame único desde cámara o imagen."
    )
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="Ruta a imagen de fallback (si no hay cámara disponible).",
    )
    parser.add_argument(
        "--no-inference",
        action="store_true",
        help="Solo capturar/cargar frame, sin ejecutar inferencia.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Inferencia de Frame Único — Tomato Monitor")
    print("=" * 60)
    print()

    # Temperature check (before inference)
    print("[1/4] Temperatura actual del sistema:")
    temp_before = get_temperature()
    print(f"  {temp_before}")
    print()

    # Frame acquisition
    print("[2/4] Obteniendo frame...")
    frame_start = time.time()

    if args.image:
        print(f"  Fuente: imagen de archivo ({args.image})")
        frame = load_frame_from_image(args.image)
    else:
        print("  Fuente: Raspberry Pi AI Camera")
        frame = capture_frame_from_camera()

    frame_time = time.time() - frame_start
    print(f"  Frame obtenido en {frame_time:.3f}s")
    print(f"  Dimensiones: {frame.shape[1]}x{frame.shape[0]} (ancho x alto)")
    print(f"  Canales: {frame.shape[2] if len(frame.shape) == 3 else 1}")
    print(f"  Dtype: {frame.dtype}")
    print()

    # Inference (optional)
    num_detections = None
    inference_time = None

    if not args.no_inference:
        print("[3/4] Ejecutando inferencia...")
        num_detections, inference_time = run_inference(frame)

        if inference_time is not None:
            print(f"  Detecciones: {num_detections}")
            print(f"  Tiempo de inferencia: {inference_time:.3f}s")
        else:
            print("  Inferencia no completada (ver errores arriba).")
        print()
    else:
        print("[3/4] Inferencia omitida (--no-inference)")
        print()

    # Temperature check (after inference)
    print("[4/4] Temperatura después de inferencia:")
    temp_after = get_temperature()
    print(f"  {temp_after}")
    print()

    # Summary
    print("=" * 60)
    print("  RESUMEN")
    print("=" * 60)
    print(f"  Fuente del frame:     {'Archivo' if args.image else 'Cámara'}")
    print(f"  Dimensiones:          {frame.shape[1]}x{frame.shape[0]}")
    print(f"  Tiempo de captura:    {frame_time:.3f}s")
    if inference_time is not None:
        print(f"  Tiempo de inferencia: {inference_time:.3f}s")
        print(f"  Detecciones:          {num_detections}")
    elif not args.no_inference:
        print(f"  Inferencia:           No completada")
    else:
        print(f"  Inferencia:           Omitida")
    print(f"  Temp. antes:          {temp_before}")
    print(f"  Temp. después:        {temp_after}")
    print()
    print("  NOTA DE SEGURIDAD TÉRMICA:")
    print("  No ejecutar inferencia continua sin refrigeración activa.")
    print("  Si la temperatura supera 80°C, detener toda inferencia.")
    print()


if __name__ == "__main__":
    main()
