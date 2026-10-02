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
from app.operational_alerts import load_operational_alerts
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
    get_inspection_result_repository,
    get_monitoring_service,
    get_activity_type_repository,
    get_activity_log_repository,
    get_export_package_repository,
    get_deletion_service,
    require_current_user_html,
)
from src.application.services.model_service import ModelService
from src.infrastructure.config.settings import DETECTION_MODEL_PATH
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.maturity_assessment import VALID_USDA_STAGES
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


def _deletion_error_message(exc: Exception) -> str:
    """Map a DeletionService exception to a user-facing Spanish message.

    The DeletionService domain errors already carry actionable Spanish
    messages; this helper simply surfaces them (with a safe fallback) so the
    route stays free of business logic — it only translates the rejection into
    a flash-style ``?error=`` redirect.
    """
    message = str(exc).strip()
    if message:
        return message
    return "No se pudo completar la eliminación."


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
# Local multiuser isolation helpers (Spec 022)
#
# Ownership is anchored on the root Greenhouse (owner_user_id). Descendants
# (Module, Monitoring, ...) inherit it via FK chain; we do NOT duplicate
# owner_user_id on children. These helpers resolve a module/monitoring up to
# its greenhouse and check it belongs to the current user, so authenticated
# routes never operate on another user's hierarchy.
# ---------------------------------------------------------------------------


def _user_owns_greenhouse(request: Request, greenhouse_id: int, user) -> bool:
    """True if the greenhouse exists AND belongs to the current user."""
    gh_repo = get_greenhouse_repository(request)
    return gh_repo.get_by_id_for_owner(greenhouse_id, user.id) is not None


def _module_owned_by_user(request: Request, module_id: int, user):
    """Return the module only if its greenhouse belongs to the current user.

    Returns None when the module does not exist OR its root greenhouse is
    owned by a different user (treated as not-found by callers).
    """
    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(module_id)
    if module is None:
        return None
    if not _user_owns_greenhouse(request, module.greenhouse_id, user):
        return None
    return module


