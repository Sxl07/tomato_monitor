"""Export service for generating local ZIP packages with data and images.

Generates a ZIP file containing structured JSON data, snapshot images,
and a manifest with metadata. Uses only Python stdlib (zipfile, json, pathlib).
"""

import json
import os
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class ExportResult:
    """Result of a ZIP export generation."""

    file_path: str
    file_size_bytes: int
    records_count: int
    images_count: int
    files_included: int
    files_missing: list
    manifest: dict
    status: str  # "completed" or "error"
    error_message: Optional[str] = None


def _serialize_value(value):
    """Convert a value to JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _entity_to_dict(entity, exclude_fields=None):
    """Convert a dataclass entity to a JSON-safe dict.

    Excludes specified fields (e.g., password_hash) and handles
    datetime serialization.
    """
    exclude = set(exclude_fields or [])
    raw = asdict(entity)
    result = {}
    for key, value in raw.items():
        if key in exclude:
            continue
        result[key] = _serialize_value(value)
    return result


class ExportService:
    """Generates local ZIP export packages with data and images."""

    def generate_export(
        self,
        greenhouses,
        modules,
        monitorings,
        metrics_by_monitoring: dict,
        snapshots_by_monitoring: dict,
        activity_types,
        activity_logs,
        output_dir: str = "outputs/exports",
        base_snapshots_dir: str = "outputs/monitorings",
        export_package=None,
    ) -> ExportResult:
        """Generate a ZIP file with all exportable data.

        Args:
            greenhouses: List of Greenhouse entities.
            modules: List of Module entities.
            monitorings: List of Monitoring entities.
            metrics_by_monitoring: Dict mapping monitoring_id to MonitoringMetrics or None.
            snapshots_by_monitoring: Dict mapping monitoring_id to list of Snapshot entities.
            activity_types: List of ActivityType entities.
            activity_logs: List of ActivityLog entities.
            output_dir: Directory to write the ZIP file.
            base_snapshots_dir: Base directory for snapshot images.
            export_package: Optional ExportPackage entity for metadata inclusion.

        Returns:
            ExportResult with file path, counts, and manifest.
        """
        try:
            return self._do_generate(
                greenhouses=greenhouses,
                modules=modules,
                monitorings=monitorings,
                metrics_by_monitoring=metrics_by_monitoring,
                snapshots_by_monitoring=snapshots_by_monitoring,
                activity_types=activity_types,
                activity_logs=activity_logs,
                output_dir=output_dir,
                base_snapshots_dir=base_snapshots_dir,
                export_package=export_package,
            )
        except Exception as e:
            return ExportResult(
                file_path="",
                file_size_bytes=0,
                records_count=0,
                images_count=0,
                files_included=0,
                files_missing=[],
                manifest={},
                status="error",
                error_message=str(e),
            )

    def _do_generate(
        self,
        greenhouses,
        modules,
        monitorings,
        metrics_by_monitoring,
        snapshots_by_monitoring,
        activity_types,
        activity_logs,
        output_dir,
        base_snapshots_dir,
        export_package=None,
    ) -> ExportResult:
        """Internal generation logic."""
        # Create output directory
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        zip_filename = f"tomato_monitor_export_{timestamp_str}.zip"
        zip_path = out_path / zip_filename

        records_count = 0
        images_count = 0
        files_included = 0
        files_missing = []

        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            # --- Data JSON files ---

            # Greenhouses
            gh_data = [_entity_to_dict(g) for g in greenhouses]
            zf.writestr("data/greenhouses.json", json.dumps(gh_data, ensure_ascii=False, indent=2))
            records_count += len(gh_data)

            # Modules
            mod_data = [_entity_to_dict(m) for m in modules]
            zf.writestr("data/modules.json", json.dumps(mod_data, ensure_ascii=False, indent=2))
            records_count += len(mod_data)

            # Monitorings
            mon_data = [_entity_to_dict(m) for m in monitorings]
            zf.writestr("data/monitorings.json", json.dumps(mon_data, ensure_ascii=False, indent=2))
            records_count += len(mon_data)

            # Metrics
            metrics_data = []
            for monitoring in monitorings:
                m_id = monitoring.id
                met = metrics_by_monitoring.get(m_id)
                if met is not None:
                    metrics_data.append(_entity_to_dict(met))
            zf.writestr("data/metrics.json", json.dumps(metrics_data, ensure_ascii=False, indent=2))
            records_count += len(metrics_data)

            # Snapshots metadata
            all_snapshots_data = []
            for monitoring in monitorings:
                m_id = monitoring.id
                snaps = snapshots_by_monitoring.get(m_id, [])
                for s in snaps:
                    all_snapshots_data.append(_entity_to_dict(s))
            zf.writestr("data/snapshots.json", json.dumps(all_snapshots_data, ensure_ascii=False, indent=2))
            records_count += len(all_snapshots_data)

            # Activity types
            at_data = [_entity_to_dict(at) for at in activity_types]
            zf.writestr("data/activity_types.json", json.dumps(at_data, ensure_ascii=False, indent=2))
            records_count += len(at_data)

            # Activity logs
            al_data = [_entity_to_dict(al) for al in activity_logs]
            zf.writestr("data/activity_logs.json", json.dumps(al_data, ensure_ascii=False, indent=2))
            records_count += len(al_data)

            # Export package metadata
            export_pkg_data = {}
            if export_package is not None:
                export_pkg_data = _entity_to_dict(export_package, exclude_fields=["file_path", "manifest_json"])
            else:
                export_pkg_data = {
                    "generated_at": now.isoformat(),
                    "export_version": "1.0",
                    "scope": "full",
                }
            zf.writestr("data/export_package.json", json.dumps(export_pkg_data, ensure_ascii=False, indent=2))

            # --- Snapshot images (raw + annotated) ---
            base_path = Path(base_snapshots_dir)

            for monitoring in monitorings:
                m_id = monitoring.id
                snaps = snapshots_by_monitoring.get(m_id, [])

                for snapshot in snaps:
                    frame_idx = snapshot.frame_index
                    img_filename = f"snapshot_{frame_idx:06d}.jpg"

                    # Prefer snapshot.image_path as authoritative source for raw
                    raw_path = None
                    if hasattr(snapshot, "image_path") and snapshot.image_path:
                        # image_path is stored relative to project root (e.g. "outputs/monitorings/1/snapshots/raw/...")
                        # base_path corresponds to base_snapshots_dir which is outputs/monitorings
                        # So we strip the "outputs/monitorings/" prefix to get a relative path from base_path
                        rel_candidate = snapshot.image_path
                        # Normalize: strip common prefix patterns
                        for prefix in ("outputs/monitorings/", "outputs\\monitorings\\"):
                            if rel_candidate.startswith(prefix):
                                rel_candidate = rel_candidate[len(prefix):]
                                break
                        candidate_path = base_path / rel_candidate
                        if candidate_path.exists():
                            # Verify containment
                            try:
                                candidate_path.resolve().relative_to(base_path.resolve())
                                raw_path = candidate_path
                            except ValueError:
                                raw_path = None  # Traversal attempt, fall through

                    # Fallback: reconstruct from frame_index
                    if raw_path is None:
                        raw_path = base_path / str(m_id) / "snapshots" / "raw" / img_filename

                    # Annotated path derived from same filename
                    annotated_path = base_path / str(m_id) / "annotated_snapshots" / img_filename

                    image_added = False

                    if annotated_path.exists():
                        arcname = f"images/monitoring_{m_id}/annotated/{img_filename}"
                        zf.write(str(annotated_path), arcname)
                        images_count += 1
                        files_included += 1
                        image_added = True

                    if raw_path.exists():
                        arcname = f"images/monitoring_{m_id}/raw/{img_filename}"
                        zf.write(str(raw_path), arcname)
                        if not image_added:
                            images_count += 1
                        files_included += 1
                        image_added = True

                    if not image_added:
                        files_missing.append(
                            f"monitoring_{m_id}/{img_filename}"
                        )

            # --- Pipeline metrics JSON (per monitoring) ---
            for monitoring in monitorings:
                m_id = monitoring.id
                pipeline_metrics_path = base_path / str(m_id) / "reports" / "pipeline_metrics.json"
                if pipeline_metrics_path.exists():
                    arcname = f"reports/monitoring_{m_id}/pipeline_metrics.json"
                    zf.write(str(pipeline_metrics_path), arcname)
                    files_included += 1

            # --- Manifest ---
            manifest = {
                "export_id": getattr(export_package, 'id', None) if export_package else None,
                "app_name": "Tomato Monitor",
                "export_version": "1.0",
                "generated_at": now.isoformat(),
                "counts": {
                    "greenhouses": len(greenhouses),
                    "modules": len(modules),
                    "monitorings": len(monitorings),
                    "metrics": len(metrics_data),
                    "snapshots": len(all_snapshots_data),
                    "activity_types": len(activity_types),
                    "activity_logs": len(activity_logs),
                    "images_included": images_count,
                    "files_included": files_included,
                    "files_missing": len(files_missing),
                    "records_total": records_count,
                },
                "included_sections": [
                    "greenhouses", "modules", "monitorings", "monitoring_metrics",
                    "snapshots_metadata", "activity_types", "activity_logs",
                    "snapshot_images", "pipeline_metrics", "export_package",
                ],
                "notes": "Exportación local generada desde datos disponibles en el dispositivo.",
                "files_missing": files_missing,
                "warnings": [],
            }

            if files_missing:
                manifest["warnings"].append(
                    f"{len(files_missing)} imagen(es) no encontrada(s) en disco"
                )

            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        # Get final file size
        file_size = os.path.getsize(str(zip_path))

        return ExportResult(
            file_path=str(zip_path),
            file_size_bytes=file_size,
            records_count=records_count,
            images_count=images_count,
            files_included=files_included,
            files_missing=files_missing,
            manifest=manifest,
            status="completed",
        )
