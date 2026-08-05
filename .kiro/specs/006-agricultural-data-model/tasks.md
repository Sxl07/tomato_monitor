# Implementation Plan: Agricultural Data Model

## Overview

Implement the SQLite persistence layer with SQLAlchemy ORM following Clean Architecture. The implementation builds domain entities and value objects first (no external dependencies), then infrastructure models and database configuration, then concrete repositories, and finally wires everything into the existing FastAPI application. Property-based tests with Hypothesis validate correctness properties from the design.

## Tasks

- [x] 1. Set up domain layer foundation
  - [x] 1.1 Create domain exceptions module
    - Create `src/domain/exceptions.py` with the exception hierarchy: `DomainError`, `InvalidTransitionError`, `DuplicateModuleError`, `ParentNotFoundError`, `InvalidImagePathError`, `MetricsNotAllowedError`
    - Each exception must include descriptive attributes as defined in the design (current_state, target_state, allowed_transitions for InvalidTransitionError, etc.)
    - _Requirements: 1.3, 5.2_

  - [x] 1.2 Create BoundingBox value object
    - Create `src/domain/value_objects/__init__.py` and `src/domain/value_objects/bounding_box.py`
    - Implement as a frozen dataclass with fields: x1, y1, x2, y2 (all int)
    - Ensure immutability — assignment after construction must raise an exception
    - _Requirements: 1.2_

  - [x] 1.3 Create MonitoringStatus value object with state machine
    - Create `src/domain/value_objects/monitoring_status.py`
    - Implement `MonitoringState` enum (str, Enum) with 7 states: initializing, running, paused, finishing, completed, aborted, error
    - Implement `MonitoringStatus` class with `VALID_TRANSITIONS` dict, `TERMINAL_STATES` set, `transition_to(target)` method, `is_terminal()` method, and `allowed_transitions()` method
    - `transition_to()` must raise `InvalidTransitionError` for invalid transitions including transitions from terminal states
    - Any non-terminal state can transition to `error`
    - _Requirements: 1.2, 1.3, 5.1, 5.2, 5.3_

  - [ ]* 1.4 Write property test for state machine transitions
    - **Property 1: State machine transition correctness**
    - Use Hypothesis to generate all (current_state, target_state) pairs
    - Verify that `transition_to` succeeds iff the pair is in VALID_TRANSITIONS, and raises InvalidTransitionError otherwise
    - Verify that all transitions from terminal states are rejected
    - **Validates: Requirements 1.2, 1.3, 5.1, 5.2, 5.3**

  - [x] 1.5 Create domain entity dataclasses
    - Create `src/domain/entities/__init__.py` and individual files: `greenhouse.py`, `module.py`, `monitoring.py`, `snapshot.py`, `inspection_result.py`, `monitoring_metrics.py`
    - Each entity is a Python dataclass with type hints matching `data-model.md` (types, nullability)
    - Use only stdlib types: str, int, float, bool, datetime, Optional, List
    - No SQLAlchemy, FastAPI, PyTorch, or OpenCV imports in any file under `src/domain/`
    - _Requirements: 1.1, 1.4_

  - [x] 1.6 Create repository interfaces (ABCs)
    - Create `src/domain/repositories/__init__.py` and individual files: `greenhouse_repository.py`, `module_repository.py`, `monitoring_repository.py`, `snapshot_repository.py`, `inspection_result_repository.py`, `monitoring_metrics_repository.py`
    - Each repository is an ABC with methods as specified in Requirements 2.1–2.6
    - Only import from stdlib and domain entities/value objects — no external dependencies
    - Document return-None contract for get_by_id and cascade-delete contract in docstrings
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9_

- [x] 2. Checkpoint - Verify domain layer
  - Ensure all domain modules import cleanly with no external dependencies
  - Run: `python -c "from src.domain.entities import *; from src.domain.value_objects import *; from src.domain.repositories import *"`
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Set up infrastructure persistence layer
  - [x] 3.1 Create SQLAlchemy declarative base
    - Create `src/infrastructure/persistence/__init__.py`, `src/infrastructure/persistence/models/__init__.py`, and `src/infrastructure/persistence/models/base.py`
    - Define the `DeclarativeBase` for all ORM models
    - _Requirements: 3.1_

  - [x] 3.2 Create SQLAlchemy ORM models
    - Create model files: `greenhouse_model.py`, `module_model.py`, `monitoring_model.py`, `snapshot_model.py`, `inspection_result_model.py`, `monitoring_metrics_model.py` in `src/infrastructure/persistence/models/`
    - Map columns, types, and constraints exactly as specified in the design Data Models section
    - Apply UniqueConstraint on (greenhouse_id, name) for ModuleModel
    - Apply unique=True on monitoring_id for MonitoringMetricsModel
    - Configure relationships with `cascade="all, delete-orphan"` for all parent-child relationships
    - Store all DateTime fields as timezone-naive UTC
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 3.3 Create database connection module
    - Create `src/infrastructure/persistence/database.py` with `DatabaseManager` class
    - Configure engine pointing to `data/tomato_monitor.db` (configurable via parameter)
    - Apply PRAGMAs on every connection: `PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL` using SQLAlchemy event listener
    - Create SessionLocal factory with `autocommit=False`, `autoflush=False`
    - Implement `init_db()` that calls `create_all(checkfirst=True)`
    - Create `data/` directory with `os.makedirs(exist_ok=True)` before engine initialization
    - Raise `DatabaseInitError` on permission/disk failures
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 8.2_

  - [x] 3.4 Create infrastructure exceptions module
    - Create `src/infrastructure/persistence/exceptions.py` with `DatabaseInitError`
    - _Requirements: 6.6, 7.4_

