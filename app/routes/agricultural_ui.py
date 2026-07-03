"""Agricultural UI router — farmer-facing screens for the Tomato Monitor.

This router provides all page endpoints for the agricultural interface,
optimized for the Raspberry Pi DSI 7" touchscreen (800×480).
Screens are rendered server-side with Jinja2 templates.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError

from app.context_builders import (
    build_greenhouse_cards,
    build_module_cards,
    build_monitoring_history,
    build_report_metrics,
    build_snapshot_gallery,
    _format_date_spanish,
    _format_time,
)
from src.application.validators import (
    ValidationError,
    validate_greenhouse_name,
    validate_module_name,
    validate_dimensions,
    validate_notes,
)
from app.dependencies import (
    get_greenhouse_repository,
    get_module_repository,
    get_monitoring_repository,
    get_snapshot_repository,
    get_monitoring_metrics_repository,
    get_monitoring_service,
)
from src.application.services.model_service import ModelService
from src.infrastructure.config.settings import DETECTION_MODEL_PATH
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.exceptions import DuplicateModuleError

router = APIRouter(tags=["agricultural-ui"])
templates = Jinja2Templates(directory="app/templates")

import logging as _logging

_logger = _logging.getLogger(__name__)


def _build_inference_runner():
    """Construct SnapshotInferenceRunner with loaded models.

    Returns None if models cannot be loaded (files missing, import errors, etc.).
    Logs the cost of on-demand loading for visibility.
    """
    try:
        from src.infrastructure.vision.detectron_detector import build_tomato_detector
        from src.infrastructure.vision.resnet_health_classifier import build_health_model_resnet
        from src.infrastructure.vision.snapshot_inference_runner import SnapshotInferenceRunner
        from src.infrastructure.config.settings import DETECTION_MODEL_PATH as det_path
        from src.infrastructure.config.settings import HEALTH_MODEL_B_PATH

        # Verify model files exist before attempting to load
        if not det_path.exists():
            _logger.warning(f"Detection model not found at {det_path}")
            return None
        if not HEALTH_MODEL_B_PATH.exists():
            _logger.warning(f"Health model not found at {HEALTH_MODEL_B_PATH}")
            return None

        _logger.info("Cargando modelos de inferencia (esto puede tomar unos segundos)...")
        detector = build_tomato_detector(det_path)
        health_model, health_transform = build_health_model_resnet(HEALTH_MODEL_B_PATH)
        _logger.info("Modelos de inferencia cargados correctamente.")

        from src.infrastructure.config.settings import ACTIVE_PROFILE

        return SnapshotInferenceRunner(
            detector=detector,
            health_model=health_model,
            health_transform=health_transform,
            skip_maturity=ACTIVE_PROFILE.skip_maturity,
            detection_score_threshold=ACTIVE_PROFILE.detection_score_threshold,
            inference_input_size=(ACTIVE_PROFILE.inference_input_width, ACTIVE_PROFILE.inference_input_height),
            run_maturity_only_for_healthy=ACTIVE_PROFILE.run_maturity_only_for_healthy,
        )
    except Exception as e:
        _logger.error(f"Error loading inference models: {e}")
        return None


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@router.get("/", response_class=RedirectResponse)
def home():
    """Redirect home to the greenhouse list screen."""
    return RedirectResponse(url="/invernaderos", status_code=302)


# ---------------------------------------------------------------------------
# Greenhouse endpoints
# ---------------------------------------------------------------------------


@router.get("/invernaderos", response_class=HTMLResponse)
def greenhouse_list(request: Request):
    """Screen 1: Greenhouse List Screen."""
    repo = get_greenhouse_repository(request)
    module_repo = get_module_repository(request)
    monitoring_repo = get_monitoring_repository(request)

    greenhouses = repo.get_all()

    # Build lookup dicts for context builder
    modules_by_gh: dict[int, list] = {}
    monitorings_by_module: dict[int, list] = {}
    for gh in greenhouses:
        modules = module_repo.get_by_greenhouse(gh.id)
        modules_by_gh[gh.id] = modules
        for m in modules:
            monitorings_by_module[m.id] = monitoring_repo.get_by_module(m.id)

    cards = build_greenhouse_cards(greenhouses, modules_by_gh, monitorings_by_module)

    # Support error query param for flash-style messages
    error = request.query_params.get("error")

    return templates.TemplateResponse(request, "agricultural/greenhouse_list.html", {
        "title": "Mis Invernaderos",
        "greenhouses": cards,
        "show_back": False,
        "error": error,
    })


@router.get("/invernaderos/crear", response_class=HTMLResponse)
def greenhouse_create_form(request: Request):
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
def greenhouse_create(request: Request, name: str = Form(...), location: str = Form("")):
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
def greenhouse_detail(request: Request, id: int):
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
def greenhouse_edit_form(request: Request, id: int):
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
def greenhouse_edit(request: Request, id: int, name: str = Form(...), location: str = Form("")):
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
def greenhouse_delete(request: Request, id: int):
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
def module_create_form(request: Request, gh_id: int):
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
            "errors": errors,
        })

    repo = get_module_repository(request)
    module = Module(
        greenhouse_id=gh_id,
        name=validated_name,
        crop_type=crop_type.strip() or "Tomate Cherry",
        width_m=parsed_width,
        length_m=parsed_length,
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
            "errors": errors,
        })

    return RedirectResponse(url=f"/modulos/{created.id}", status_code=303)


@router.get("/modulos/{id}", response_class=HTMLResponse)
def module_detail(request: Request, id: int):
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

    # Format dimensions for info panel
    if module.width_m is not None and module.length_m is not None:
        dimensions_display = f"{module.width_m} × {module.length_m} m"
    else:
        dimensions_display = "No configuradas"

    return templates.TemplateResponse(request, "agricultural/module_detail.html", {
        "title": module.name,
        "module": module,
        "dimensions_display": dimensions_display,
        "history": history,
        "show_back": True,
        "back_url": f"/invernaderos/{module.greenhouse_id}",
    })


@router.get("/modulos/{id}/editar", response_class=HTMLResponse)
def module_edit_form(request: Request, id: int):
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
            "errors": errors,
        })

    try:
        repo.update(id, {
            "name": validated_name,
            "crop_type": crop_type.strip() or "Tomate Cherry",
            "width_m": parsed_width,
            "length_m": parsed_length,
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
            "errors": errors,
        })

    return RedirectResponse(url=f"/modulos/{id}", status_code=303)


@router.post("/modulos/{id}/eliminar")
def module_delete(request: Request, id: int):
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
def monitoring_setup(request: Request, id: int):
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
    width_m: str = Form(...),
    length_m: str = Form(...),
    notes: str = Form(""),
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
    width: float = 0.0
    length: float = 0.0
    try:
        width, length = validate_dimensions(width_m, length_m)
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

    # Auto-save dimensions to module record (R4.7)
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

    # 2. Inference runner (detector + health + maturity models)
    inference_runner = _build_inference_runner()
    if inference_runner is None:
        errors = ["No se pudieron cargar los modelos de inferencia. Verifica que los archivos estén en su lugar."]
        frame_source.release()
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

    # 3. DB session and log service
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
            inference_runner=inference_runner,
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
def monitoring_execution(request: Request, id: int):
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
def monitoring_report(request: Request, id: int):
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
def monitoring_abort(request: Request, id: int):
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
