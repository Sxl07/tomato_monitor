"""Authentication routes: login, logout."""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_user_optional, get_auth_service, _get_request_session
from src.application.services.auth_service import SESSION_COOKIE_NAME, SESSION_MAX_AGE
from src.infrastructure.persistence.repositories.sql_user_repository import SqlUserRepository

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
    return templates.TemplateResponse(request, "auth/login.html", {"error": None, "next_url": next_url})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    """Process login form submission."""
    auth = get_auth_service()
    session = _get_request_session(request)
    user_repo = SqlUserRepository(session=session)

    # Validate next parameter
    next_url = next if (next and next.startswith("/") and not next.startswith("//")) else ""

    user = auth.authenticate(email, password, user_repo)
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Credenciales incorrectas. Verifica tu email y contraseña.", "next_url": next_url},
            status_code=401,
        )

    # Update last_login_at
    from datetime import datetime, timezone

    user_repo.update(user.id, {"last_login_at": datetime.now(timezone.utc).replace(tzinfo=None)})

    # Create session token and set cookie
    token = auth.create_session_token(user.id)

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


@router.post("/logout")
async def logout(request: Request):
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response