- [x] 4. Implement concrete repositories
  - [x] 4.1 Implement SqlGreenhouseRepository
    - Create `src/infrastructure/persistence/repositories/__init__.py` and `sql_greenhouse_repository.py`
    - Implement all methods from GreenhouseRepository interface: create, get_all, get_by_id, update, delete
    - Include `_to_entity()` and `_to_model()` conversion methods
    - Cascade delete removes all descendants
    - Return None for non-existent IDs
    - _Requirements: 2.1, 4.1, 4.3, 4.8, 8.1, 8.4_

  - [x] 4.2 Implement SqlModuleRepository
    - Create `sql_module_repository.py` in `src/infrastructure/persistence/repositories/`
    - Implement all methods from ModuleRepository interface: create, get_by_greenhouse, get_by_id, update, delete
    - Catch IntegrityError on duplicate (greenhouse_id, name) and raise DuplicateModuleError
    - Order child queries by created_at descending
    - _Requirements: 2.2, 4.1, 4.4, 4.6, 4.8, 4.9_

  - [x] 4.3 Implement SqlMonitoringRepository
    - Create `sql_monitoring_repository.py` in `src/infrastructure/persistence/repositories/`
    - Implement all methods from MonitoringRepository interface: create, get_by_module, get_by_id, update_status, update_counters, delete
    - On create: assign status="initializing" and started_at=UTC now
    - On update_status: validate transition using MonitoringStatus state machine; set completed_at on terminal transitions
    - Order child queries by started_at descending
    - _Requirements: 2.3, 4.1, 4.5, 4.7, 4.8, 4.9, 5.4_

  - [x] 4.4 Implement SqlSnapshotRepository
    - Create `sql_snapshot_repository.py` in `src/infrastructure/persistence/repositories/`
    - Implement all methods from SnapshotRepository interface: create, get_by_monitoring, get_by_id
    - Validate image_path before INSERT: reject paths > 500 chars, paths containing `../`, or absolute paths
    - Order child queries by captured_at descending
    - _Requirements: 2.4, 4.1, 4.8, 9.1, 9.3, 9.4_

  - [x] 4.5 Implement SqlInspectionResultRepository
    - Create `sql_inspection_result_repository.py` in `src/infrastructure/persistence/repositories/`
    - Implement all methods from InspectionResultRepository interface: create, get_by_snapshot, get_by_monitoring
    - _Requirements: 2.5, 4.1, 4.8_

  - [x] 4.6 Implement SqlMonitoringMetricsRepository
    - Create `sql_monitoring_metrics_repository.py` in `src/infrastructure/persistence/repositories/`
    - Implement all methods from MonitoringMetricsRepository interface: create, get_by_monitoring
    - On create: check monitoring status is in {completed, aborted}; raise MetricsNotAllowedError otherwise
    - Respect UNIQUE constraint on monitoring_id (one metrics per monitoring)
    - _Requirements: 2.6, 4.1, 4.8, 5.5, 5.6_

- [x] 5. Checkpoint - Verify persistence layer
  - Ensure all infrastructure modules import cleanly
  - Run: `python -c "from src.infrastructure.persistence.database import DatabaseManager; from src.infrastructure.persistence.repositories import *"`
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Write unit tests for domain logic
  - [x] 6.1 Write unit tests for MonitoringStatus state machine
    - Test all valid transitions succeed
    - Test all invalid transitions raise InvalidTransitionError with correct attributes
    - Test terminal states reject all transitions
    - Test error transition from all non-terminal states
    - Place tests in `tests/domain/test_monitoring_status.py`
    - _Requirements: 1.2, 1.3, 5.1, 5.2, 5.3_

  - [x] 6.2 Write unit tests for BoundingBox immutability
    - Test that attribute assignment after construction raises FrozenInstanceError
    - Test construction with valid values succeeds
    - Place tests in `tests/domain/test_value_objects.py`
    - _Requirements: 1.2_

  - [x] 6.3 Write unit tests for image path validation
    - Test valid relative paths are accepted
    - Test paths > 500 chars are rejected
    - Test paths containing `../` are rejected
    - Test absolute paths (starting with `/` or drive letter) are rejected
    - Place tests in `tests/infrastructure/persistence/test_path_validation.py`
    - _Requirements: 9.1, 9.4_

