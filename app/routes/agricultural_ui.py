"""Agricultural UI router — farmer-facing screens for the Tomato Monitor.

This router provides all page endpoints for the agricultural interface,
optimized for the Raspberry Pi DSI 7" touchscreen (800×480).
Screens are rendered server-side with Jinja2 templates.
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError

from app.context_builders import (
    build_greenhouse_cards,
    build_module_cards,
    build_monitoring_history,
    build_report_metrics,
    build_snapshot_gallery,
    build_active_monitoring_context,
    _format_date_spanish,
    _format_time,
)
from src.application.validators import (
    ValidationError,
    validate_greenhouse_name,
    validate_module_name,
    validate_dimensions,
    validate_optional_dimensions,
    validate_notes,
)
from app.dependencies import (
    get_greenhouse_repository,
    get_module_repository,
    get_monitoring_repository,
    get_snapshot_repository,
    get_monitoring_metrics_repository,
    get_monitoring_service,
    get_activity_type_repository,
    get_activity_log_repository,
    get_export_package_repository,
    require_current_user_html,
)
from src.application.services.model_service import ModelService
from src.infrastructure.config.settings import DETECTION_MODEL_PATH
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.exceptions import DuplicateModuleError
from src.application.utils.jinja_filters import register_filters
from src.application.utils.timezone import bogota_to_utc

router = APIRouter(tags=["agricultural-ui"])
templates = Jinja2Templates(directory="app/templates")
register_filters(templates)


def _is_supabase_configured(request: Request) -> bool:
    """Check if Supabase remote sync is configured on this device."""
    config = getattr(request.app.state, "supabase_config", None)
    return config is not None and config.is_configured

import logging as _logging

_logger = _logging.getLogger(__name__)


def _parse_monitoring_frequency(value: str) -> int | None:
    """Parse monitoring frequency from form input.

    Returns a positive integer or None. Returns 7 (default) when input is empty.
    Returns None for invalid input (zero, negative, non-numeric).
    """
    if not value or not value.strip():
        return 7  # Default for tomato cherry
    try:
        freq = int(value.strip())
        if freq <= 0:
            return None  # Validation error
        return freq
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@router.get("/", response_class=RedirectResponse)
async def home(request: Request, user=Depends(require_current_user_html)):
    """Redirect home to the dashboard (or login if not auth)."""
    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(require_current_user_html)):
    """Dashboard: contextual overview of the system state."""
    from src.application.services.dashboard_service import DashboardService

    gh_repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)
    activity_log_repo = get_activity_log_repository(request)
    activity_type_repo = get_activity_type_repository(request)
    export_repo = get_export_package_repository(request)

    greenhouses = gh_repo.get_all()
    modules = []
    monitorings_by_module: dict[int, list] = {}
    for gh in greenhouses:
        gh_modules = module_repo.get_by_greenhouse(gh.id)
        modules.extend(gh_modules)
        for m in gh_modules:
            monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    # Recent activities (enriched with type names)
    recent_logs = activity_log_repo.list_recent(limit=5)
    all_types = {t.id: t for t in activity_type_repo.list_all()}
    recent_activities = []
    for log in recent_logs:
        at = all_types.get(log.activity_type_id)
        recent_activities.append({
            "activity_type_name": at.name if at else "Desconocido",
            "category": at.category if at else "",
            "occurred_at": log.occurred_at,
            "module_id": log.module_id,
        })

    # Export packages
    export_packages = export_repo.list_pending()

    # Build context
    dashboard_service = DashboardService()
    context = dashboard_service.build_context(
        greenhouses=greenhouses,
        modules=modules,
        monitorings_by_module=monitorings_by_module,
        recent_activities=recent_activities,
        export_packages=export_packages,
    )

    return templates.TemplateResponse(request, "agricultural/dashboard.html", {
        "title": "Dashboard",
        "show_back": False,
        "supabase_configured": _is_supabase_configured(request),
        **context,
    })


# ---------------------------------------------------------------------------
# Greenhouse endpoints
# ---------------------------------------------------------------------------


@router.get("/invernaderos", response_class=HTMLResponse)
async def greenhouse_list(request: Request, user=Depends(require_current_user_html)):
    """Screen 1: Greenhouse List Screen."""

    repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)

    greenhouses = repo.get_all()

    # Build lookup dicts for context builder
    modules_by_gh: dict[int, list] = {}
    monitorings_by_module: dict[int, list] = {}
    all_modules: list = []
    for gh in greenhouses:
        modules = module_repo.get_by_greenhouse(gh.id)
        modules_by_gh[gh.id] = modules
        all_modules.extend(modules)
        for m in modules:
            monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    cards = build_greenhouse_cards(greenhouses, modules_by_gh, monitorings_by_module)

    # Compute operational alerts
    from src.application.services.alert_service import AlertService
    alert_service = AlertService()
    alerts = alert_service.compute_alerts(
        modules=all_modules,
        monitorings_by_module=monitorings_by_module,
    )

    # Support error query param for flash-style messages
    error = request.query_params.get("error")

    return templates.TemplateResponse(request, "agricultural/greenhouse_list.html", {
        "title": "Mis Invernaderos",
        "greenhouses": cards,
        "alerts": alerts,
        "show_back": False,
        "error": error,
    })


@router.get("/invernaderos/crear", response_class=HTMLResponse)
def greenhouse_create_form(request: Request, user=Depends(require_current_user_html)):
    """Greenhouse creation form."""
    return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
        "title": "Crear Invernadero",
        "show_back": True,
        "back_url": "/invernaderos",
        "mode": "create",
        "name": "",
        "location": "",
        "errors": [],
    })


@router.post("/invernaderos/crear", response_class=HTMLResponse)
def greenhouse_create(request: Request, name: str = Form(...), location: str = Form(""), user=Depends(require_current_user_html)):
    """Process greenhouse creation."""
    errors = []
    try:
        validated_name = validate_greenhouse_name(name)
    except ValidationError as e:
        errors.append(e.message)
        return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
            "title": "Crear Invernadero",
            "show_back": True,
            "back_url": "/invernaderos",
            "mode": "create",
            "name": name,
            "location": location,
            "errors": errors,
        })

    repo = get_greenhouse_repository(request)
    try:
        greenhouse = repo.create(Greenhouse(name=validated_name, location=location.strip() or None))
    except IntegrityError:
        errors.append("Ya existe un invernadero con ese nombre.")
        return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
            "title": "Crear Invernadero",
            "show_back": True,
            "back_url": "/invernaderos",
            "mode": "create",
            "name": name,
            "location": location,
            "errors": errors,
        })

    return RedirectResponse(url=f"/invernaderos/{greenhouse.id}", status_code=303)


@router.get("/invernaderos/{id}", response_class=HTMLResponse)
async def greenhouse_detail(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 2: Greenhouse Detail Screen."""

    repo = get_greenhouse_repository(request)
    greenhouse = repo.get_by_id(id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)

    modules = module_repo.get_by_greenhouse(id)
    monitorings_by_module: dict[int, list] = {}
    for m in modules:
        monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    cards = build_module_cards(modules, monitorings_by_module)

    return templates.TemplateResponse(request, "agricultural/greenhouse_detail.html", {
        "title": greenhouse.name,
        "greenhouse": greenhouse,
        "modules": cards,
        "show_back": True,
        "back_url": "/invernaderos",
    })


