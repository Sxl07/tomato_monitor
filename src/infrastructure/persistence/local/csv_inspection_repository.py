from __future__ import annotations

import csv
from pathlib import Path

from src.domain.entities.fruit_detection import FruitDetection
from src.domain.entities.health_assessment import HealthAssessment
from src.domain.entities.inspection_result import InspectionResult
from src.domain.entities.maturity_assessment import MaturityAssessment
from src.domain.repositories.inspection_repository import InspectionRepository
from src.domain.value_objects.bounding_box import BoundingBox
from src.domain.value_objects.frame_reference import FrameReference


class CsvInspectionRepository(InspectionRepository):
    def __init__(self, experiments_dir: Path) -> None:
        self.experiments_dir = Path(experiments_dir)
        self.experiments_dir.mkdir(parents=True, exist_ok=True)

    def save_result(self, result: InspectionResult) -> None:
        self.save_many([result])

    def save_many(self, results: list[InspectionResult]) -> None:
        if not results:
            return

        session_id = results[0].detection.session_id
        csv_path = self._csv_path_for_session(session_id)

        existing_rows = self._read_rows(csv_path)
        new_rows = [r.to_record_dict() for r in results]
        all_rows = existing_rows + new_rows

        self._write_rows(csv_path, all_rows)

    def list_by_session(self, session_id: str) -> list[InspectionResult]:
        csv_path = self._csv_path_for_session(session_id)
        rows = self._read_rows(csv_path)

        results: list[InspectionResult] = []
        for row in rows:
            frame_ref = FrameReference(
                source_name=row["source_name"],
                frame_index=int(row["frame_index"]),
            )
            bbox = BoundingBox(
                x1=int(row["x1"]),
                y1=int(row["y1"]),
                x2=int(row["x2"]),
                y2=int(row["y2"]),
            )
            detection = FruitDetection(
                session_id=row["session_id"],
                frame_ref=frame_ref,
                detection_id=int(row["detection_id"]),
                track_id=int(row["track_id"]),
                bbox=bbox,
                detection_score=float(row["detection_score"]),
                class_id=int(row.get("class_id", 0)),
                is_new_track=self._to_bool(row.get("is_new_track", False)),
                track_hits=int(row.get("track_hits", 0)),
                reused_previous_result=self._to_bool(row.get("reused_previous_result", False)),
                propagated=self._to_bool(row.get("propagated", False)),
            )

            health_assessment = None
            if row.get("health_label"):
                health_assessment = HealthAssessment(
                    label=row["health_label"],
                    confidence=float(row["health_confidence"]),
                    prob_healthy=float(row["prob_healthy"]),
                    prob_unhealthy=float(row["prob_unhealthy"]),
                )

            maturity_assessment = None
            if row.get("usda_stage"):
                maturity_assessment = MaturityAssessment(
                    usda_stage=row["usda_stage"],
                    maturity_percent=float(row["maturity_percent"]),
                    confidence=float(row["maturity_confidence"]),
                    occlusion_ratio=float(row.get("occlusion_ratio", 0.0) or 0.0),
                    visible_fruit_ratio=float(row.get("visible_fruit_ratio", 1.0) or 1.0),
                    warning=row.get("maturity_warning") or None,
                )

            results.append(
                InspectionResult(
                    detection=detection,
                    health_assessment=health_assessment,
                    maturity_assessment=maturity_assessment,
                )
            )

        return results

    def _csv_path_for_session(self, session_id: str) -> Path:
        reports_dir = self.experiments_dir / session_id / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        return reports_dir / "inspection_results.csv"

    def _read_rows(self, csv_path: Path) -> list[dict]:
        if not csv_path.exists():
            return []

        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _write_rows(self, csv_path: Path, rows: list[dict]) -> None:
        if not rows:
            return

        fieldnames = list(rows[0].keys())

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _to_bool(self, value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}