- [ ] 7. Write property-based tests for persistence correctness
  - [ ]* 7.1 Write property test for cascade delete
    - **Property 2: Cascade delete removes all descendants**
    - Use Hypothesis to generate hierarchies of varying depth and breadth
    - Delete the top-level Greenhouse and verify zero records remain in all child tables
    - Use in-memory SQLite for speed
    - Place in `tests/infrastructure/persistence/test_properties.py`
    - **Validates: Requirements 2.9, 3.5, 3.8, 4.3, 4.4, 4.5, 8.4**

  - [ ]* 7.2 Write property test for unique constraint on modules
    - **Property 3: Unique constraint on (greenhouse_id, name)**
    - Use Hypothesis to generate module names; attempt duplicate creation and verify IntegrityError
    - **Validates: Requirements 3.3, 3.7, 4.6**

  - [ ]* 7.3 Write property test for timezone-naive timestamps
    - **Property 4: All timestamps stored as timezone-naive UTC**
    - Create entities through repositories and verify all DateTime fields have tzinfo=None
    - **Validates: Requirements 3.6**

  - [ ]* 7.4 Write property test for non-existent ID returns None
    - **Property 5: Non-existent ID returns None**
    - Use Hypothesis to generate random positive integers; verify get_by_id returns None for IDs not in the database
    - **Validates: Requirements 2.8, 4.8**

  - [ ]* 7.5 Write property test for child query ordering
    - **Property 6: Child queries ordered by created_at descending**
    - Create multiple children with distinct timestamps; verify returned list is newest-first
    - **Validates: Requirements 4.9**

  - [ ]* 7.6 Write property test for monitoring creation invariants
    - **Property 7: Monitoring creation assigns initializing status and started_at**
    - Use Hypothesis to generate valid creation inputs; verify status="initializing" and started_at is set
    - **Validates: Requirements 4.7**

  - [ ]* 7.7 Write property test for terminal state sets completed_at
    - **Property 8: Terminal state transition sets completed_at**
    - Transition monitorings to completed/aborted and verify completed_at is non-None; verify non-terminal states have completed_at=None
    - **Validates: Requirements 5.4**

  - [ ]* 7.8 Write property test for metrics creation guard
    - **Property 9: Metrics creation guarded by monitoring status**
    - Attempt metrics creation for monitorings in all statuses; verify only completed/aborted allow it
    - Verify second creation attempt fails due to UNIQUE constraint
    - **Validates: Requirements 5.5, 5.6, 3.4**

  - [ ]* 7.9 Write property test for referential integrity
    - **Property 10: Referential integrity rejects orphan creation**
    - Attempt child creation with non-existent parent IDs; verify IntegrityError is raised
    - **Validates: Requirements 8.3, 8.5**

  - [ ]* 7.10 Write property test for schema idempotency
    - **Property 11: Schema creation is idempotent**
    - Insert records, call init_db() again, verify all records remain unchanged
    - **Validates: Requirements 7.2**

  - [ ]* 7.11 Write property test for image path validation
    - **Property 12: Image path validation rejects invalid paths**
    - Use Hypothesis text strategy to generate strings > 500 chars, strings with `../`, and absolute paths; verify rejection
    - **Validates: Requirements 9.1, 9.4**

  - [ ]* 7.12 Write property test for image path format
    - **Property 13: Image path format matches expected pattern**
    - Use Hypothesis to generate positive integers for monitoring_id and non-negative integers for frame_index; verify path matches `outputs/monitorings/{monitoring_id}/snapshots/snapshot_{frame_index}.jpg`
    - **Validates: Requirements 9.2**

- [x] 8. Checkpoint - Verify all tests pass
  - Run full test suite: `pytest tests/ -v`
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Integration and wiring
  - [x] 9.1 Wire repositories into FastAPI dependencies
    - Update `app/dependencies.py` to instantiate `DatabaseManager` and provide concrete repository instances
    - Call `init_db()` at application startup (in `app/main.py` lifespan or startup event)
    - Ensure session lifecycle is managed per-request
    - _Requirements: 6.1, 7.1, 10.1, 10.3_

  - [x] 9.2 Update .gitignore for database files
    - Add `data/*.db` pattern to `.gitignore`
    - _Requirements: 10.5_

  - [ ]* 9.3 Write integration test for database connection and PRAGMAs
    - Verify that a new connection has `foreign_keys=ON` and `journal_mode=WAL`
    - Use a temporary file-based SQLite database (WAL requires file-based DB)
    - Place in `tests/infrastructure/persistence/test_database_integration.py`
    - _Requirements: 6.2, 8.2_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Run full test suite: `pytest tests/ -v`
  - Verify application starts without errors: `python -c "from app.main import app"`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All persistence tests use in-memory SQLite (`sqlite:///:memory:`) except integration tests requiring WAL mode
- The domain layer has zero external dependencies — only Python stdlib, dataclasses, and enum
- The implementation language is Python (as used throughout the design document)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "3.4"] },
    { "id": 1, "tasks": ["1.3", "1.5"] },
    { "id": 2, "tasks": ["1.4", "1.6", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3"] },
    { "id": 4, "tasks": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"] },
    { "id": 5, "tasks": ["6.1", "6.2", "6.3", "9.2"] },
    { "id": 6, "tasks": ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12"] },
    { "id": 7, "tasks": ["9.1", "9.3"] }
  ]
}
```