@router.get("/invernaderos/{id}/editar", response_class=HTMLResponse)
def greenhouse_edit_form(request: Request, id: int, user=Depends(require_current_user_html)):
    """Greenhouse edit form."""
    repo = get_greenhouse_repository(request)
    greenhouse = repo.get_by_id(id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
        "title": "Editar Invernadero",
        "show_back": True,
        "back_url": f"/invernaderos/{id}",
        "mode": "edit",
        "greenhouse_id": id,
        "name": greenhouse.name,
        "location": greenhouse.location or "",
        "errors": [],
    })


@router.post("/invernaderos/{id}/editar", response_class=HTMLResponse)
def greenhouse_edit(request: Request, id: int, name: str = Form(...), location: str = Form(""), user=Depends(require_current_user_html)):
    """Process greenhouse edit."""
    errors = []
    try:
        validated_name = validate_greenhouse_name(name)
    except ValidationError as e:
        errors.append(e.message)
        return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
            "title": "Editar Invernadero",
            "show_back": True,
            "back_url": f"/invernaderos/{id}",
            "mode": "edit",
            "greenhouse_id": id,
            "name": name,
            "location": location,
            "errors": errors,
        })

    repo = get_greenhouse_repository(request)
    greenhouse = repo.get_by_id(id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    try:
        repo.update(id, name=validated_name, location=location.strip() or None)
    except IntegrityError:
        errors.append("Ya existe un invernadero con ese nombre.")
        return templates.TemplateResponse(request, "agricultural/greenhouse_form.html", {
            "title": "Editar Invernadero",
            "show_back": True,
            "back_url": f"/invernaderos/{id}",
            "mode": "edit",
            "greenhouse_id": id,
            "name": name,
            "location": location,
            "errors": errors,
        })

    return RedirectResponse(url=f"/invernaderos/{id}", status_code=303)


@router.post("/invernaderos/{id}/eliminar")
def greenhouse_delete(request: Request, id: int, user=Depends(require_current_user_html)):
    """Delete greenhouse (with confirmation handled client-side)."""
    repo = get_greenhouse_repository(request)
    greenhouse = repo.get_by_id(id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)
    repo.delete(id)
    return RedirectResponse(url="/invernaderos", status_code=303)


# ---------------------------------------------------------------------------
# Module endpoints
# ---------------------------------------------------------------------------


@router.get("/invernaderos/{gh_id}/modulos/crear", response_class=HTMLResponse)
def module_create_form(request: Request, gh_id: int, user=Depends(require_current_user_html)):
    """Module creation form."""
    gh_repo = get_greenhouse_repository(request)
    greenhouse = gh_repo.get_by_id(gh_id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    return templates.TemplateResponse(request, "agricultural/module_form.html", {
        "title": "Crear Módulo",
        "show_back": True,
        "back_url": f"/invernaderos/{gh_id}",
        "mode": "create",
        "greenhouse_id": gh_id,
        "name": "",
        "crop_type": "Tomate Cherry",
        "width_m": "",
        "length_m": "",
        "monitoring_frequency_days": "7",
        "errors": [],
    })


@router.post("/invernaderos/{gh_id}/modulos/crear", response_class=HTMLResponse)
def module_create(
    request: Request,
    gh_id: int,
    name: str = Form(...),
    crop_type: str = Form("Tomate Cherry"),
    width_m: str = Form(""),
    length_m: str = Form(""),
    monitoring_frequency_days: str = Form(""),
    user=Depends(require_current_user_html),
):
    """Process module creation."""
    errors: list[str] = []

    # Validate module name
    validated_name = None
    try:
        validated_name = validate_module_name(name)
    except ValidationError as e:
        errors.append(e.message)

    # Validate dimensions (optional — only validate if at least one is provided)
    parsed_width: float | None = None
    parsed_length: float | None = None
    if width_m.strip() or length_m.strip():
        try:
            parsed_width, parsed_length = validate_dimensions(
                width_m if width_m.strip() else "0",
                length_m if length_m.strip() else "0",
            )
        except ValidationError as e:
            errors.append(e.message)
    else:
        parsed_width = None
        parsed_length = None

    # Parse monitoring frequency
    parsed_frequency = _parse_monitoring_frequency(monitoring_frequency_days)
    if monitoring_frequency_days.strip() and parsed_frequency is None:
        errors.append("La frecuencia de monitoreo debe ser un número entero positivo.")

    if errors:
        return templates.TemplateResponse(request, "agricultural/module_form.html", {
            "title": "Crear Módulo",
            "show_back": True,
            "back_url": f"/invernaderos/{gh_id}",
            "mode": "create",
            "greenhouse_id": gh_id,
            "name": name,
            "crop_type": crop_type,
            "width_m": width_m,
            "length_m": length_m,
            "monitoring_frequency_days": monitoring_frequency_days,
            "errors": errors,
        })

    repo = get_module_repository(request)
    module = Module(
        greenhouse_id=gh_id,
        name=validated_name,
        crop_type=crop_type.strip() or "Tomate Cherry",
        width_m=parsed_width,
        length_m=parsed_length,
        monitoring_frequency_days=parsed_frequency,
    )
    try:
        created = repo.create(gh_id, module)
    except DuplicateModuleError:
        errors.append("Ya existe un módulo con ese nombre en este invernadero.")
        return templates.TemplateResponse(request, "agricultural/module_form.html", {
            "title": "Crear Módulo",
            "show_back": True,
            "back_url": f"/invernaderos/{gh_id}",
            "mode": "create",
            "greenhouse_id": gh_id,
            "name": name,
            "crop_type": crop_type,
            "width_m": width_m,
            "length_m": length_m,
            "monitoring_frequency_days": monitoring_frequency_days,
            "errors": errors,
        })

    return RedirectResponse(url=f"/modulos/{created.id}", status_code=303)


@router.get("/modulos/{id}", response_class=HTMLResponse)
async def module_detail(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 3: Module Detail Screen."""

    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    monitoring_repo = get_monitoring_repository(request)
    metrics_repo = get_monitoring_metrics_repository(request)

    monitorings = monitoring_repo.get_by_module(id)
    metrics_by_monitoring: dict[int, object] = {}
    for m in monitorings:
        metrics = metrics_repo.get_by_monitoring(m.id)
        if metrics:
            metrics_by_monitoring[m.id] = metrics

    history = build_monitoring_history(monitorings, metrics_by_monitoring)
    active_monitoring = build_active_monitoring_context(monitorings)

    # Combined history (monitorings + activities)
    from src.application.services.history_service import HistoryService

    activity_log_repo = get_activity_log_repository(request)
    activity_type_repo = get_activity_type_repository(request)
    activity_logs = activity_log_repo.list_by_module(id)
    activity_types = activity_type_repo.list_all()

    history_service = HistoryService()
    combined_history = history_service.build_combined_history(
        monitorings=monitorings,
        metrics_by_monitoring=metrics_by_monitoring,
        activity_logs=activity_logs,
        activity_types=activity_types,
    )

    # Format dimensions for info panel
    if module.width_m is not None and module.length_m is not None:
        dimensions_display = f"{module.width_m} × {module.length_m} m"
    else:
        dimensions_display = "No configuradas"

    # Compute monitoring due status
    from src.application.services.alert_service import AlertService
    from datetime import date
    alert_service = AlertService()
    monitoring_due_status = None
    frequency = alert_service.get_effective_frequency(module)
    if frequency is not None:
        next_due = alert_service.calculate_next_monitoring_due(module, monitorings)
        if next_due is None:
            # No valid monitorings → pending
            monitoring_due_status = "pending"
        else:
            today = date.today()
            days_overdue = (today - next_due).days
            if days_overdue > 0:
                monitoring_due_status = "overdue"
            elif days_overdue == 0:
                monitoring_due_status = "due_today"
            else:
                monitoring_due_status = "up_to_date"

    return templates.TemplateResponse(request, "agricultural/module_detail.html", {
        "title": module.name,
        "module": module,
        "dimensions_display": dimensions_display,
        "history": history,
        "combined_history": combined_history,
        "active_monitoring": active_monitoring,
        "monitoring_due_status": monitoring_due_status,
        "show_back": True,
        "back_url": f"/invernaderos/{module.greenhouse_id}",
    })


@router.get("/modulos/{id}/editar", response_class=HTMLResponse)
def module_edit_form(request: Request, id: int, user=Depends(require_current_user_html)):
    """Module edit form."""
    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    return templates.TemplateResponse(request, "agricultural/module_form.html", {
        "title": "Editar Módulo",
        "show_back": True,
        "back_url": f"/modulos/{id}",
        "mode": "edit",
        "module_id": id,
        "greenhouse_id": module.greenhouse_id,
        "name": module.name,
        "crop_type": module.crop_type,
        "width_m": module.width_m if module.width_m is not None else "",
        "length_m": module.length_m if module.length_m is not None else "",
        "monitoring_frequency_days": module.monitoring_frequency_days if module.monitoring_frequency_days is not None else "7",
        "errors": [],
    })


@router.post("/modulos/{id}/editar", response_class=HTMLResponse)
def module_edit(
    request: Request,
    id: int,
    name: str = Form(...),
    crop_type: str = Form("Tomate Cherry"),
    width_m: str = Form(""),
    length_m: str = Form(""),
    monitoring_frequency_days: str = Form(""),
    user=Depends(require_current_user_html),
):
    """Process module edit."""
    errors: list[str] = []

    # Validate module name
    validated_name = None
    try:
        validated_name = validate_module_name(name)
    except ValidationError as e:
        errors.append(e.message)

    # Validate dimensions (optional — only validate if at least one is provided)
    parsed_width: float | None = None
    parsed_length: float | None = None
    if width_m.strip() or length_m.strip():
        try:
            parsed_width, parsed_length = validate_dimensions(
                width_m if width_m.strip() else "0",
                length_m if length_m.strip() else "0",
            )
        except ValidationError as e:
            errors.append(e.message)
    else:
        parsed_width = None
        parsed_length = None

    # Parse monitoring frequency
    parsed_frequency = _parse_monitoring_frequency(monitoring_frequency_days)
    if monitoring_frequency_days.strip() and parsed_frequency is None:
        errors.append("La frecuencia de monitoreo debe ser un número entero positivo.")

    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    if errors:
        return templates.TemplateResponse(request, "agricultural/module_form.html", {
            "title": "Editar Módulo",
            "show_back": True,
            "back_url": f"/modulos/{id}",
            "mode": "edit",
            "module_id": id,
            "greenhouse_id": module.greenhouse_id,
            "name": name,
            "crop_type": crop_type,
            "width_m": width_m,
            "length_m": length_m,
            "monitoring_frequency_days": monitoring_frequency_days,
            "errors": errors,
        })

    try:
        repo.update(id, {
            "name": validated_name,
            "crop_type": crop_type.strip() or "Tomate Cherry",
            "width_m": parsed_width,
            "length_m": parsed_length,
            "monitoring_frequency_days": parsed_frequency,
        })
    except DuplicateModuleError:
        errors.append("Ya existe un módulo con ese nombre en este invernadero.")
        return templates.TemplateResponse(request, "agricultural/module_form.html", {
            "title": "Editar Módulo",
            "show_back": True,
            "back_url": f"/modulos/{id}",
            "mode": "edit",
            "module_id": id,
            "greenhouse_id": module.greenhouse_id,
            "name": name,
            "crop_type": crop_type,
            "width_m": width_m,
            "length_m": length_m,
            "monitoring_frequency_days": monitoring_frequency_days,
            "errors": errors,
        })

    return RedirectResponse(url=f"/modulos/{id}", status_code=303)


@router.post("/modulos/{id}/eliminar")
def module_delete(request: Request, id: int, user=Depends(require_current_user_html)):
    """Delete module (with confirmation handled client-side)."""
    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    greenhouse_id = module.greenhouse_id
    repo.delete(id)
    return RedirectResponse(url=f"/invernaderos/{greenhouse_id}", status_code=303)


# ---------------------------------------------------------------------------
# Monitoring endpoints
# ---------------------------------------------------------------------------


@router.get("/modulos/{id}/monitoreo/nuevo", response_class=HTMLResponse)
async def monitoring_setup(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 4: Monitoring Setup Screen."""

    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    # Check model availability (lightweight file existence check)
    model_service = ModelService(DETECTION_MODEL_PATH)
    model_status = model_service.check_availability().value

    return templates.TemplateResponse(request, "agricultural/monitoring_setup.html", {
        "title": f"Nuevo Monitoreo — {module.name}",
        "module": module,
        "width_m": module.width_m if module.width_m is not None else "",
        "length_m": module.length_m if module.length_m is not None else "",
        "notes": "",
        "errors": [],
        "model_status": model_status,
        "show_back": True,
        "back_url": f"/modulos/{id}",
    })


@router.post("/modulos/{id}/monitoreo/iniciar")
def monitoring_start(
    request: Request,
    id: int,
    width_m: str = Form(""),
    length_m: str = Form(""),
    notes: str = Form(""),
    user=Depends(require_current_user_html),
):
    """Start a monitoring session for the given module.

    Delegates to MonitoringService.start_session() which:
    - Validates module exists
    - Enforces one-active-session-per-module invariant
    - Creates the Monitoring entity
    - Spawns the background capture worker
    """
    from src.application.services.monitoring_service import (
        ActiveSessionError,
        CameraStillBusyError,
    )
    from src.application.services.frame_source_factory import create_frame_source
    from app.dependencies import get_log_service

    repo = get_module_repository(request)
    module = repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    # Check model availability for re-rendering context
    model_service = ModelService(DETECTION_MODEL_PATH)
    model_status = model_service.check_availability().value

    # Validate dimensions and notes using application validators
    errors: list[str] = []
    width: "float | None" = None
    length: "float | None" = None
    try:
        width, length = validate_optional_dimensions(width_m, length_m)
    except ValidationError as e:
        errors.append(e.message)

    validated_notes = validate_notes(notes)

    if errors:
        return templates.TemplateResponse(request, "agricultural/monitoring_setup.html", {
            "title": f"Nuevo Monitoreo — {module.name}",
            "module": module,
            "width_m": width_m,
            "length_m": length_m,
            "notes": notes,
            "errors": errors,
            "model_status": model_status,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })

    # Auto-save dimensions to module record only when provided (R4.7)
    if width is not None and length is not None:
        repo.update(id, {"width_m": width, "length_m": length})

    # Construct dependencies for MonitoringService.start_session()
    # 1. Frame source (camera backend — picamera2 on RPi, OpenCV on PC)
    #    Pass camera resolution from ACTIVE_PROFILE for monitoring capture.
    from src.infrastructure.config.settings import ACTIVE_PROFILE
    frame_source = create_frame_source(
        width=ACTIVE_PROFILE.camera_width,
        height=ACTIVE_PROFILE.camera_height,
        fps=ACTIVE_PROFILE.camera_fps,
    )
    if frame_source is None:
        errors = ["La cámara no está disponible. Verifica la conexión y vuelve a intentar."]
        return templates.TemplateResponse(request, "agricultural/monitoring_setup.html", {
            "title": f"Nuevo Monitoreo — {module.name}",
            "module": module,
            "width_m": width_m,
            "length_m": length_m,
            "notes": notes,
            "errors": errors,
            "model_status": model_status,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })

    # 2. DB session and log service (no inference models loaded at start)
    from app.dependencies import _get_request_session
    db_session = _get_request_session(request)
    log_service = get_log_service(request)

    # Delegate to MonitoringService
    monitoring_service = get_monitoring_service(request)
    try:
        monitoring = monitoring_service.start_session(
            module_id=id,
            width_m=width,
            length_m=length,
            notes=validated_notes,
            frame_source=frame_source,
            db_session=db_session,
            log_service=log_service,
        )
    except ActiveSessionError as e:
        # Redirect to the existing active monitoring's execution screen
        frame_source.release()
        return RedirectResponse(
            url=f"/monitoreos/{e.existing_monitoring_id}/ejecucion",
            status_code=303,
        )
    except CameraStillBusyError:
        # Previous worker still releasing camera — tell farmer to wait
        frame_source.release()
        errors = ["El monitoreo anterior todavía está liberando la cámara. Espera unos segundos e intenta de nuevo."]
        return templates.TemplateResponse(request, "agricultural/monitoring_setup.html", {
            "title": f"Nuevo Monitoreo — {module.name}",
            "module": module,
            "width_m": width_m,
            "length_m": length_m,
            "notes": notes,
            "errors": errors,
            "model_status": model_status,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })
    except Exception as e:
        frame_source.release()
        # Handle various hardware/system errors
        error_str = str(e)
        if "cámara" in error_str.lower() or "camera" in error_str.lower():
            friendly_msg = "La cámara no está disponible. Verifica la conexión y vuelve a intentar."
        elif "disco" in error_str.lower() or "disk" in error_str.lower() or "space" in error_str.lower():
            friendly_msg = "Espacio en disco bajo. Libera espacio antes de continuar."
        elif "modelo" in error_str.lower() or "model" in error_str.lower() or "inference" in error_str.lower():
            friendly_msg = "No se pudieron cargar los modelos de inferencia. Verifica que los archivos estén en su lugar."
        else:
            friendly_msg = f"Error al iniciar el monitoreo: {error_str}"

        errors = [friendly_msg]
        return templates.TemplateResponse(request, "agricultural/monitoring_setup.html", {
            "title": f"Nuevo Monitoreo — {module.name}",
            "module": module,
            "width_m": width_m,
            "length_m": length_m,
            "notes": notes,
            "errors": errors,
            "model_status": model_status,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })

    return RedirectResponse(url=f"/monitoreos/{monitoring.id}/ejecucion", status_code=303)


@router.get("/monitoreos/{id}/ejecucion", response_class=HTMLResponse)
async def monitoring_execution(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 5: Monitoring Execution Screen."""

    monitoring_repo = get_monitoring_repository(request)
    monitoring = monitoring_repo.get_by_id(id)
    if monitoring is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(monitoring.module_id)

    return templates.TemplateResponse(request, "agricultural/monitoring_execution.html", {
        "title": f"Monitoreando — {module.name}",
        "monitoring": monitoring,
        "module": module,
        "show_back": True,
        "back_url": f"/modulos/{module.id}",
    })


@router.get("/monitoreos/{id}/reporte", response_class=HTMLResponse)
async def monitoring_report(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 6: Monitoring Report Screen."""

    monitoring_repo = get_monitoring_repository(request)
    monitoring = monitoring_repo.get_by_id(id)
    if monitoring is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(monitoring.module_id)

    metrics_repo = get_monitoring_metrics_repository(request)
    metrics = metrics_repo.get_by_monitoring(id)

    snapshot_repo = get_snapshot_repository(request)
    snapshots = snapshot_repo.get_by_monitoring(id)

    # Build context
    report_metrics = build_report_metrics(metrics) if metrics else None
    gallery = build_snapshot_gallery(snapshots, id)

    # Format header date/time
    date_str = _format_date_spanish(monitoring.started_at)
    time_str = _format_time(monitoring.started_at)

    return templates.TemplateResponse(request, "agricultural/monitoring_report.html", {
        "title": f"{module.name} — {date_str} {time_str}",
        "monitoring": monitoring,
        "module": module,
        "metrics": report_metrics,
        "gallery": gallery,
        "date_str": date_str,
        "time_str": time_str,
        "show_back": True,
        "back_url": f"/modulos/{module.id}",
    })


@router.post("/monitoreos/{id}/abortar")
def monitoring_abort(request: Request, id: int, user=Depends(require_current_user_html)):
    """Abort an active monitoring session (redirects to module detail).

    Delegates to MonitoringService.abort_session() which:
    - Signals the background worker to stop
    - Computes partial metrics
    - Transitions status to 'aborted'
    """
    from src.application.services.monitoring_service import (
        MonitoringNotFoundError,
    )
    from src.domain.exceptions import InvalidTransitionError

    _logger.info(f"UI abort request received for monitoring {id}")

    monitoring_service = get_monitoring_service(request)
    try:
        monitoring = monitoring_service.abort_session(id)
        _logger.info(
            f"UI abort completed for monitoring {id}, "
            f"redirecting to module {monitoring.module_id}"
        )
        return RedirectResponse(url=f"/modulos/{monitoring.module_id}", status_code=303)
    except MonitoringNotFoundError:
        _logger.warning(f"UI abort: monitoring {id} not found")
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)
    except InvalidTransitionError:
        # Session is already in a terminal state — redirect gracefully
        _logger.info(f"UI abort: monitoring {id} already in terminal state")
        monitoring_repo = get_monitoring_repository(request)
        monitoring = monitoring_repo.get_by_id(id)
        if monitoring is None:
            return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)
        return RedirectResponse(url=f"/modulos/{monitoring.module_id}", status_code=303)


@router.post("/monitoreos/{id}/finalizar-captura")
def monitoring_finalize_capture(request: Request, id: int, user=Depends(require_current_user_html)):
    """Finalize the capture phase and start deferred analysis.

    Delegates exclusively to MonitoringService.finalize_capture() which:
    - Signals the worker to stop capturing (NOT abort)
    - Waits for camera release
    - Starts analysis thread if snapshots exist
    - Transitions to 'completed' directly if zero snapshots

    Always redirects to the execution screen regardless of outcome.
    """
    from src.application.services.monitoring_service import (
        MonitoringNotFoundError,
        FinalizationInProgressError,
    )
    from src.domain.exceptions import InvalidTransitionError

    _logger.info(f"UI finalize-capture request received for monitoring {id}")

    monitoring_service = get_monitoring_service(request)
    try:
        monitoring = monitoring_service.finalize_capture(id)
        _logger.info(
            f"UI finalize-capture accepted for monitoring {id}, "
            f"status={monitoring.status}"
        )
        return RedirectResponse(
            url=f"/monitoreos/{id}/ejecucion", status_code=303
        )
    except MonitoringNotFoundError:
        _logger.warning(f"UI finalize-capture: monitoring {id} not found")
        return RedirectResponse(
            url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303
        )
    except FinalizationInProgressError:
        _logger.info(
            f"UI finalize-capture: monitoring {id} already being finalized"
        )
        return RedirectResponse(
            url=f"/monitoreos/{id}/ejecucion", status_code=303
        )
    except InvalidTransitionError:
        _logger.info(
            f"UI finalize-capture: monitoring {id} invalid transition"
        )
        monitoring_repo = get_monitoring_repository(request)
        monitoring = monitoring_repo.get_by_id(id)
        if monitoring is None:
            return RedirectResponse(
                url="/invernaderos?error=Monitoreo+no+encontrado",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/monitoreos/{id}/ejecucion", status_code=303
        )



# ---------------------------------------------------------------------------
# Activity Log endpoints (Bitácora agrícola)
# ---------------------------------------------------------------------------


@router.get("/modulos/{id}/actividades", response_class=HTMLResponse)
async def activity_list(request: Request, id: int, user=Depends(require_current_user_html)):
    """Activity log list for a module."""
    from src.application.services.activity_service import ActivityService

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    activity_type_repo = get_activity_type_repository(request)
    activity_log_repo = get_activity_log_repository(request)

    service = ActivityService()
    activities = service.list_activities_by_module(id, activity_log_repo, activity_type_repo)

    return templates.TemplateResponse(request, "agricultural/activity_list.html", {
        "title": f"Bitácora — {module.name}",
        "module": module,
        "activities": activities,
        "show_back": True,
        "back_url": f"/modulos/{id}",
    })


@router.get("/modulos/{id}/actividades/registrar", response_class=HTMLResponse)
async def activity_create_form(request: Request, id: int, user=Depends(require_current_user_html)):
    """Show form to register a new agricultural activity."""
    from src.application.services.activity_service import ActivityService

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    activity_type_repo = get_activity_type_repository(request)
    service = ActivityService()
    activity_types = service.list_activity_types(activity_type_repo)

    return templates.TemplateResponse(request, "agricultural/activity_form.html", {
        "title": f"Registrar Actividad — {module.name}",
        "module": module,
        "activity_types": activity_types,
        "errors": [],
        "form_data": {},
        "show_back": True,
        "back_url": f"/modulos/{id}",
    })


@router.post("/modulos/{id}/actividades/registrar", response_class=HTMLResponse)
def activity_create(
    request: Request,
    id: int,
    activity_type_id: str = Form(...),
    occurred_at_date: str = Form(""),
    occurred_at_time: str = Form(""),
    product_name: str = Form(""),
    quantity: str = Form(""),
    unit: str = Form(""),
    notes: str = Form(""),
    user=Depends(require_current_user_html),
):
    """Process activity registration form."""
    from src.application.services.activity_service import (
        ActivityService,
        ActivityValidationError,
        ActivityTypeNotFoundError,
    )

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(id)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    activity_type_repo = get_activity_type_repository(request)
    activity_log_repo = get_activity_log_repository(request)
    service = ActivityService()

    # Preserve form data for re-rendering on error
    form_data = {
        "activity_type_id": activity_type_id,
        "occurred_at_date": occurred_at_date,
        "occurred_at_time": occurred_at_time,
        "product_name": product_name,
        "quantity": quantity,
        "unit": unit,
        "notes": notes,
    }

    errors: list[str] = []

    # Parse activity_type_id
    parsed_type_id = 0
    try:
        parsed_type_id = int(activity_type_id)
    except (ValueError, TypeError):
        errors.append("Selecciona un tipo de actividad válido.")

    # Parse occurred_at from date + time
    occurred_at = None
    if occurred_at_date.strip():
        try:
            date_str = occurred_at_date.strip()
            time_str = occurred_at_time.strip() if occurred_at_time.strip() else "00:00"
            local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            occurred_at = bogota_to_utc(local_dt)
        except ValueError:
            errors.append("Formato de fecha/hora inválido.")

    # Parse quantity
    parsed_quantity = None
    if quantity.strip():
        try:
            parsed_quantity = float(quantity.strip())
        except ValueError:
            errors.append("La cantidad debe ser un número válido.")

    if errors:
        activity_types = service.list_activity_types(activity_type_repo)
        return templates.TemplateResponse(request, "agricultural/activity_form.html", {
            "title": f"Registrar Actividad — {module.name}",
            "module": module,
            "activity_types": activity_types,
            "errors": errors,
            "form_data": form_data,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })

    # Call service
    try:
        service.create_activity(
            module_id=id,
            activity_type_id=parsed_type_id,
            user_id=user.id,
            product_name=product_name.strip() or None,
            quantity=parsed_quantity,
            unit=unit.strip() or None,
            notes=notes.strip() or None,
            occurred_at=occurred_at,
            activity_type_repo=activity_type_repo,
            activity_log_repo=activity_log_repo,
        )
    except ActivityValidationError as e:
        errors.append(e.message)
        activity_types = service.list_activity_types(activity_type_repo)
        return templates.TemplateResponse(request, "agricultural/activity_form.html", {
            "title": f"Registrar Actividad — {module.name}",
            "module": module,
            "activity_types": activity_types,
            "errors": errors,
            "form_data": form_data,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })
    except ActivityTypeNotFoundError as e:
        errors.append(e.message)
        activity_types = service.list_activity_types(activity_type_repo)
        return templates.TemplateResponse(request, "agricultural/activity_form.html", {
            "title": f"Registrar Actividad — {module.name}",
            "module": module,
            "activity_types": activity_types,
            "errors": errors,
            "form_data": form_data,
            "show_back": True,
            "back_url": f"/modulos/{id}",
        })

    return RedirectResponse(url=f"/modulos/{id}/actividades", status_code=303)


# ---------------------------------------------------------------------------
# Export endpoints (Exportación ZIP)
# ---------------------------------------------------------------------------


@router.get("/exportar", response_class=HTMLResponse)
async def export_list(request: Request, user=Depends(require_current_user_html)):
    """List export packages and show generate button."""
    export_repo = get_export_package_repository(request)
    packages = export_repo.list_by_user(user.id)

    return templates.TemplateResponse(request, "agricultural/export_list.html", {
        "title": "Exportación de datos",
        "packages": packages,
        "show_back": True,
        "back_url": "/dashboard",
    })


@router.post("/exportar")
def export_create(request: Request, user=Depends(require_current_user_html)):
    """Generate a new ZIP export package."""
    from src.application.services.export_service import ExportService
    from src.domain.entities.export_package import ExportPackage

    export_repo = get_export_package_repository(request)
    gh_repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)
    metrics_repo = get_monitoring_metrics_repository(request)
    snapshot_repo = get_snapshot_repository(request)
    activity_type_repo = get_activity_type_repository(request)
    activity_log_repo = get_activity_log_repository(request)

    # Create export package record with status "generating"
    package = ExportPackage(
        created_by_user_id=user.id,
        scope="full",
        status="generating",
    )
    package = export_repo.create(package)

    try:
        # Fetch all data
        greenhouses = gh_repo.get_all()
        modules = []
        for gh in greenhouses:
            modules.extend(module_repo.get_by_greenhouse(gh.id))

        monitorings = []
        for mod in modules:
            monitorings.extend(monitoring_repo.get_by_module(mod.id))

        metrics_by_monitoring = {}
        snapshots_by_monitoring = {}
        for mon in monitorings:
            met = metrics_repo.get_by_monitoring(mon.id)
            if met:
                metrics_by_monitoring[mon.id] = met
            snapshots_by_monitoring[mon.id] = snapshot_repo.get_by_monitoring(mon.id)

        activity_types = activity_type_repo.list_all()
        activity_logs = activity_log_repo.list_recent(limit=10000)

        # Generate ZIP
        service = ExportService()
        result = service.generate_export(
            greenhouses=greenhouses,
            modules=modules,
            monitorings=monitorings,
            metrics_by_monitoring=metrics_by_monitoring,
            snapshots_by_monitoring=snapshots_by_monitoring,
            activity_types=activity_types,
            activity_logs=activity_logs,
            export_package=package,
        )

        # Update package status
        now = datetime.now()
        if result.status == "completed":
            export_repo.update(package.id, {
                "status": "completed",
                "file_path": result.file_path,
                "file_size_bytes": result.file_size_bytes,
                "records_count": result.records_count,
                "images_count": result.images_count,
                "completed_at": now,
                "manifest_json": json.dumps(result.manifest, ensure_ascii=False),
            })
        else:
            export_repo.update(package.id, {
                "status": "error",
                "error_message": result.error_message or "Error desconocido",
                "completed_at": now,
            })

    except Exception as e:
        export_repo.update(package.id, {
            "status": "error",
            "error_message": str(e),
            "completed_at": datetime.now(),
        })

    return RedirectResponse(url=f"/exportar/{package.id}", status_code=303)


@router.get("/exportar/{id}", response_class=HTMLResponse)
async def export_detail(request: Request, id: int, user=Depends(require_current_user_html)):
    """Show export package details."""
    export_repo = get_export_package_repository(request)
    package = export_repo.get_by_id(id)
    if package is None:
        return RedirectResponse(url="/exportar?error=Exportación+no+encontrada", status_code=303)

    # Parse manifest for display
    manifest = None
    if package.manifest_json:
        try:
            manifest = json.loads(package.manifest_json)
        except (json.JSONDecodeError, TypeError):
            manifest = None

    return templates.TemplateResponse(request, "agricultural/export_detail.html", {
        "title": f"Exportación #{package.id}",
        "package": package,
        "manifest": manifest,
        "show_back": True,
        "back_url": "/exportar",
    })


def _is_safe_export_path(file_path: str) -> bool:
    """Verify file_path is inside outputs/exports and is a .zip file."""
    if not file_path:
        return False
    try:
        resolved = Path(file_path).resolve()
        exports_dir = Path("outputs/exports").resolve()
        try:
            resolved.relative_to(exports_dir)
        except ValueError:
            return False
        if resolved.suffix.lower() != ".zip":
            return False
        if not resolved.is_file():
            return False
        return True
    except (ValueError, OSError):
        return False


@router.get("/exportar/{id}/descargar")
async def export_download(request: Request, id: int, user=Depends(require_current_user_html)):
    """Download the ZIP file for a completed export."""
    from fastapi.responses import FileResponse

    export_repo = get_export_package_repository(request)
    package = export_repo.get_by_id(id)
    if package is None:
        return RedirectResponse(url="/exportar?error=Exportación+no+encontrada", status_code=303)

    if package.status != "completed":
        return RedirectResponse(url=f"/exportar/{id}?error=No+disponible", status_code=303)

    if not package.file_path or not _is_safe_export_path(package.file_path):
        return RedirectResponse(url=f"/exportar/{id}?error=Archivo+no+disponible", status_code=303)

    file_path = Path(package.file_path)
    return FileResponse(
        path=str(file_path),
        media_type="application/zip",
        filename=file_path.name,
    )


# ---------------------------------------------------------------------------
# Sync routes
# ---------------------------------------------------------------------------


@router.get("/sincronizacion", response_class=HTMLResponse)
async def sync_status_page(request: Request, user=Depends(require_current_user_html)):
    """Show sync status and manual sync trigger."""
    monitoring_repo = get_monitoring_repository(request)
    activity_log_repo = get_activity_log_repository(request)
    export_repo = get_export_package_repository(request)

    from src.application.services.sync_service import SyncService

    monitorings = monitoring_repo.list_all()
    activity_logs = activity_log_repo.list_all()

    sync_service = SyncService()
    sync_status = sync_service.compute_sync_status(monitorings, activity_logs)

    # Get last export for display
    user_exports = export_repo.list_by_user(user.id)
    last_export = user_exports[0] if user_exports else None

    error = request.query_params.get("error")
    success_message = request.query_params.get("success")

    # Check if Supabase remote sync is configured
    supabase_configured = _is_supabase_configured(request)

    return templates.TemplateResponse(request, "agricultural/sync_status.html", {
        "title": "Sincronización",
        "sync_status": sync_status,
        "last_export": last_export,
        "error": error,
        "success_message": success_message,
        "supabase_configured": supabase_configured,
        "show_back": True,
        "back_url": "/dashboard",
    })


@router.post("/sincronizacion/local")
async def sync_local_trigger(request: Request, user=Depends(require_current_user_html)):
    """Trigger a manual local sync (ZIP export + mark records as exported)."""
    monitoring_repo = get_monitoring_repository(request)
    activity_log_repo = get_activity_log_repository(request)
    export_repo = get_export_package_repository(request)

    from src.application.services.sync_service import SyncService
    from src.application.services.export_service import ExportService
    from src.domain.entities.export_package import ExportPackage

    monitorings = monitoring_repo.list_all()
    activity_logs = activity_log_repo.list_all()

    # Check there are pending records
    sync_service = SyncService()
    status_info = sync_service.compute_sync_status(monitorings, activity_logs)
    if status_info["total_pending"] == 0:
        return RedirectResponse(
            url="/sincronizacion?error=No+hay+registros+pendientes",
            status_code=303,
        )

    # Create export package record
    package = ExportPackage(
        created_by_user_id=user.id,
        scope="full",
        status="pending",
    )
    package = export_repo.create(package)

    # Generate the export (reuses existing ExportService)
    from app.dependencies import (
        get_greenhouse_repository,
        get_module_repository,
        get_snapshot_repository,
        get_monitoring_metrics_repository,
        get_activity_type_repository,
    )

    greenhouse_repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    snapshot_repo = get_snapshot_repository(request)
    metrics_repo = get_monitoring_metrics_repository(request)
    activity_type_repo = get_activity_type_repository(request)

    greenhouses = greenhouse_repo.get_all()
    modules = []
    for gh in greenhouses:
        modules.extend(module_repo.get_by_greenhouse(gh.id))

    all_activity_types = activity_type_repo.list_active()

    metrics_by_monitoring = {}
    snapshots_by_monitoring = {}
    for m in monitorings:
        metrics_by_monitoring[m.id] = metrics_repo.get_by_monitoring(m.id)
        snapshots_by_monitoring[m.id] = snapshot_repo.get_by_monitoring(m.id)

    export_service = ExportService()
    export_result = export_service.generate_export(
        greenhouses=greenhouses,
        modules=modules,
        monitorings=monitorings,
        metrics_by_monitoring=metrics_by_monitoring,
        snapshots_by_monitoring=snapshots_by_monitoring,
        activity_types=all_activity_types,
        activity_logs=activity_logs,
        export_package=package,
    )

    # Update export package status
    from datetime import datetime, timezone

    if export_result.status == "completed":
        export_repo.update(package.id, {
            "status": "completed",
            "file_path": export_result.file_path,
            "file_size_bytes": export_result.file_size_bytes,
            "records_count": export_result.records_count,
            "images_count": export_result.images_count,
            "completed_at": datetime.now(timezone.utc),
            "manifest_json": json.dumps(export_result.manifest, ensure_ascii=False),
        })
    else:
        export_repo.update(package.id, {
            "status": "error",
            "error_message": export_result.error_message or "Error desconocido",
        })
        return RedirectResponse(
            url="/sincronizacion?error=La+exportación+local+falló",
            status_code=303,
        )

    # Mark pending records as exported
    result = sync_service.manual_local_sync(
        export_result=export_result,
        monitoring_repo=monitoring_repo,
        activity_log_repo=activity_log_repo,
        monitorings=monitorings,
        activity_logs=activity_logs,
    )

    msg = f"Exportación+completada.+{result['updated_monitorings']}+monitoreos+y+{result['updated_activities']}+actividades+marcados+como+exportados"
    return RedirectResponse(url=f"/sincronizacion?success={msg}", status_code=303)
