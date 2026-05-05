from __future__ import annotations

from pathlib import Path
import cv2

from config.settings import IMAGES_DIR, OUTPUTS_DIR
from pipeline_core.orchestrator import build_pipeline_components, process_frame
from storage.csv_store import write_csv
from storage.file_store import save_image, save_mask


def main():
    print("=== RUN PIPELINE IMAGES ===")

    image_files = sorted(
        [
            p for p in IMAGES_DIR.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        ]
    )

    if not image_files:
        print(f"[WARN] No se encontraron imágenes en: {IMAGES_DIR}")
        return

    components = build_pipeline_components()

    session_dir = OUTPUTS_DIR / "pipeline_v1_images"
    crops_dir = session_dir / "crops"
    masked_dir = session_dir / "masked_crops"
    masks_dir = session_dir / "masks"
    reports_dir = session_dir / "reports"

    per_image_rows = []
    per_detection_rows = []

    for image_path in image_files:
        print(f"\nProcesando: {image_path.name}")

        image = cv2.imread(str(image_path))
        if image is None:
            print(f"[WARN] No se pudo leer: {image_path}")
            continue

        result = process_frame(image, components, image_path.name)

        per_image_rows.append({
            "image_name": result["image_name"],
            "detections_count": result["detections_count"],
            "tracked_count": result["tracked_count"],
            "detection_sec": result["times"]["detection_sec"],
            "tracking_sec": result["times"]["tracking_sec"],
            "total_frame_sec": result["times"]["total_frame_sec"],
        })

        for det in result["detections"]:
            x1, y1, x2, y2 = det["bbox"]
            crop = image[y1:y2, x1:x2].copy()

            crop_path = crops_dir / image_path.stem / f"track_{det['track_id']:03d}.jpg"
            save_image(crop, crop_path)

            mask_path = None
            masked_crop_path = None

            if det.get("fruit_mask") is not None:
                mask_path = masks_dir / image_path.stem / f"track_{det['track_id']:03d}.png"
                save_mask(det["fruit_mask"], mask_path)

            if det.get("masked_crop") is not None:
                masked_crop_path = masked_dir / image_path.stem / f"track_{det['track_id']:03d}.jpg"
                save_image(det["masked_crop"], masked_crop_path)

            health = det["health_result"] or {}
            maturity = det["maturity_result"] or {}
            times = det["times"]

            per_detection_rows.append({
                "image_name": det["image_name"],
                "track_id": det["track_id"],
                "is_new_track": det["is_new_track"],
                "track_hits": det["track_hits"],
                "detection_id": det["detection_id"],
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "det_score": det["det_score"],
                "health_executed": det["health_executed"],
                "health_label": health.get("label"),
                "health_confidence": health.get("confidence"),
                "prob_healthy": health.get("prob_healthy"),
                "prob_unhealthy": health.get("prob_unhealthy"),
                "maturity_executed": det["maturity_executed"],
                "usda_stage": maturity.get("usda_stage"),
                "maturity_percent": maturity.get("maturity_percent"),
                "maturity_confidence": maturity.get("confidence"),
                "maturity_warning": maturity.get("warning"),
                "crop_sec": times["crop_sec"],
                "health_sec": times["health_sec"],
                "maturity_sec": times["maturity_sec"],
                "detection_pipeline_sec": times["detection_pipeline_sec"],
                "crop_path": str(crop_path),
                "mask_path": str(mask_path) if mask_path else "",
                "masked_crop_path": str(masked_crop_path) if masked_crop_path else "",
            })

        print(
            f"[OK] det={result['detections_count']} | "
            f"tracked={result['tracked_count']} | "
            f"time={result['times']['total_frame_sec']:.3f}s"
        )

    write_csv(per_image_rows, reports_dir / "per_image.csv")
    write_csv(per_detection_rows, reports_dir / "per_detection.csv")

    print("\n[OK] Pipeline por imágenes finalizado")
    print(f"[OK] Reportes en: {reports_dir}")


if __name__ == "__main__":
    main()