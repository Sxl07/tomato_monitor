"""Owner-scoped inputs for the existing operational alert service."""

from app.dependencies import (
    get_export_package_repository,
    get_greenhouse_repository,
    get_module_repository,
    get_monitoring_repository,
)
from src.application.services.alert_service import AlertService
from src.domain.entities.operational_alert import OperationalAlert


def format_operational_alert(alert: OperationalAlert) -> dict:
    """Build the shared header/API presentation of one existing alert."""
    url = None
    if alert.alert_type in ("monitoring_pending", "monitoring_overdue"):
        if alert.module_id is not None:
            url = f"/modulos/{alert.module_id}"
    elif alert.alert_type == "analysis_error":
        if alert.monitoring_id is not None:
            url = f"/monitoreos/{alert.monitoring_id}/reporte"
        elif alert.module_id is not None:
            url = f"/modulos/{alert.module_id}"

    return {
        "severity": alert.severity,
        "title": alert.title,
        "message": alert.message,
        "url": url,
    }


def load_operational_alerts(
    request,
    user,
    *,
    greenhouses=None,
    modules=None,
    monitorings_by_module=None,
    export_packages=None,
):
    """Return the full severity-sorted list for one local owner.

    The dashboard supplies its already scoped inputs. Other pages use the same
    owner-rooted repository traversal through the header's read-only endpoint.
    Export inputs intentionally match the dashboard's pending-only selection.
    """
    supplied = (greenhouses, modules, monitorings_by_module, export_packages)
    if any(value is None for value in supplied):
        if not all(value is None for value in supplied):
            raise ValueError("Supply either all scoped alert inputs or none")
        greenhouse_repo = get_greenhouse_repository(request)
        module_repo = get_module_repository(request)
        monitoring_repo = get_monitoring_repository(request)
        export_repo = get_export_package_repository(request)

        greenhouses = greenhouse_repo.get_all_by_owner(user.id)
        modules = []
        monitorings_by_module = {}
        for greenhouse in greenhouses:
            for module in module_repo.get_by_greenhouse(greenhouse.id):
                modules.append(module)
                monitorings_by_module[module.id] = monitoring_repo.get_by_module(module.id)
        export_packages = [
            package for package in export_repo.list_by_user(user.id)
            if getattr(package, "status", None) == "pending"
        ]

    greenhouse_names_by_id = {greenhouse.id: greenhouse.name for greenhouse in greenhouses}
    return AlertService().compute_alerts(
        modules=modules,
        monitorings_by_module=monitorings_by_module,
        export_packages=export_packages,
        greenhouse_names_by_id=greenhouse_names_by_id,
    )
