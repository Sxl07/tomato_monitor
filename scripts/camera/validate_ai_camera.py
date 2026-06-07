#!/usr/bin/env python3
"""Validate Raspberry Pi AI Camera availability and capture.

This script checks that the Raspberry Pi AI Camera is accessible via picamera2,
captures a single frame, and saves it to outputs/camera_test/test_frame.jpg.

IMPORTANT:
- This script only works on Raspberry Pi with the AI Camera connected.
- picamera2 is pre-installed on Raspberry Pi OS; no pip install is needed.
- On non-RPi environments, it will report that picamera2 is not available.
- Ensure active cooling (fan) is running before executing camera operations.

Usage (on Raspberry Pi):
    python scripts/camera/validate_ai_camera.py
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.config.settings import CAMERA_WIDTH, CAMERA_HEIGHT, OUTPUTS_DIR


def check_picamera2_available() -> bool:
    """Check if picamera2 library is importable."""
    try:
        from picamera2 import Picamera2  # noqa: F401
        return True
    except ImportError:
        return False


def validate_camera() -> None:
    """Run camera validation: availability check, single frame capture, and save."""
    print("=" * 60)
    print("  Validación de Raspberry Pi AI Camera")
    print("=" * 60)
    print()

    # Step 1: Check picamera2 availability
    print("[1/4] Verificando disponibilidad de picamera2...")
    if not check_picamera2_available():
        print("  ERROR: picamera2 no está disponible.")
        print("  Este script solo funciona en Raspberry Pi OS.")
        print("  picamera2 viene pre-instalado en RPi OS (no requiere pip install).")
        print()
        print("  Verificar con: python -c \"from picamera2 import Picamera2; print('OK')\"")
        sys.exit(1)
    print("  OK: picamera2 disponible.")
    print()

    # Step 2: Check camera hardware accessibility
    print("[2/4] Verificando acceso a la cámara...")
    from src.infrastructure.camera.raspberry_camera_frame_source import (
        RaspberryCameraFrameSource,
    )

    camera_source = RaspberryCameraFrameSource(
        width=CAMERA_WIDTH, height=CAMERA_HEIGHT
    )

    if not camera_source.is_available():
        print("  ERROR: La cámara no está disponible.")
        print("  Posibles causas:")
        print("    - La cámara no está conectada correctamente.")
        print("    - Otro proceso está usando la cámara.")
        print("    - Permisos insuficientes (verificar grupo 'video').")
        print()
        print("  Verificar con: rpicam-hello --timeout 2000")
        sys.exit(1)
    print("  OK: Cámara accesible.")
    print()

    # Step 3: Capture a single frame
    print("[3/4] Capturando frame de prueba...")
    start_time = time.time()
    success, frame = camera_source.read()
    capture_time = time.time() - start_time

    if not success or frame is None:
        print("  ERROR: No se pudo capturar el frame.")
        print("  Verificar conexión física de la cámara y reintentar.")
        camera_source.release()
        sys.exit(1)

    print(f"  OK: Frame capturado en {capture_time:.3f}s")
    print(f"  Dimensiones: {frame.shape[1]}x{frame.shape[0]} (ancho x alto)")
    print(f"  Canales: {frame.shape[2] if len(frame.shape) == 3 else 1}")
    print(f"  Dtype: {frame.dtype}")
    print(f"  Formato: BGR (compatible con OpenCV)")
    print()

    # Step 4: Save test frame
    print("[4/4] Guardando frame de prueba...")
    output_dir = OUTPUTS_DIR / "camera_test"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "test_frame.jpg"

    try:
        import cv2
        cv2.imwrite(str(output_path), frame)
        print(f"  OK: Frame guardado en {output_path.relative_to(PROJECT_ROOT)}")
    except Exception as e:
        print(f"  ERROR al guardar frame: {e}")
        camera_source.release()
        sys.exit(1)

    # Cleanup
    camera_source.release()

    # Summary
    print()
    print("=" * 60)
    print("  RESULTADO: Validación exitosa")
    print("=" * 60)
    print(f"  Resolución capturada: {frame.shape[1]}x{frame.shape[0]}")
    print(f"  Tiempo de captura:    {capture_time:.3f}s")
    print(f"  Frame guardado en:    {output_path.relative_to(PROJECT_ROOT)}")
    print(f"  Formato de salida:    JPEG (BGR → OpenCV compatible)")
    print()
    print("  La cámara está lista para integración con el pipeline.")
    print()


if __name__ == "__main__":
    validate_camera()
