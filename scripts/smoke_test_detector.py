from __future__ import annotations

import cv2

from config.settings import DETECTION_MODEL_PATH, DEVICE, IMAGES_DIR
from config.thresholds import DETECTION_SCORE_THRESHOLD
from pipeline_core.detector import (
    build_tomato_detector,
    run_detection,
    extract_detection_dicts,
)


def main():
    print("Modelo:", DETECTION_MODEL_PATH)
    print("Device:", DEVICE)
    print("Threshold:", DETECTION_SCORE_THRESHOLD)

    image_files = sorted(
        [
            p for p in IMAGES_DIR.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        ]
    )

    if not image_files:
        print(f"[WARN] No se encontraron imágenes en: {IMAGES_DIR}")
        return

    test_image_path = image_files[0]
    print("Imagen de prueba:", test_image_path)

    image = cv2.imread(str(test_image_path))
    if image is None:
        print(f"[ERROR] No se pudo leer la imagen: {test_image_path}")
        return

    predictor = build_tomato_detector()
    outputs = run_detection(predictor, image)
    detections = extract_detection_dicts(outputs)

    print(f"[OK] Detecciones encontradas: {len(detections)}")

    for det in detections[:5]:
        print(det)


if __name__ == "__main__":
    main()