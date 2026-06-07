# Requirements Document

## Introduction

This spec covers code quality and security hardening for the Tomato Monitor codebase. The system now includes SQLite persistence with SQLAlchemy repositories, a FastAPI web interface with Jinja2 templates and HTML forms, a monitoring execution flow with background threads, camera integration, and edge optimization profiles. A systematic pass is needed to ensure input validation, robust error handling, path traversal protection, proper session management, structured logging, and test coverage for critical paths.

All improvements must be non-breaking, compatible with Raspberry Pi 5 (CPU-only), and implementable as small, reviewable changes.

## Glossary

- **Validator**: The input validation layer that checks form fields, API parameters, and file paths before processing.
- **Route_Handler**: A FastAPI route function in `app/routes/` that receives HTTP requests and delegates to services.
- **Session_Manager**: The SQLAlchemy session lifecycle logic in `app/dependencies.py` that provides commit/rollback/close semantics per request.
- **Repository**: An SQLAlchemy-based data access class in `src/infrastructure/persistence/repositories/` that performs CRUD operations.
- **Monitoring_Service**: The application service in `src/application/services/monitoring_service.py` that orchestrates monitoring state transitions.
- **Logger**: The Python `logging`-based structured logging configuration used across all layers.
- **Path_Sanitizer**: A utility that validates file paths against traversal attacks (e.g., `../` sequences) before filesystem operations.
- **Error_Handler**: The exception handling logic that translates domain/infrastructure exceptions into user-facing messages or HTTP responses.
- **Test_Suite**: The pytest-based test collection covering repositories, services, and route handlers.

## Requirements

### Requirement 1: Form Input Validation

**User Story:** As a developer, I want all form inputs validated before processing, so that invalid or malicious data never reaches the persistence or service layer.

#### Acceptance Criteria

1. WHEN a greenhouse name is submitted via form, THE Validator SHALL reject the input and return a descriptive error IF the name is empty after trimming, exceeds 100 characters, or contains only whitespace.
2. WHEN module dimensions (width_m, length_m) are submitted via form, THE Validator SHALL reject the input and return a descriptive error IF the values are not positive numbers or exceed 1000 meters.
3. WHEN monitoring notes are submitted via form, THE Validator SHALL trim leading and trailing whitespace and truncate the value to 500 characters.
4. WHEN a string form field is submitted, THE Validator SHALL trim leading and trailing whitespace before further processing.
5. WHEN a numeric form field is submitted, THE Validator SHALL reject the input IF the value cannot be parsed as a finite number (rejects NaN, Infinity).

### Requirement 2: API Parameter Validation

**User Story:** As a developer, I want all API endpoint parameters validated, so that the monitoring API rejects malformed requests with clear error messages.

#### Acceptance Criteria

1. WHEN a StartMonitoringRequest is received with module_id that does not exist, THE Route_Handler SHALL return HTTP 404 with a descriptive error message.
2. WHEN a monitoring control endpoint receives a monitoring_id that does not exist, THE Route_Handler SHALL return HTTP 404 with a descriptive error message.
3. WHEN a monitoring control endpoint receives a request for an invalid state transition, THE Route_Handler SHALL return HTTP 409 with a message explaining the current state and the attempted action.
4. WHEN a route receives a path parameter that is not a positive integer, THE Route_Handler SHALL return HTTP 422 with a validation error.

### Requirement 3: Path Traversal Protection

**User Story:** As a developer, I want all file path inputs sanitized against directory traversal, so that attackers cannot read or write files outside the intended directories.

#### Acceptance Criteria

1. WHEN a file path is constructed from user input or database values, THE Path_Sanitizer SHALL resolve the path and verify it resides within the allowed base directory (project root or configured output directory).
2. IF a resolved path escapes the allowed base directory, THEN THE Path_Sanitizer SHALL reject the path and raise a security error without performing any filesystem operation.
3. WHEN snapshot image_path values are read from the database for display, THE Path_Sanitizer SHALL validate that the resolved path is within the configured outputs directory before serving the file.
4. THE Path_Sanitizer SHALL reject paths containing null bytes, regardless of other content.

