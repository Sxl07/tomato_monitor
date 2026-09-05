from __future__ import annotations

import time
from collections import Counter
import cv2

from src.infrastructure.config.settings import VIDEOS_DIR, OUTPUTS_DIR

from src.infrastructure.vision.capture_gate import should_run_detector_by_scene_change
from src.infrastructure.vision.detector_decision import decide_run_detector
from src.infrastructure.vision.pipeline_orchestrator import build_pipeline_components, process_frame
from src.infrastructure.vision.visual_tracker import OpticalFlowVisualTracker

from src.infrastructure.persistence.local.file_utils import write_csv, save_image


VALID_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv"}


def draw_frame_annotations(frame_bgr, frame_result: dict):
    drawn = frame_bgr.copy()

    detector_ran = frame_result.get("detector_ran", False)

    for det in frame_result.get("detections", []):
        x1, y1, x2, y2 = det["bbox"]
        track_id = det["track_id"]
        score = det["det_score"]

        health = det.get("health_result") or {}
        maturity = det.get("maturity_result") or {}

        health_label = health.get("label", "-")
        usda_stage = maturity.get("usda_stage", "-")
        reused = det.get("reused_previous_result", False)
        propagated = det.get("propagated", False)

        if detector_ran:
            color = (0, 255, 255) if det["is_new_track"] else (255, 0, 255)
        else:
            color = (255, 165, 0) if propagated else (180, 180, 180)

        if reused and detector_ran:
            color = (180, 180, 180)

        cv2.rectangle(drawn, (x1, y1), (x2, y2), color, 2)

        src_label = "DET" if detector_ran else "FLOW"
        label_1 = f"{src_label} ID:{track_id} score:{score:.2f}"
        label_2 = f"H:{health_label} M:{usda_stage}"

        cv2.putText(
            drawn,
            label_1,
            (x1, max(20, y1 - 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )
        cv2.putText(
            drawn,
            label_2,
            (x1, max(40, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )

    overlay_1 = (
        f"det={frame_result.get('detections_count', 0)} "
        f"tracked={frame_result.get('tracked_count', 0)} "
        f"new={frame_result.get('new_tracks_count', 0)} "
        f"reused={frame_result.get('reused_count', 0)}"
    )
    overlay_2 = (
        f"health={frame_result.get('health_executed_count', 0)} "
        f"maturity={frame_result.get('maturity_executed_count', 0)} "
        f"time={frame_result.get('times', {}).get('total_frame_sec', 0.0):.3f}s"
    )
    overlay_3 = (
        f"detector_ran={frame_result.get('detector_ran', False)} "
        f"reason={frame_result.get('detector_reason', '-')}"
    )

    cv2.putText(drawn, overlay_1, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cv2.putText(drawn, overlay_2, (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.putText(drawn, overlay_3, (15, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

    return drawn


def build_skipped_frame_result(
    frame_name: str,
    frame_idx: int,
    propagated_detections: list[dict],
    propagation_sec: float,
    total_frame_sec: float,
):
    return {
        "frame_idx": frame_idx,
        "image_name": frame_name,
        "detections_count": len(propagated_detections),
        "tracked_count": len(propagated_detections),
        "health_executed_count": 0,
        "maturity_executed_count": 0,
        "reused_count": len(propagated_detections),
        "new_tracks_count": 0,
        "times": {
            "detection_sec": 0.0,
            "tracking_sec": 0.0,
            "propagation_sec": propagation_sec,
            "total_frame_sec": total_frame_sec,
        },
        "detections": propagated_detections,
        "detector_ran": False,
        "detector_reason": "skipped",
    }


def save_detection_snapshot(
    frame,
    drawn,
    result: dict,
    frame_idx: int,
    session_dir,
    save_detection_crops: bool,
):
    snapshots_dir = session_dir / "detection_snapshots"
    raw_dir = snapshots_dir / "raw_frames"
    annotated_dir = snapshots_dir / "annotated_frames"
    crops_dir = snapshots_dir / "crops"

    frame_stem = f"frame_{frame_idx:06d}"

    save_image(frame, raw_dir / f"{frame_stem}.jpg")
    save_image(drawn, annotated_dir / f"{frame_stem}.jpg")

    if save_detection_crops:
        for det in result.get("detections", []):
            x1, y1, x2, y2 = det["bbox"]
            track_id = det["track_id"]

            crop = frame[y1:y2, x1:x2].copy()
            if crop.size == 0:
                continue

            save_image(
                crop,
                crops_dir / frame_stem / f"track_{track_id:03d}.jpg",
            )


def summarize_results(
    strategy_name: str,
    video_name: str,
    per_frame_rows: list[dict],
    per_detection_rows: list[dict],
    detector_runs: int,
    detector_skips: int,
    detector_reason_counts: Counter,
):
    total_frames = len(per_frame_rows)
    total_wall_time = sum(r["total_frame_sec"] for r in per_frame_rows)

    detector_frame_rows = [r for r in per_frame_rows if r["detector_ran"]]
    skipped_frame_rows = [r for r in per_frame_rows if not r["detector_ran"]]

    unique_tracks = sorted({r["track_id"] for r in per_detection_rows})
    total_health_exec = sum(1 for r in per_detection_rows if r["health_executed"])
    total_maturity_exec = sum(1 for r in per_detection_rows if r["maturity_executed"])
    total_propagated = sum(1 for r in per_detection_rows if r["propagated"])

    return {
        "strategy_name": strategy_name,
        "video_name": video_name,
        "total_frames": total_frames,
        "detector_runs": detector_runs,
        "detector_skips": detector_skips,
        "detector_run_ratio": detector_runs / total_frames if total_frames else 0.0,
        "avg_total_frame_sec": total_wall_time / total_frames if total_frames else 0.0,
        "effective_fps": total_frames / total_wall_time if total_wall_time > 0 else 0.0,
        "avg_detector_frame_sec": (
            sum(r["total_frame_sec"] for r in detector_frame_rows) / len(detector_frame_rows)
            if detector_frame_rows else 0.0
        ),
        "avg_skipped_frame_sec": (
            sum(r["total_frame_sec"] for r in skipped_frame_rows) / len(skipped_frame_rows)
            if skipped_frame_rows else 0.0
        ),
        "avg_propagation_sec": (
            sum(r.get("propagation_sec", 0.0) for r in skipped_frame_rows) / len(skipped_frame_rows)
            if skipped_frame_rows else 0.0
        ),
        "unique_tracks_detected": len(unique_tracks),
        "total_health_executions": total_health_exec,
        "total_maturity_executions": total_maturity_exec,
        "total_propagated_rows": total_propagated,
        "reason_first_frame": detector_reason_counts.get("first_frame", 0),
        "reason_scene_gate": detector_reason_counts.get("scene_gate", 0),
        "reason_scene_gate_blocked": detector_reason_counts.get("scene_gate_blocked", 0),
        "reason_max_gap_force": detector_reason_counts.get("max_gap_force", 0),
        "reason_min_gap_ready": detector_reason_counts.get("min_gap_ready", 0),
        "reason_cooldown": detector_reason_counts.get("cooldown", 0),
        "reason_full_detection": detector_reason_counts.get("full_detection", 0),
    }


def run_video_inspection(
    strategy_name: str,
    enable_sparse_detection: bool,
    enable_flow_propagation: bool,
    use_scene_gate: bool,
    min_frames_between_detections: int,
    max_frames_without_detection: int,
    force_detect_on_first_frame: bool = True,
    save_detection_snapshots: bool = True,
    save_detection_crops: bool = True,
    save_annotated_video: bool = True,
    video_index: int = 0,
    max_frames_to_process: int | None = None,
    verbose: bool = True,
):
    video_files = sorted([p for p in VIDEOS_DIR.iterdir() if p.suffix.lower() in VALID_VIDEO_EXTS])

    if not video_files:
        raise FileNotFoundError(f"No se encontraron videos en: {VIDEOS_DIR}")

    video_path = video_files[video_index]

    if verbose:
        print(f"=== RUN VIDEO INSPECTION: {strategy_name} ===")
        print(f"Video: {video_path.name}")
        print(f"enable_sparse_detection={enable_sparse_detection}")
        print(f"enable_flow_propagation={enable_flow_propagation}")
        print(f"use_scene_gate={use_scene_gate}")
        print(f"min_frames_between_detections={min_frames_between_detections}")
        print(f"max_frames_without_detection={max_frames_without_detection}")
        print(f"max_frames_to_process={max_frames_to_process}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"No se pudo abrir el video: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 20.0

    components = build_pipeline_components()
    visual_tracker = OpticalFlowVisualTracker()

    session_dir = OUTPUTS_DIR / "experiments" / strategy_name
    reports_dir = session_dir / "reports"
    annotated_dir = session_dir / "annotated_video"
    reports_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir.mkdir(parents=True, exist_ok=True)

    output_video_path = annotated_dir / f"{video_path.stem}_{strategy_name}.mp4"
    writer = None
    if save_annotated_video:
        writer = cv2.VideoWriter(
            str(output_video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )

    per_frame_rows = []
    per_detection_rows = []

    frame_idx = 0
    frames_since_last_detection = 0
    last_detection_frame = None

    detector_runs = 0
    detector_skips = 0
    detector_reason_counts = Counter()

    while True:
        if max_frames_to_process is not None and frame_idx >= max_frames_to_process:
            break

        ret, frame = cap.read()
        if not ret:
            break

        frame_name = f"{video_path.stem}_frame_{frame_idx:06d}"
        frame_wall_start = time.perf_counter()

        decision = decide_run_detector(
            frame_idx=frame_idx,
            frames_since_last_detection=frames_since_last_detection,
            last_detection_frame=last_detection_frame,
            current_frame=frame,
            enable_sparse_detection=enable_sparse_detection,
            use_scene_gate=use_scene_gate,
            min_frames_between_detections=min_frames_between_detections,
            max_frames_without_detection=max_frames_without_detection,
            force_detect_on_first_frame=force_detect_on_first_frame,
            scene_gate_fn=should_run_detector_by_scene_change,
        )
        run_detector = decision.run_detector
        detector_reason = decision.reason

        detector_reason_counts[detector_reason] += 1

        if run_detector:
            result = process_frame(frame, components, frame_name)
            result["detector_ran"] = True
            result["detector_reason"] = detector_reason
            result["times"]["propagation_sec"] = 0.0

            if enable_flow_propagation:
                visual_tracker.update_from_detection_result(frame, result["detections"])

            last_detection_frame = frame.copy()
            frames_since_last_detection = 0
            detector_runs += 1

        else:
            detector_skips += 1
            frames_since_last_detection += 1

            propagation_start = time.perf_counter()
            propagated_detections = visual_tracker.propagate(frame) if enable_flow_propagation else []
            propagation_sec = time.perf_counter() - propagation_start

            result = build_skipped_frame_result(
                frame_name=frame_name,
                frame_idx=frame_idx,
                propagated_detections=propagated_detections,
                propagation_sec=propagation_sec,
                total_frame_sec=0.0,
            )
            result["detector_reason"] = detector_reason

        drawn = draw_frame_annotations(frame, result)

        if writer is not None:
            writer.write(drawn)

        if result["detector_ran"] and save_detection_snapshots:
            save_detection_snapshot(
                frame=frame,
                drawn=drawn,
                result=result,
                frame_idx=frame_idx,
                session_dir=session_dir,
                save_detection_crops=save_detection_crops,
            )

        wall_time = time.perf_counter() - frame_wall_start
        result["times"]["total_frame_sec"] = wall_time

        per_frame_rows.append({
            "frame_idx": frame_idx,
            "frame_name": frame_name,
            "detector_ran": result["detector_ran"],
            "detector_reason": result["detector_reason"],
            "detections_count": result["detections_count"],
            "tracked_count": result["tracked_count"],
            "new_tracks_count": result["new_tracks_count"],
            "reused_count": result["reused_count"],
            "health_executed_count": result["health_executed_count"],
            "maturity_executed_count": result["maturity_executed_count"],
            "detection_sec": result["times"]["detection_sec"],
            "tracking_sec": result["times"]["tracking_sec"],
            "propagation_sec": result["times"].get("propagation_sec", 0.0),
            "total_frame_sec": result["times"]["total_frame_sec"],
        })

        for det in result["detections"]:
            health = det.get("health_result") or {}
            maturity = det.get("maturity_result") or {}
            x1, y1, x2, y2 = det["bbox"]

            if result["detector_ran"]:
                times = det["times"]
                crop_sec = times["crop_sec"]
                health_sec = times["health_sec"]
                maturity_sec = times["maturity_sec"]
                detection_pipeline_sec = times["detection_pipeline_sec"]
                detection_id = det["detection_id"]
                health_executed = det["health_executed"]
                maturity_executed = det["maturity_executed"]
                is_new_track = det["is_new_track"]
                track_hits = det["track_hits"]
                reused_previous_result = det["reused_previous_result"]
            else:
                crop_sec = 0.0
                health_sec = 0.0
                maturity_sec = 0.0
                detection_pipeline_sec = 0.0
                detection_id = -1
                health_executed = False
                maturity_executed = False
                is_new_track = False
                track_hits = 0
                reused_previous_result = True

            per_detection_rows.append({
                "frame_idx": frame_idx,
                "frame_name": frame_name,
                "detector_ran": result["detector_ran"],
                "track_id": det["track_id"],
                "is_new_track": is_new_track,
                "track_hits": track_hits,
                "reused_previous_result": reused_previous_result,
                "detection_id": detection_id,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "det_score": det["det_score"],
                "health_executed": health_executed,
                "health_label": health.get("label"),
                "health_confidence": health.get("confidence"),
                "prob_healthy": health.get("prob_healthy"),
                "prob_unhealthy": health.get("prob_unhealthy"),
                "maturity_executed": maturity_executed,
                "usda_stage": maturity.get("usda_stage"),
                "maturity_percent": maturity.get("maturity_percent"),
                "maturity_confidence": maturity.get("confidence"),
                "maturity_warning": maturity.get("warning"),
                "crop_sec": crop_sec,
                "health_sec": health_sec,
                "maturity_sec": maturity_sec,
                "detection_pipeline_sec": detection_pipeline_sec,
                "propagated": not result["detector_ran"],
            })

        if verbose and frame_idx % 15 == 0:
            print(
                f"[FRAME {frame_idx}] "
                f"detector_ran={result['detector_ran']} | "
                f"reason={result['detector_reason']} | "
                f"det={result['detections_count']} | "
                f"tracked={result['tracked_count']} | "
                f"time={result['times']['total_frame_sec']:.3f}s"
            )

        frame_idx += 1

    cap.release()
    if writer is not None:
        writer.release()

    write_csv(per_frame_rows, reports_dir / "per_frame.csv")
    write_csv(per_detection_rows, reports_dir / "per_detection.csv")

    summary = summarize_results(
        strategy_name=strategy_name,
        video_name=video_path.name,
        per_frame_rows=per_frame_rows,
        per_detection_rows=per_detection_rows,
        detector_runs=detector_runs,
        detector_skips=detector_skips,
        detector_reason_counts=detector_reason_counts,
    )
    write_csv([summary], reports_dir / "summary.csv")

    if verbose:
        print("\n[OK] Video inspection finalizada")
        print(f"[OK] Detector runs: {detector_runs}")
        print(f"[OK] Detector skips: {detector_skips}")
        print(f"[OK] Reason counts: {dict(detector_reason_counts)}")
        if writer is not None:
            print(f"[OK] Video anotado en: {output_video_path}")
        print(f"[OK] Reportes en: {reports_dir}")
        print(f"[OK] Summary: {reports_dir / 'summary.csv'}")

    return summary