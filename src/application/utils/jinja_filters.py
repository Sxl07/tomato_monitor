"""Jinja2 template filters for the agricultural UI."""

from src.application.utils.timezone import to_bogota


def filter_to_bogota(dt, fmt="%d/%m/%Y %H:%M"):
    """Jinja2 filter: convert UTC datetime to America/Bogota formatted string.

    Usage in templates:
        {{ dt | to_bogota }}              -> "15/01/2024 21:17"
        {{ dt | to_bogota('%H:%M') }}     -> "21:17"
        {{ dt | to_bogota('%d/%m/%Y') }}  -> "15/01/2024"
    """
    local = to_bogota(dt)
    return local.strftime(fmt) if local else ""


def register_filters(templates_instance):
    """Register custom filters on a Jinja2Templates instance.

    Call after creating each Jinja2Templates instance:
        templates = Jinja2Templates(directory="app/templates")
        register_filters(templates)
    """
    templates_instance.env.filters["to_bogota"] = filter_to_bogota