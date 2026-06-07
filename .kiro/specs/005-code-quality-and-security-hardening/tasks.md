# Implementation Plan: Code Quality and Security Hardening

## Overview

Implement a systematic input validation layer, path traversal protection, structured logging, global error handling, Jinja2 security audit, and test suites for the Tomato Monitor codebase. All changes are additive, non-breaking, and compatible with Raspberry Pi 5 (CPU-only, Python 3.10+). No new external dependencies required except `hypothesis` for property-based tests.

## Tasks

- [x] 1. Add validation constants and configure structured logging
  - [x] 1.1 Add validation constants to `src/infrastructure/config/settings.py`
    - Add `MAX_GREENHOUSE_NAME_LENGTH = 100`, `MAX_MODULE_NAME_LENGTH = 100`, `MAX_NOTES_LENGTH = 500`, `MAX_DIMENSION_METERS = 1000.0`, `MIN_DIMENSION_METERS = 0.0` under a `# --- Validation Limits ---` section
    - _Requirements: 12.1, 12.3_

  - [x] 1.2 Create structured logging configuration at `src/infrastructure/config/logging_config.py`
    - Implement `configure_logging(level=logging.INFO)` with format `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` and date format `"%Y-%m-%d %H:%M:%S"`
    - Suppress noisy `uvicorn.access` logger to WARNING
    - Use `logging.basicConfig(force=True)` for idempotent configuration
    - _Requirements: 6.1, 6.6_

  - [x] 1.3 Integrate `configure_logging()` into `app/main.py` lifespan startup
    - Call `configure_logging()` at the beginning of the lifespan context manager, before database initialization
    - _Requirements: 6.1_

- [x] 2. Implement input validators
  - [x] 2.1 Create `src/application/validators.py` with `ValidationError` exception and pure validation functions
    - Implement `ValidationError(field, message)` exception class
    - Implement `parse_finite_float(value, field_name)` — reject NaN, Infinity, non-numeric
    - Implement `validate_greenhouse_name(name)` — trim, reject empty/whitespace, enforce ≤100 chars
    - Implement `validate_module_name(name)` — same rules as greenhouse name
    - Implement `validate_dimensions(width, length)` — parse as finite floats, enforce > 0 and ≤ 1000
    - Implement `validate_notes(notes)` — trim, truncate to 500 chars, return None if empty
    - Import limits from `src/infrastructure/config/settings.py`
    - Error messages in Spanish (e.g., "El nombre no puede estar vacío.")
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 2.2 Write property test for greenhouse name validation (Property 1)
    - **Property 1: Greenhouse name validation correctness**
    - Generate random strings with `hypothesis`, verify: returns trimmed non-empty string ≤100 iff `s.strip()` is non-empty and ≤100 chars, else raises `ValidationError`
    - Create `tests/application/test_validators.py`
    - **Validates: Requirements 1.1, 1.4**

  - [ ]* 2.3 Write property test for dimension validation (Property 2)
    - **Property 2: Dimension validation correctness**
    - Generate random string pairs with `hypothesis`, verify: returns `(float(w), float(l))` iff both parse as finite positive numbers ≤1000, else raises `ValidationError`
    - Rejection of NaN, Infinity, negative, zero, non-numeric, >1000
    - **Validates: Requirements 1.2, 1.5**

  - [ ]* 2.4 Write property test for notes sanitization (Property 3)
    - **Property 3: Notes sanitization invariants**
    - Generate random strings, verify: returns None if `s.strip()` is empty, else returns `s.strip()[:500]` with `len(r) <= 500` and no leading/trailing whitespace
    - **Validates: Requirements 1.3, 1.4**

- [x] 3. Implement path sanitizer
  - [x] 3.1 Create `src/infrastructure/security/__init__.py` and `src/infrastructure/security/path_sanitizer.py`
    - Implement `PathTraversalError(reason)` exception class
    - Implement `validate_safe_path(path, allowed_base)` — reject null bytes, resolve candidate, verify containment within `allowed_base.resolve()`
    - Return resolved absolute Path if valid, raise `PathTraversalError` otherwise
    - _Requirements: 3.1, 3.2, 3.4_

  - [ ]* 3.2 Write property test for path sanitizer containment (Property 4)
    - **Property 4: Path sanitizer containment**
    - Generate random path strings (including traversal attempts `../`, null bytes, absolute prefixes), verify: returns resolved Path within `base.resolve()` iff containment holds, else raises `PathTraversalError`
    - Create `tests/infrastructure/security/__init__.py` and `tests/infrastructure/security/test_path_sanitizer.py`
    - **Validates: Requirements 3.1, 3.2, 3.4**

- [x] 4. Checkpoint — Validate core modules
  - Ensure all tests pass (`pytest tests/application/test_validators.py tests/infrastructure/security/test_path_sanitizer.py`), ask the user if questions arise.

- [x] 5. Implement global exception handler and error template
  - [x] 5.1 Create error template at `app/templates/error.html`
    - Extend `base_agricultural.html`
    - Display a generic error message variable with appropriate styling
    - Include a "Volver al inicio" link to `/invernaderos`
    - _Requirements: 4.6, 7.2_

  - [x] 5.2 Add global exception handler to `app/main.py`
    - Register `@app.exception_handler(Exception)` that logs full traceback at ERROR level and returns the `error.html` template with a generic Spanish message ("Ocurrió un error inesperado. Intenta de nuevo.")
    - Never expose internal details (paths, SQL, stack traces) to the user
    - _Requirements: 4.6, 6.3_

