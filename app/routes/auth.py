"""Authentication routes: login, logout, register."""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import (
    get_current_user_optional,
    get_auth_service,
    get_hybrid_auth_service,
    get_supabase_config,
)
from src.application.services.auth_service import SESSION_COOKIE_NAME, SESSION_MAX_AGE

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    """Display the login form. Redirect to main page if already authenticated."""
    if user is not None:
        return RedirectResponse(url="/invernaderos", status_code=302)
    next_url = request.query_params.get("next", "")
    # Validate: only allow local paths (prevent open redirect)
    if next_url and (not next_url.startswith("/") or next_url.startswith("//")):
        next_url = ""
    registration_available = get_supabase_config(request) is not None
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": None, "next_url": next_url, "registration_available": registration_available},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    """Process login form submission via HybridAuthService."""
    hybrid_auth = get_hybrid_auth_service(request)

    # Validate next parameter
    next_url = next if (next and next.startswith("/") and not next.startswith("//")) else ""

    result = hybrid_auth.login(email, password)

    if not result.success or result.user is None or result.user.id is None:
        error_message = result.error_message or "No fue posible iniciar sesión."
        registration_available = get_supabase_config(request) is not None
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": error_message, "next_url": next_url, "registration_available": registration_available},
            status_code=401,
        )

    # Create local session token and set cookie
    auth = get_auth_service()
    token = auth.create_session_token(result.user.id)

    redirect_to = next_url if next_url else "/invernaderos"

    response = RedirectResponse(url=redirect_to, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/registro", response_class=HTMLResponse)
async def register_page(request: Request):
    """Display the registration form."""
    registration_available = get_supabase_config(request) is not None
    return templates.TemplateResponse(
        request,
        "auth/register.html",
        {
            "error": None,
            "success_message": None,
            "registration_available": registration_available,
            "full_name_value": "",
            "email_value": "",
        },
    )


@router.post("/registro", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    """Process registration form submission via HybridAuthService."""
    config = get_supabase_config(request)
    registration_available = config is not None

    # Check if registration is available
    if not registration_available:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {
                "error": "Registro no disponible sin conexión remota.",
                "success_message": None,
                "registration_available": False,
                "full_name_value": full_name,
                "email_value": email,
            },
            status_code=400,
        )

    # Server-side validation
    if not full_name.strip() or not email.strip() or not password or not confirm_password:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {
                "error": "Completa todos los campos obligatorios.",
                "success_message": None,
                "registration_available": True,
                "full_name_value": full_name,
                "email_value": email,
            },
            status_code=400,
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {
                "error": "Las contraseñas no coinciden.",
                "success_message": None,
                "registration_available": True,
                "full_name_value": full_name,
                "email_value": email,
            },
            status_code=400,
        )

    # Call HybridAuthService.register
    hybrid_auth = get_hybrid_auth_service(request)
    result = hybrid_auth.register(email.strip(), password, full_name.strip())

    if not result.success or result.user is None or result.user.id is None:
        error_message = result.error_message or "No se pudo crear la cuenta."
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {
                "error": error_message,
                "success_message": None,
                "registration_available": True,
                "full_name_value": full_name,
                "email_value": email,
            },
            status_code=400,
        )

    # Success: create local session cookie
    auth = get_auth_service()
    token = auth.create_session_token(result.user.id)

    response = templates.TemplateResponse(
        request,
        "auth/register.html",
        {
            "error": None,
            "success_message": "Tu cuenta fue creada. Ahora puedes iniciar sesión sin Internet.",
            "registration_available": True,
            "full_name_value": "",
            "email_value": "",
        },
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response