### Requirement 4: Error Handling Robustness

**User Story:** As a developer, I want consistent and informative error handling across all routes, so that failures produce clear messages without leaking internal details to the user.

#### Acceptance Criteria

1. THE Route_Handler SHALL NOT contain any bare `except Exception: pass` or `except: pass` patterns that silently swallow errors.
2. WHEN a database operation fails, THE Session_Manager SHALL execute a rollback before re-raising or translating the exception.
3. WHEN a camera initialization fails during monitoring start, THE Error_Handler SHALL return a user-facing message indicating the camera is unavailable and suggesting the user check the connection.
4. WHEN a model file fails to load, THE Error_Handler SHALL return a user-facing message indicating the model files could not be loaded and suggesting the user verify file presence.
5. WHEN a filesystem error occurs (disk full, permission denied), THE Error_Handler SHALL return a user-facing message describing the issue without exposing full system paths.
6. WHEN an unexpected exception occurs in a route handler, THE Error_Handler SHALL log the full exception with traceback and return a generic error message to the user.

### Requirement 5: Session Management Hardening

**User Story:** As a developer, I want all database sessions properly managed with commit, rollback, and close semantics, so that connections are never leaked and data integrity is maintained.

#### Acceptance Criteria

1. THE Session_Manager SHALL ensure every SQLAlchemy session opened during a request is closed when the request completes, regardless of success or failure.
2. WHEN a request completes successfully, THE Session_Manager SHALL commit the session transaction before closing.
3. WHEN an exception occurs during request processing, THE Session_Manager SHALL rollback the session transaction before closing.
4. THE Session_Manager SHALL provide a single session instance per request, shared across all repositories used within that request.
5. WHEN the Monitoring_Service performs a state transition across multiple repositories, THE Session_Manager SHALL ensure all writes occur within a single transaction (atomic commit or full rollback).

### Requirement 6: Structured Logging

**User Story:** As a developer, I want structured logging throughout the application, so that debugging issues in production (on the Raspberry Pi) is efficient and traceable.

#### Acceptance Criteria

1. THE Logger SHALL use Python standard `logging` module with a consistent format including timestamp, level, logger name, and message.
2. WHEN a monitoring state transition occurs, THE Logger SHALL log the transition with monitoring_id, previous state, new state, and timestamp.
3. WHEN an error occurs during request processing, THE Logger SHALL log the error with contextual identifiers (monitoring_id, snapshot_id, module_id, or greenhouse_id as applicable).
4. THE Logger SHALL NOT include raw SQL statements, full filesystem paths containing system directory information, or any secrets in log output.
5. WHEN a monitoring worker starts or stops, THE Logger SHALL log the event with monitoring_id and the reason for stopping (completion, abort, or error).
6. THE Logger SHALL use appropriate log levels: DEBUG for detailed flow, INFO for state transitions and key events, WARNING for recoverable issues, ERROR for failures.

### Requirement 7: Jinja2 Template Security

**User Story:** As a developer, I want Jinja2 templates protected against XSS injection, so that user-provided data rendered in HTML cannot execute arbitrary scripts.

#### Acceptance Criteria

1. THE Route_Handler SHALL configure Jinja2 with autoescape enabled for all HTML templates.
2. WHEN user-provided data (greenhouse names, module names, notes) is rendered in templates, THE Route_Handler SHALL rely on Jinja2 autoescaping to neutralize HTML special characters.
3. IF a template must render raw HTML, THE Route_Handler SHALL use the `|safe` filter only for content that is generated by the application itself, never for user-provided strings.

### Requirement 8: Code Quality Standards

**User Story:** As a developer, I want consistent code quality across all modules, so that the codebase remains maintainable, readable, and appropriate for a thesis project.