- [x] 6. Refactor route handlers to use validators
  - [x] 6.1 Refactor `greenhouse_create` and `greenhouse_edit` in `app/routes/agricultural_ui.py`
    - Import and call `validate_greenhouse_name(name)` before repository operations
    - Catch `ValidationError` and re-render form with Spanish error messages
    - Remove any inline ad-hoc validation logic
    - _Requirements: 1.1, 1.4, 8.1_

  - [x] 6.2 Refactor `module_create` and `module_edit` in `app/routes/agricultural_ui.py`
    - Import and call `validate_module_name(name)` and `validate_dimensions(width, length)` before repository operations
    - Catch `ValidationError` and re-render form with field-specific Spanish error messages
    - Remove inline `_parse_optional_float` or replace with `parse_finite_float` from validators
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 8.1_

  - [x] 6.3 Refactor `monitoring_start` in `app/routes/agricultural_ui.py`
    - Apply `validate_dimensions(width, length)` and `validate_notes(notes)` before creating the monitoring session
    - Catch `ValidationError` and re-render setup form with errors
    - _Requirements: 1.2, 1.3, 1.5, 8.1_

  - [x] 6.4 Apply path sanitizer to snapshot-serving logic
    - Where snapshot `image_path` values are read from the database for display or file serving, call `validate_safe_path(image_path, OUTPUTS_DIR)` before accessing the filesystem
    - Catch `PathTraversalError` and return 403 or redirect with error message
    - _Requirements: 3.1, 3.3_

- [x] 7. Jinja2 autoescape audit
  - [x] 7.1 Audit all templates in `app/templates/` for `|safe` filter usage on user-provided data
    - Verify `Jinja2Templates` is configured with autoescape enabled (default for FastAPI)
    - Search for `|safe` occurrences — remove any applied to greenhouse names, module names, or notes
    - Document audit result as a comment in the commit message
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 8. Checkpoint — Validate route refactoring
  - Ensure all tests pass and the application starts without errors (`python -c "from app.main import app"`), ask the user if questions arise.

- [x] 9. Implement repository CRUD tests
  - [x] 9.1 Create test infrastructure: `tests/__init__.py`, `tests/infrastructure/__init__.py`, `tests/infrastructure/persistence/__init__.py`, `tests/conftest.py`
    - Set up shared pytest fixtures: in-memory SQLite engine, session factory, `create_all()` for schema
    - _Requirements: 9.5_

  - [x] 9.2 Create `tests/infrastructure/persistence/test_repository_crud.py`
    - Test Greenhouse repository: create, read by id, read all, update, delete with cascade
    - Test Module repository: create under greenhouse, read by greenhouse, unique constraint on (greenhouse_id, name), delete with cascade
    - Test Monitoring repository: create under module, read by module, status update, delete with cascade
    - Test error cases: create with non-existent parent IDs, read non-existent records, duplicate name violations
    - Use in-memory SQLite for isolation and speed
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

- [x] 10. Implement monitoring service tests
  - [x] 10.1 Create `tests/application/__init__.py` and `tests/application/test_monitoring_service.py`
    - Test happy-path state machine: initializing → running → finishing → completed
    - Test pause and resume: running → paused → running
    - Test abort: running → aborted
    - Test invalid transitions (e.g., completed → running) raise `InvalidTransitionError`
    - Test `ActiveSessionError` when starting monitoring on module with active session
    - Mock repository dependencies, use in-memory SQLite session
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [x] 11. Implement import validation test
  - [x] 11.1 Create `tests/test_imports.py`
    - Dynamically discover and import all modules under `app/` and `src/` without raising `ImportError`
    - Skip modules that require optional hardware at import time (camera, GPU) with clear skip reason
    - Execute without connected camera, GPU, or pre-loaded model files
    - _Requirements: 11.1, 11.2, 11.3_

- [x] 12. Checkpoint — Full test suite
  - Ensure all tests pass (`pytest tests/ -v`), ask the user if questions arise.

- [ ]* 13. Write Jinja2 autoescaping property test (Property 5)
  - [ ]* 13.1 Write property test for Jinja2 autoescape behavior
    - **Property 5: Jinja2 autoescaping neutralizes HTML in user strings**
    - Generate strings containing HTML special characters (`<`, `>`, `&`, `"`, `'`), render through a Jinja2 template variable (without `|safe`), verify output contains escaped entities and not raw HTML
    - Create `tests/presentation/__init__.py` and `tests/presentation/test_template_security.py`
    - **Validates: Requirements 7.1, 7.2**

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation between logical groups
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The project uses `hypothesis` for property-based testing — install with `pip install hypothesis` if not already available
- All tests run with: `source ~/venvs/tomato_monitor/bin/activate && pytest tests/ -v`
- No new dependencies besides `hypothesis` are introduced

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.2", "5.1"] },
    { "id": 3, "tasks": ["5.2", "6.1", "6.2", "6.3"] },
    { "id": 4, "tasks": ["6.4", "7.1"] },
    { "id": 5, "tasks": ["9.1"] },
    { "id": 6, "tasks": ["9.2", "10.1", "11.1"] },
    { "id": 7, "tasks": ["13.1"] }
  ]
}
```