def _monitoring_owned_by_user(request: Request, monitoring_id: int, user):
    """Return the monitoring only if its module's greenhouse belongs to the user.

    Returns None when the monitoring does not exist OR belongs to another
    user's hierarchy (module -> greenhouse -> owner).
    """
    monitoring_repo = get_monitoring_repository(request)
    monitoring = monitoring_repo.get_by_id(monitoring_id)
    if monitoring is None:
        return None
    if _module_owned_by_user(request, monitoring.module_id, user) is None:
        return None
    return monitoring


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

    # Local multiuser isolation (Spec 022): dashboard covers only the current
    # user's greenhouses and their descendants.
    greenhouses = gh_repo.get_all_by_owner(user.id)
    modules = []
    monitorings_by_module: dict[int, list] = {}
    for gh in greenhouses:
        gh_modules = module_repo.get_by_greenhouse(gh.id)
        modules.extend(gh_modules)
        for m in gh_modules:
            monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    # Recent activities (enriched with type names) — scoped to the user's
    # modules, then sorted by occurrence and limited, to avoid cross-user leak.
    scoped_logs = []
    for m in modules:
        scoped_logs.extend(activity_log_repo.list_by_module(m.id))
    scoped_logs.sort(key=lambda l: l.occurred_at, reverse=True)
    recent_logs = scoped_logs[:5]
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

    # Export packages — scoped to the current user; keep only pending ones
    # (no global list_pending() that could surface other users' packages).
    export_packages = [
        p for p in export_repo.list_by_user(user.id)
        if getattr(p, "status", None) == "pending"
    ]

    # Reuse the same full alert result for the dashboard and global header.
    full_alerts = load_operational_alerts(
        request,
        user,
        greenhouses=greenhouses,
        modules=modules,
        monitorings_by_module=monitorings_by_module,
        export_packages=export_packages,
    )

    # Build operational context (unchanged).
    dashboard_service = DashboardService()
    context = dashboard_service.build_context(
        greenhouses=greenhouses,
        modules=modules,
        monitorings_by_module=monitorings_by_module,
        recent_activities=recent_activities,
        export_packages=export_packages,
        alerts=full_alerts,
    )

    # --- Analytical context (Spec 024) ---
    # Built via bulk repository reads with strict user isolation: only the
    # already owner-scoped `greenhouses` seed the scope; module/monitoring ids
    # are derived by descending from the selected owned greenhouse, never from
    # the querystring directly. The template is NOT modified in this iteration;
    # the analytics context is provided for the upcoming UI work.
    from src.application.services.analytics_service import AnalyticsService
    from src.application.services.dashboard_scope_builder import build_scope_data

    metrics_repo = get_monitoring_metrics_repository(request)
    inspection_repo = get_inspection_result_repository(request)

    greenhouse_id_param = request.query_params.get("greenhouse_id")
    module_id_param = request.query_params.get("module_id")

    scope_data = build_scope_data(
        greenhouses=greenhouses,
        module_repo=module_repo,
        monitoring_repo=monitoring_repo,
        metrics_repo=metrics_repo,
        inspection_repo=inspection_repo,
        greenhouse_id_param=greenhouse_id_param,
        module_id_param=module_id_param,
    )
    analytics = (
        AnalyticsService().build_analytics(scope_data)
        if scope_data is not None
        else None
    )

    # Selector data for the (future) UI: the user's greenhouses and, for the
    # selected greenhouse, its modules. Always owner-scoped.
    selected_greenhouse_id = scope_data.greenhouse_id if scope_data else None
    selected_module_id = scope_data.selected_module_id if scope_data else None
    scope_modules = (
        module_repo.get_by_greenhouse(selected_greenhouse_id)
        if selected_greenhouse_id is not None
        else []
    )

    return templates.TemplateResponse(request, "agricultural/dashboard.html", {
        "title": "Dashboard",
        "show_back": False,
        "supabase_configured": _is_supabase_configured(request),
        "analytics": analytics,
        "analytics_greenhouses": greenhouses,
        "analytics_scope_modules": scope_modules,
        "analytics_selected_greenhouse_id": selected_greenhouse_id,
        "analytics_selected_module_id": selected_module_id,
        "header_alert_count": len(full_alerts),
        "header_alerts": full_alerts[:5],
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

    # Local multiuser isolation (Spec 022): list only the current user's own
    # greenhouses; ownership of descendants is inherited via the greenhouse.
    greenhouses = repo.get_all_by_owner(user.id)

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

    # Compute operational alerts. Include greenhouse names so alerts remain
    # unambiguous when different greenhouses contain modules with the same name.
    from src.application.services.alert_service import AlertService
    greenhouse_names_by_id = {
        greenhouse.id: greenhouse.name
        for greenhouse in greenhouses
    }
    alert_service = AlertService()
    alerts = alert_service.compute_alerts(
        modules=all_modules,
        monitorings_by_module=monitorings_by_module,
        greenhouse_names_by_id=greenhouse_names_by_id,
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
        # Requirement 1.2: persist the authenticated owner on creation. The
        # auth dependency (require_current_user_html) already blocks
        # unauthenticated access, so a normal creation always has an owner.
        greenhouse = repo.create(
            Greenhouse(
                name=validated_name,
                owner_user_id=user.id,
                location=location.strip() or None,
            )
        )
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
    # Isolation: another user's greenhouse resolves to None (not found).
    greenhouse = repo.get_by_id_for_owner(id, user.id)
    if greenhouse is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)

    modules = module_repo.get_by_greenhouse(id)
    monitorings_by_module: dict[int, list] = {}
    for m in modules:
        monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    cards = build_module_cards(modules, monitorings_by_module)

    # Support error query param for flash-style messages (deletion rejection).
    error = request.query_params.get("error")

    return templates.TemplateResponse(request, "agricultural/greenhouse_detail.html", {
        "title": greenhouse.name,
        "greenhouse": greenhouse,
        "modules": cards,
        "error": error,
        "show_back": True,
        "back_url": "/invernaderos",
    })


@router.get("/invernaderos/{id}/editar", response_class=HTMLResponse)
def greenhouse_edit_form(request: Request, id: int, user=Depends(require_current_user_html)):
    """Greenhouse edit form."""
    repo = get_greenhouse_repository(request)
    greenhouse = repo.get_by_id_for_owner(id, user.id)
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
    greenhouse = repo.get_by_id_for_owner(id, user.id)
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
    """Delete greenhouse via DeletionService (confirmation handled client-side).

    Delegates the durable deletion to DeletionService and returns immediately
    after the local delete (Req 2.4). The backend re-validates every descendant
    monitoring's state and worker even if the UI hid the button (Req 2.7). On
    rejection/failure no records are deleted; the operator is redirected back
    with a Spanish ``?error=`` message. On success the greenhouse and its
    children stop being shown (redirect to the greenhouse list).
    """
    from src.application.services.deletion_service import DeletionError

    # Isolation: refuse to delete a greenhouse owned by another user.
    gh_repo = get_greenhouse_repository(request)
    if gh_repo.get_by_id_for_owner(id, user.id) is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

    service = get_deletion_service(request)
    try:
        service.delete_greenhouse(id)
    except DeletionError as exc:
        from urllib.parse import quote
        message = quote(_deletion_error_message(exc))
        return RedirectResponse(url=f"/invernaderos/{id}?error={message}", status_code=303)
    return RedirectResponse(url="/invernaderos", status_code=303)


# ---------------------------------------------------------------------------
# Module endpoints
# ---------------------------------------------------------------------------


@router.get("/invernaderos/{gh_id}/modulos/crear", response_class=HTMLResponse)
def module_create_form(request: Request, gh_id: int, user=Depends(require_current_user_html)):
    """Module creation form."""
    gh_repo = get_greenhouse_repository(request)
    # Isolation: cannot create a module under another user's greenhouse.
    greenhouse = gh_repo.get_by_id_for_owner(gh_id, user.id)
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
    # Isolation: cannot create a module under another user's greenhouse.
    gh_repo = get_greenhouse_repository(request)
    if gh_repo.get_by_id_for_owner(gh_id, user.id) is None:
        return RedirectResponse(url="/invernaderos?error=Invernadero+no+encontrado", status_code=303)

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

    module = _module_owned_by_user(request, id, user)
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

    # Support error query param for flash-style messages (deletion rejection).
    error = request.query_params.get("error")

    return templates.TemplateResponse(request, "agricultural/module_detail.html", {
        "title": module.name,
        "module": module,
        "dimensions_display": dimensions_display,
        "history": history,
        "combined_history": combined_history,
        "active_monitoring": active_monitoring,
        "monitoring_due_status": monitoring_due_status,
        "error": error,
        "show_back": True,
        "back_url": f"/invernaderos/{module.greenhouse_id}",
    })


@router.get("/modulos/{id}/editar", response_class=HTMLResponse)
def module_edit_form(request: Request, id: int, user=Depends(require_current_user_html)):
    """Module edit form."""
    module = _module_owned_by_user(request, id, user)
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
    module = _module_owned_by_user(request, id, user)
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
    """Delete module via DeletionService (confirmation handled client-side).

    Captures the parent greenhouse id for the success redirect, then delegates
    the durable deletion to DeletionService and returns immediately after the
    local delete (Req 2.4). The backend re-validates every descendant
    monitoring's state and worker even if the UI hid the button (Req 2.7). On
    rejection/failure no records are deleted and the operator is redirected back
    to the module detail with a Spanish ``?error=`` message; on success the
    module and its children stop being shown (redirect to the greenhouse).
    """
    from src.application.services.deletion_service import DeletionError

    module = _module_owned_by_user(request, id, user)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    greenhouse_id = module.greenhouse_id
    service = get_deletion_service(request)
    try:
        service.delete_module(id)
    except DeletionError as exc:
        from urllib.parse import quote
        message = quote(_deletion_error_message(exc))
        return RedirectResponse(url=f"/modulos/{id}?error={message}", status_code=303)
    return RedirectResponse(url=f"/invernaderos/{greenhouse_id}", status_code=303)


# ---------------------------------------------------------------------------
# Monitoring endpoints
# ---------------------------------------------------------------------------


@router.get("/modulos/{id}/monitoreo/nuevo", response_class=HTMLResponse)
async def monitoring_setup(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 4: Monitoring Setup Screen."""

    module = _module_owned_by_user(request, id, user)
    if module is None:
        return RedirectResponse(url="/invernaderos?error=Módulo+no+encontrado", status_code=303)

    # Spec 023: returning to the setup screen closes the preview lifecycle
    # SUSPENDED -> IDLE. enable_preview() is idempotent, does NOT open the camera
    # and only re-enables future subscriptions when the device is verifiably free.
    live_preview_manager = getattr(request.app.state, "live_preview_manager", None)
    if live_preview_manager is not None:
        live_preview_manager.enable_preview()

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
    module = _module_owned_by_user(request, id, user)
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

    # Spec 023 handoff: before acquiring the camera for monitoring, stop the
    # persistent pre-monitoring preview server-side and block new subscriptions
    # so a concurrent preview-stream cannot reacquire the camera. This is a
    # deterministic handoff — do NOT rely on the browser closing the MJPEG
    # stream, and do NOT add a second _CAMERA_SETTLE_SECONDS (release() already
    # settles). If the camera is not released in time, do not start monitoring.
    live_preview_manager = getattr(request.app.state, "live_preview_manager", None)
    if live_preview_manager is not None:
        handoff_ok = live_preview_manager.suspend_for_handoff(timeout=5.0)
        if not handoff_ok:
            errors = ["La cámara está ocupada. Intenta nuevamente."]
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

    # Construct dependencies for MonitoringService.start_session()
    # 1. Frame source (camera backend — picamera2 on RPi, OpenCV on PC)
    #    Pass camera resolution from ACTIVE_PROFILE for monitoring capture.
    #    Video-first (Task 10.1): use the VIDEO camera configuration with the
    #    configured recording fps so the sensor cadence is explicit.
    from src.infrastructure.config.settings import ACTIVE_PROFILE
    if getattr(ACTIVE_PROFILE, "video_first_enabled", False):
        # Spec 023: the PHYSICAL camera cadence is camera_stream_fps (~20 FPS),
        # decoupled from the recording cadence (recording_target_fps). The
        # video-mode FrameDurationLimits are driven by camera_stream_fps here;
        # the recording cadence is applied later by the RecordingSampler (Task
        # 3/4), not by the camera. Passing recording_target_fps here would keep
        # the sensor throttled to the recording rate (the coupling this Spec
        # removes).
        frame_source = create_frame_source(
            width=ACTIVE_PROFILE.camera_width,
            height=ACTIVE_PROFILE.camera_height,
            fps=int(ACTIVE_PROFILE.camera_stream_fps),
            camera_mode="video",
        )
    else:
        frame_source = create_frame_source(
            width=ACTIVE_PROFILE.camera_width,
            height=ACTIVE_PROFILE.camera_height,
            fps=ACTIVE_PROFILE.camera_fps,
        )
    if frame_source is None:
        # We suspended the preview for the handoff but never acquired the camera
        # for monitoring; let the manager re-enable preview if it is safe.
        if live_preview_manager is not None:
            live_preview_manager.resume_after_failed_handoff()
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
            created_by_user_id=user.id,
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
        # START failed before acquiring: let the manager re-enable preview only
        # if it is verifiably safe (it revalidates internally).
        if live_preview_manager is not None:
            live_preview_manager.resume_after_failed_handoff()
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
        # START failed: let the manager decide if it is safe to re-enable preview.
        if live_preview_manager is not None:
            live_preview_manager.resume_after_failed_handoff()
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

    monitoring = _monitoring_owned_by_user(request, id, user)
    if monitoring is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(monitoring.module_id)

    # A monitoring is deletable in a safe FSM state. The UI only shows the
    # delete action in these states; the backend re-validates regardless
    # (Req 2.5-2.7).
    deletable_states = {"ready_for_analysis", "completed", "error", "aborted"}
    can_delete = monitoring.status in deletable_states

    return templates.TemplateResponse(request, "agricultural/monitoring_execution.html", {
        "title": f"Monitoreando — {module.name}",
        "monitoring": monitoring,
        "module": module,
        "can_delete": can_delete,
        "show_back": True,
        "back_url": f"/modulos/{module.id}",
    })


@router.get("/monitoreos/{id}/reporte", response_class=HTMLResponse)
async def monitoring_report(request: Request, id: int, user=Depends(require_current_user_html)):
    """Screen 6: Monitoring Report Screen."""

    monitoring = _monitoring_owned_by_user(request, id, user)
    if monitoring is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    module_repo = get_module_repository(request)
    module = module_repo.get_by_id(monitoring.module_id)

    metrics_repo = get_monitoring_metrics_repository(request)
    metrics = metrics_repo.get_by_monitoring(id)

    snapshot_repo = get_snapshot_repository(request)
    snapshots = snapshot_repo.get_by_monitoring(id)

    # Count detections whose maturity was estimable (non-NULL stage) so the
    # report can communicate non-estimable maturity. The six USDA percentages
    # remain computed over this covered subset only (semantics unchanged).
    report_metrics = None
    if metrics:
        inspection_repo = get_inspection_result_repository(request)
        results = inspection_repo.get_by_monitoring(id)
        maturity_covered = sum(
            1 for r in results
            if getattr(r, "maturity_stage", None) in VALID_USDA_STAGES
        )
        report_metrics = build_report_metrics(metrics, maturity_covered)
    gallery = build_snapshot_gallery(snapshots, id)

    # Format header date/time
    date_str = _format_date_spanish(monitoring.started_at)
    time_str = _format_time(monitoring.started_at)

    # Support error query param for flash-style messages (deletion rejection).
    error = request.query_params.get("error")

    # A monitoring is deletable in a safe FSM state. The UI only shows the
    # button in these states; the backend re-validates regardless (Req 2.5-2.7).
    deletable_states = {"ready_for_analysis", "completed", "error", "aborted"}
    can_delete = monitoring.status in deletable_states

    return templates.TemplateResponse(request, "agricultural/monitoring_report.html", {
        "title": f"{module.name} — {date_str} {time_str}",
        "monitoring": monitoring,
        "module": module,
        "metrics": report_metrics,
        "gallery": gallery,
        "date_str": date_str,
        "time_str": time_str,
        "error": error,
        "can_delete": can_delete,
        "show_back": True,
        "back_url": f"/modulos/{module.id}",
    })


@router.post("/monitoreos/{id}/eliminar")
def monitoring_delete(request: Request, id: int, user=Depends(require_current_user_html)):
    """Delete a monitoring via DeletionService (confirmation handled client-side).

    Fetches the parent module id BEFORE the delete so the success redirect can
    return to the module detail, then delegates the durable deletion to
    DeletionService and returns immediately after the local delete (Req 2.4).
    The backend re-validates the monitoring's state and active worker even if
    the UI hid the button (Req 2.7). On rejection/failure (prohibited state,
    active worker, not found, durable-registration or cascade failure) no
    records are deleted and the operator is redirected back to the report with
    a Spanish ``?error=`` message; on success the monitoring stops being shown
    (redirect to the module detail).
    """
    from src.application.services.deletion_service import DeletionError

    monitoring = _monitoring_owned_by_user(request, id, user)
    if monitoring is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    module_id = monitoring.module_id
    service = get_deletion_service(request)
    try:
        service.delete_monitoring(id)
    except DeletionError as exc:
        from urllib.parse import quote
        message = quote(_deletion_error_message(exc))
        return RedirectResponse(
            url=f"/monitoreos/{id}/reporte?error={message}", status_code=303
        )
    return RedirectResponse(url=f"/modulos/{module_id}", status_code=303)


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

    # Isolation: refuse to operate on another user's monitoring.
    if _monitoring_owned_by_user(request, id, user) is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

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

    # Isolation: refuse to operate on another user's monitoring.
    if _monitoring_owned_by_user(request, id, user) is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

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


@router.post("/monitoreos/{id}/iniciar-analisis")
def monitoring_start_analysis(
    request: Request,
    id: int,
    power_source_confirmed: bool = Form(False),
    user=Depends(require_current_user_html),
):
    """Manually start the deferred analysis of a ready_for_analysis monitoring (Spec 020).

    The power-source confirmation is an OPTIONAL form field defaulting to False so
    that FastAPI does NOT return 422 when it is absent; the application evaluates
    it. When absent/false the monitoring stays in ready_for_analysis (no thread,
    no retained claim) and the operator sees a controlled Spanish error. The
    confirmation is per-attempt: it is never persisted and each retry re-confirms.

    Always redirects to the execution screen (303) with an actionable ?error= on
    rejection.
    """
    from urllib.parse import quote

    from src.application.services.monitoring_service import (
        MonitoringNotFoundError,
        NotReadyForAnalysisError,
        PowerSourceNotConfirmedError,
        AnalysisPreflightFailedError,
        AnalysisAlreadyRunningError,
        DeviceBusyError,
    )
    from app.dependencies import _get_request_session

    _logger.info(
        f"UI start-analysis request for monitoring {id} "
        f"(power_source_confirmed={power_source_confirmed})"
    )

    # Isolation: refuse to operate on another user's monitoring.
    if _monitoring_owned_by_user(request, id, user) is None:
        return RedirectResponse(url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303)

    monitoring_service = get_monitoring_service(request)
    db_session = _get_request_session(request)

    def _redirect_exec(error: str | None = None) -> RedirectResponse:
        url = f"/monitoreos/{id}/ejecucion"
        if error:
            url += f"?error={quote(error)}"
        return RedirectResponse(url=url, status_code=303)

    try:
        monitoring = monitoring_service.start_deferred_analysis(
            id, bool(power_source_confirmed), db_session
        )
        _logger.info(
            f"UI start-analysis accepted for monitoring {id}, status={monitoring.status}"
        )
        return _redirect_exec()
    except MonitoringNotFoundError:
        _logger.warning(f"UI start-analysis: monitoring {id} not found")
        return RedirectResponse(
            url="/invernaderos?error=Monitoreo+no+encontrado", status_code=303
        )
    except PowerSourceNotConfirmedError as e:
        _logger.info(f"UI start-analysis: power source not confirmed for {id}")
        return _redirect_exec(str(e))
    except NotReadyForAnalysisError as e:
        _logger.info(f"UI start-analysis: monitoring {id} not ready for analysis")
        return _redirect_exec(str(e))
    except AnalysisPreflightFailedError as e:
        _logger.info(
            f"UI start-analysis: preflight failed for {id} ({e.reason_code})"
        )
        return _redirect_exec(str(e))
    except AnalysisAlreadyRunningError as e:
        _logger.info(f"UI start-analysis: analysis already running for {id}")
        return _redirect_exec(str(e))
    except DeviceBusyError as e:
        _logger.info(f"UI start-analysis: device busy for {id}")
        return _redirect_exec(str(e))



# ---------------------------------------------------------------------------
# Activity Log endpoints (Bitácora agrícola)
# ---------------------------------------------------------------------------


@router.get("/modulos/{id}/actividades", response_class=HTMLResponse)
async def activity_list(request: Request, id: int, user=Depends(require_current_user_html)):
    """Activity log list for a module."""
    from src.application.services.activity_service import ActivityService

    module = _module_owned_by_user(request, id, user)
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

    module = _module_owned_by_user(request, id, user)
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

    module = _module_owned_by_user(request, id, user)
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
        "error": request.query_params.get("error"),
        "success_message": request.query_params.get("success"),
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
        # Fetch data scoped to the current user's greenhouses only (Spec 022):
        # descendants are collected exclusively under the user's own greenhouses,
        # so another user's hierarchy/activity logs are never exported.
        greenhouses = gh_repo.get_all_by_owner(user.id)
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
        # Scope activity logs to the user's modules only (no cross-user leakage).
        activity_logs = []
        for mod in modules:
            activity_logs.extend(activity_log_repo.list_by_module(mod.id))

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
    # Isolation: another user's package is treated as not-found.
    if package is None or package.created_by_user_id != user.id:
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
        "error": request.query_params.get("error"),
        "success_message": request.query_params.get("success"),
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
    # Isolation: another user's package is treated as not-found.
    if package is None or package.created_by_user_id != user.id:
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


@router.post("/exportar/{id}/eliminar")
def export_delete(request: Request, id: int, user=Depends(require_current_user_html)):
    """Delete an owned export package (ZIP file + record) via the app service.

    Thin route: authenticate, obtain the repository, delegate coordination to
    ``ExportDeletionService``, and translate the outcome into a Spanish
    ``RedirectResponse``. All ownership, status, and filesystem-safety logic
    lives in the service. Never a GET; never touches source data, sync, outbox,
    or Supabase.
    """
    from urllib.parse import quote

    from src.application.services.export_deletion_service import (
        ExportDeletionService,
        ExportNotFoundError,
        ExportGeneratingError,
        ExportStatusNotDeletableError,
        UnsafeExportPathError,
        ExportDeletionError,
    )

    export_repo = get_export_package_repository(request)
    service = ExportDeletionService(export_repo)

    try:
        service.delete_export(package_id=id, user_id=user.id)
    except ExportNotFoundError:
        return RedirectResponse(
            url="/exportar?error=Exportación+no+encontrada", status_code=303
        )
    except ExportGeneratingError as exc:
        return RedirectResponse(
            url=f"/exportar?error={quote(str(exc))}", status_code=303
        )
    except ExportStatusNotDeletableError as exc:
        # pending or any other non-terminal status (fail-closed).
        return RedirectResponse(
            url=f"/exportar?error={quote(str(exc))}", status_code=303
        )
    except UnsafeExportPathError as exc:
        return RedirectResponse(
            url=f"/exportar?error={quote(str(exc))}", status_code=303
        )
    except ExportDeletionError as exc:
        # Controlled filesystem/record failure: no traceback/500.
        return RedirectResponse(
            url=f"/exportar?error={quote(str(exc))}", status_code=303
        )

    return RedirectResponse(
        url="/exportar?success=Exportación+eliminada", status_code=303
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

    # Match the current-user hierarchy used by the local ZIP export route.
    greenhouse_repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    monitorings = []
    activity_logs = []
    for greenhouse in greenhouse_repo.get_all_by_owner(user.id):
        for module in module_repo.get_by_greenhouse(greenhouse.id):
            monitorings.extend(monitoring_repo.get_by_module(module.id))
            activity_logs.extend(activity_log_repo.list_by_module(module.id))

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

    # Local multiuser isolation (Spec 022): local sync/export covers only the
    # current user's hierarchy (greenhouses -> modules -> monitorings/activities),
    # never another user's records.
    _gh_repo = get_greenhouse_repository(request)
    _module_repo = get_module_repository(request)
    _user_greenhouses = _gh_repo.get_all_by_owner(user.id)
    _user_modules = []
    for _gh in _user_greenhouses:
        _user_modules.extend(_module_repo.get_by_greenhouse(_gh.id))
    monitorings = []
    activity_logs = []
    for _mod in _user_modules:
        monitorings.extend(monitoring_repo.get_by_module(_mod.id))
        activity_logs.extend(activity_log_repo.list_by_module(_mod.id))

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

    # Generate the export (reuses existing ExportService). Greenhouses/modules
    # were already resolved (user-scoped) above; only the extra repos are needed here.
    from app.dependencies import (
        get_snapshot_repository,
        get_monitoring_metrics_repository,
        get_activity_type_repository,
    )

    snapshot_repo = get_snapshot_repository(request)
    metrics_repo = get_monitoring_metrics_repository(request)
    activity_type_repo = get_activity_type_repository(request)

    # Reuse the user-scoped greenhouses/modules resolved above.
    greenhouses = _user_greenhouses
    modules = _user_modules

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