#### Acceptance Criteria

1. THE Route_Handler SHALL follow a consistent error handling pattern: validate input, call service, handle specific exceptions, return response.
2. THE Repository SHALL have type hints on all public method signatures.
3. THE Repository SHALL have docstrings on all public classes and public methods describing purpose and parameters.
4. THE Route_Handler SHALL NOT contain magic numbers; configurable values SHALL be defined in a configuration module or as named constants.
5. THE Route_Handler SHALL NOT contain unused imports or dead code branches.

### Requirement 9: Test Coverage for Repositories

**User Story:** As a developer, I want pytest tests covering repository CRUD operations, so that data layer correctness is verified without requiring hardware.

#### Acceptance Criteria

1. THE Test_Suite SHALL include tests for Greenhouse repository: create, read by id, read all, update, delete with cascade.
2. THE Test_Suite SHALL include tests for Module repository: create under greenhouse, read by greenhouse, unique constraint on (greenhouse_id, name), delete with cascade.
3. THE Test_Suite SHALL include tests for Monitoring repository: create under module, read by module, status update, delete with cascade.
4. THE Test_Suite SHALL include tests for error cases: creating with non-existent parent IDs, reading non-existent records, duplicate name violations.
5. THE Test_Suite SHALL use an in-memory SQLite database for isolation and speed.

### Requirement 10: Test Coverage for Monitoring Service

**User Story:** As a developer, I want tests covering the monitoring service state transitions, so that edge cases and invalid transitions are caught automatically.

#### Acceptance Criteria

1. THE Test_Suite SHALL include tests for the happy-path state machine: initializing → running → finishing → completed.
2. THE Test_Suite SHALL include tests for pause and resume: running → paused → running.
3. THE Test_Suite SHALL include tests for abort: running → aborted.
4. THE Test_Suite SHALL include tests for invalid transitions (e.g., completed → running) and verify they raise InvalidTransitionError.
5. THE Test_Suite SHALL include tests for the ActiveSessionError when attempting to start a monitoring on a module with an existing active session.

### Requirement 11: Import Validation Test

**User Story:** As a developer, I want a test that verifies all application modules are importable without hardware dependencies, so that development and CI can run on machines without cameras or GPUs.

#### Acceptance Criteria

1. THE Test_Suite SHALL include a test that imports all modules under `app/` and `src/` without raising ImportError.
2. THE Test_Suite SHALL execute without requiring a connected camera, GPU, or pre-loaded model files.
3. IF a module requires optional hardware at import time, THE Test_Suite SHALL skip that module with a clear reason.

### Requirement 12: Configuration Centralization

**User Story:** As a developer, I want all configurable values centralized, so that no magic numbers or hardcoded paths exist in route handlers or service code.

#### Acceptance Criteria

1. THE Route_Handler SHALL obtain validation limits (max string lengths, numeric bounds) from a configuration module or named constants, not inline literals.
2. THE Route_Handler SHALL obtain file path base directories from the configuration module in `src/infrastructure/config/`, not from hardcoded strings.
3. WHEN a new configurable parameter is needed, THE Route_Handler SHALL define it in the appropriate configuration file with a descriptive name and default value.

### Requirement 13: Non-Functional Constraints

**User Story:** As a developer, I want quality and security improvements that do not break existing functionality or introduce unnecessary complexity.

#### Acceptance Criteria

1. THE Test_Suite SHALL pass after all changes are applied, confirming no regressions.
2. THE Route_Handler and services SHALL remain compatible with Raspberry Pi 5 running CPU-only Python 3.10+.
3. THE Route_Handler SHALL NOT introduce new Python dependencies without documented justification in the commit message or ADR.
4. THE Route_Handler changes SHALL be organized as small, independently reviewable commits.
5. THE Logger and Validator SHALL NOT add measurable latency to request processing (overhead below 1ms per request on target hardware).
