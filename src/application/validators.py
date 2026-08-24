"""Input validation functions for the Tomato Monitor application layer.

All validators are pure functions: they accept raw input strings, return
sanitized values, or raise ValidationError with a field name and Spanish message.
"""
from __future__ import annotations

import math
from typing import Optional

from src.infrastructure.config.settings import (
    MAX_GREENHOUSE_NAME_LENGTH,
    MAX_MODULE_NAME_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_DIMENSION_METERS,
)


class ValidationError(Exception):
    """Raised when input validation fails.

    Attributes:
        field: Name of the field that failed validation.
        message: Human-readable Spanish error message.
    """

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def validate_greenhouse_name(name: str) -> str:
    """Validate and sanitize a greenhouse name.

    Returns trimmed name. Raises ValidationError if empty or too long.
    """
    trimmed = name.strip()
    if not trimmed:
        raise ValidationError("name", "El nombre del invernadero no puede estar vacío.")
    if len(trimmed) > MAX_GREENHOUSE_NAME_LENGTH:
        raise ValidationError(
            "name",
            f"El nombre no puede exceder {MAX_GREENHOUSE_NAME_LENGTH} caracteres."
        )
    return trimmed


def validate_module_name(name: str) -> str:
    """Validate and sanitize a module name.

    Returns trimmed name. Raises ValidationError if empty or too long.
    """
    trimmed = name.strip()
    if not trimmed:
        raise ValidationError("name", "El nombre del módulo no puede estar vacío.")
    if len(trimmed) > MAX_MODULE_NAME_LENGTH:
        raise ValidationError(
            "name",
            f"El nombre no puede exceder {MAX_MODULE_NAME_LENGTH} caracteres."
        )
    return trimmed


def parse_finite_float(value: str, field_name: str) -> float:
    """Parse a string as a finite float, rejecting NaN and Infinity.

    Raises ValidationError if value is not a finite number.
    """
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(field_name, "Este campo es obligatorio.")
    try:
        result = float(trimmed)
    except (ValueError, TypeError):
        raise ValidationError(field_name, "Debe ser un número válido.")
    if math.isnan(result) or math.isinf(result):
        raise ValidationError(field_name, "Debe ser un número finito.")
    return result


def validate_dimensions(width: str, length: str) -> tuple[float, float]:
    """Parse and validate module dimensions from form strings.

    Returns (width_float, length_float), both > 0 and <= MAX_DIMENSION_METERS.
    Raises ValidationError if values are invalid.
    """
    w = parse_finite_float(width, "width_m")
    l = parse_finite_float(length, "length_m")

    if w <= 0:
        raise ValidationError("width_m", "El ancho debe ser mayor que cero.")
    if l <= 0:
        raise ValidationError("length_m", "El largo debe ser mayor que cero.")
    if w > MAX_DIMENSION_METERS:
        raise ValidationError(
            "width_m",
            f"El ancho no puede exceder {MAX_DIMENSION_METERS} metros."
        )
    if l > MAX_DIMENSION_METERS:
        raise ValidationError(
            "length_m",
            f"El largo no puede exceder {MAX_DIMENSION_METERS} metros."
        )
    return (w, l)


def validate_notes(notes: str) -> Optional[str]:
    """Sanitize monitoring notes.

    Returns trimmed string truncated to MAX_NOTES_LENGTH, or None if empty.
    """
    trimmed = notes.strip()
    if not trimmed:
        return None
    return trimmed[:MAX_NOTES_LENGTH]


def validate_optional_dimensions(
    width: str, length: str
) -> tuple[Optional[float], Optional[float]]:
    """Parse and validate optional module dimensions from form strings.

    Rules:
    - Both empty -> (None, None) valid
    - Both filled with positive values -> (width, length) valid
    - One filled, one empty -> ValidationError
    - Invalid numbers or negative -> ValidationError

    Returns (width_float_or_None, length_float_or_None).
    Raises ValidationError if values are inconsistent or invalid.
    """
    w_stripped = width.strip()
    l_stripped = length.strip()

    # Both empty: valid
    if not w_stripped and not l_stripped:
        return (None, None)

    # One filled, one empty: error
    if not w_stripped and l_stripped:
        raise ValidationError(
            "width_m",
            "Ingresa ambas dimensiones o deja ambos campos vacíos."
        )
    if w_stripped and not l_stripped:
        raise ValidationError(
            "length_m",
            "Ingresa ambas dimensiones o deja ambos campos vacíos."
        )

    # Both filled: validate as positive finite floats
    return validate_dimensions(w_stripped, l_stripped)