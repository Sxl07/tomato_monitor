from __future__ import annotations

from storage.csv_store import write_csv
from scripts.run_pipeline_video import run_video_pipeline


def main():
    print("=== COMPARE VIDEO STRATEGIES ===")

    strategies = [
        {
            "strategy_name": "full_detection",
            "enable_sparse_detection": False,
            "enable_flow_propagation": False,
            "use_scene_gate": False,
            "min_frames_between_detections": 0,
            "max_frames_without_detection": 0,
        },
        {
            "strategy_name": "sparse_honest",
            "enable_sparse_detection": True,
            "enable_flow_propagation": False,
            "use_scene_gate": True,
            "min_frames_between_detections": 5,
            "max_frames_without_detection": 12,
        },
        {
            "strategy_name": "sparse_flow",
            "enable_sparse_detection": True,
            "enable_flow_propagation": True,
            "use_scene_gate": True,
            "min_frames_between_detections": 5,
            "max_frames_without_detection": 12,
        },
    ]

    summaries = []

    for cfg in strategies:
        print(f"\n--- Running: {cfg['strategy_name']} ---")
        summary = run_video_pipeline(
            strategy_name=cfg["strategy_name"],
            enable_sparse_detection=cfg["enable_sparse_detection"],
            enable_flow_propagation=cfg["enable_flow_propagation"],
            use_scene_gate=cfg["use_scene_gate"],
            min_frames_between_detections=cfg["min_frames_between_detections"],
            max_frames_without_detection=cfg["max_frames_without_detection"],
            force_detect_on_first_frame=True,
            save_detection_snapshots=True,
            save_detection_crops=True,
            video_index=0,
            verbose=True,
        )
        summaries.append(summary)

    output_path = None
    if summaries:
        from config.settings import OUTPUTS_DIR
        output_path = OUTPUTS_DIR / "experiments" / "comparison_summary.csv"
        write_csv(summaries, output_path)

    print("\n=== COMPARISON FINISHED ===")
    if output_path:
        print(f"[OK] Summary comparativo en: {output_path}")


if __name__ == "__main__":
    main()