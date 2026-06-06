from __future__ import annotations

import cv2

from config.settings import VIDEOS_DIR
from pipeline_core.capture_logic import should_capture_new_image


def main():
    print("=== SMOKE TEST CAPTURE ===")

    video_files = sorted(
        [
            p for p in VIDEOS_DIR.iterdir()
            if p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"}
        ]
    )

    if not video_files:
        print(f"[WARN] No se encontraron videos en: {VIDEOS_DIR}")
        return

    test_video_path = video_files[0]
    print("Video de prueba:", test_video_path)

    cap = cv2.VideoCapture(str(test_video_path))
    if not cap.isOpened():
        print(f"[ERROR] No se pudo abrir el video: {test_video_path}")
        return

    ret, reference_frame = cap.read()
    if not ret or reference_frame is None:
        print("[ERROR] No se pudo leer el primer frame del video.")
        cap.release()
        return

    print("[OK] Frame de referencia cargado")

    frame_idx = 0
    frames_since_last_capture = 0
    samples_checked = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        frames_since_last_capture += 1

        trigger, metrics = should_capture_new_image(
            reference_frame,
            frame,
            frames_since_last_capture,
        )

        if frame_idx % 15 == 0:
            print(
                f"[FRAME {frame_idx}] "
                f"trigger={trigger} | "
                f"orb={metrics['orb_matches']:.0f} | "
                f"hist_diff={metrics['hist_diff']:.3f} | "
                f"cooldown_ok={metrics['cooldown_ok']:.0f} | "
                f"timeout_force={metrics['timeout_force']:.0f}"
            )
            samples_checked += 1

        if samples_checked >= 5:
            break

    cap.release()
    print("[OK] Smoke test de captura finalizado")


if __name__ == "__main__":
    main()