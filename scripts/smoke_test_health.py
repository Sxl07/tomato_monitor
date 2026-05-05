from __future__ import annotations

import cv2

from config.settings import (
    DEVICE,
    DETECTION_MODEL_PATH,
    HEALTH_MODEL_B_PATH,
    IMAGES_DIR,
)
from config.thresholds import (
    DETECTION_SCORE_THRESHOLD,
    HEALTH_B_THRESHOLD,
)

from pipeline_core.detector import (
    build_tomato_detector,
    run_detection,
    extract_detection_dicts,
)
from pipeline_core.cropper import (
    clamp_box_xyxy,
    expand_box,
    crop_from_box,
    is_crop_large_enough,
)
from pipeline_core.health import (
    build_health_model_resnet,
    predict_health,
)


def main():
    print("=== SMOKE TEST HEALTH ===")
    print("Detection model:", DETECTION_MODEL_PATH)
    print("Health model:", HEALTH_MODEL_B_PATH)
    print("Device:", DEVICE)
    print("Detection threshold:", DETECTION_SCORE_THRESHOLD)
    print("Health threshold:", HEALTH_B_THRESHOLD)

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

    h, w = image.shape[:2]

    detector = build_tomato_detector()
    outputs = run_detection(detector, image)
    detections = extract_detection_dicts(outputs)

    print(f"[INFO] Detecciones encontradas: {len(detections)}")

    if not detections:
        print("[WARN] No hay detecciones para probar sanidad.")
        return

    first_det = detections[0]
    x1, y1, x2, y2 = first_det["bbox"]

    x1, y1, x2, y2 = clamp_box_xyxy(x1, y1, x2, y2, w, h)
    x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, w, h)

    crop = crop_from_box(image, (x1, y1, x2, y2))

    if not is_crop_large_enough(crop):
        print("[WARN] El crop detectado es demasiado pequeño para probar sanidad.")
        return

    health_model, health_transform = build_health_model_resnet()
    result = predict_health(health_model, health_transform, crop)

    print("[OK] Resultado de sanidad:")
    print(result)


if __name__ == "__main__":
    main()