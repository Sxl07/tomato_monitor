"""Application service: DeletionService.

Orchestrates durable deletion of monitorings (and, in later sub-tasks, modules
and greenhouses) following a two-transaction protocol with a local deletion
outbox. This service lives in the application layer and depends ONLY on domain
repository/port abstractions and the standard library. It MUST NOT import
FastAPI, SQLAlchemy, httpx, torch or cv2.

Scope of this file (Spec 021, Tasks 4.1 + 4.2 + 4.3):
    - 4.1 Monitoring deletion AUTHORIZATION by FSM state and existence check.
    - 4.2 No-interference check via ``MonitoringRuntimeRegistry``: reject a
          deletion when an active capture/analysis worker exists for the target
          monitoring, without altering its state or data.
    - 4.3 BUILD (not persist) the deletion payload BEFORE the Local_Cascade:
          capture the root ``remote_id`` + ``remote_table`` from the sync-state
          port, every descendant snapshot's non-null raw/annotated Storage
          paths, and one local-artifact row per descendant monitoring
          (``monitorings/{monitoring_id}`` relative to OUTPUTS_DIR).

Task 4.4 (implemented here) adds the two-transaction protocol that turns the
built payload into a durable deletion:
    - TX1: enqueue the outbox entry with ``local_delete_status='prepared'`` and
          COMMIT (durability before cascade). On failure, abort WITHOUT running
          the cascade, leaving the hierarchy intact (Req 4.7).
    - TX2: run the ``Local_Cascade`` and set ``local_delete_status='completed'``
          + ``deleted_at`` in the SAME transaction. On failure, the entry stays
          non-completed and the deletion is NOT propagated remotely (Req 4.10).
The whole flow is connectivity-independent — no network calls anywhere.

Task 5.1 (implemented here) adds the module/greenhouse descendant
pre-validation that runs BEFORE any outbox entry (TX1) and BEFORE any
``Local_Cascade``: the container deletion is authorized ONLY IF every
descendant monitoring is in a deletable state AND none has an active worker;
otherwise the WHOLE operation is rejected with the hierarchy left intact (no
outbox entry, no cascade).

Task 5.2 (implemented here) wires module/greenhouse deletion into the same
durable two-transaction path as monitoring: ``delete`` runs the Task 5.1
descendant pre-validation for a ``module``/``greenhouse`` root, then builds a
SINGLE root outbox entry (the root's ``remote_id`` + ``remote_table``,
``modules``/``greenhouses``, with descendant snapshot Storage paths aggregated
and one local-artifact row per descendant monitoring -- relying on remote ON
DELETE CASCADE for descendant rows, Req 7.5), runs TX1 (``prepared`` + COMMIT)
and TX2 (``Local_Cascade`` + mark ``completed`` with ``deleted_at``). The
convenience wrappers ``delete_module`` / ``delete_greenhouse`` mirror
``delete_monitoring``. Monitoring behavior (Task 4.4) is unchanged.

Design references: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.3, 3.1, 3.2, 4.2,
4.3, 4.7, 4.8, 4.9, 4.10, 4.11, 4.12, 4.13, 6.3, 6.5, 6.6, 6.7, 6.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    DeletionOutboxEntryInput,
    DeletionOutboxPort,
    OutboxLocalArtifactInput,
    OutboxStoragePathInput,
)
from src.application.interfaces.local_cascade_port import LocalCascadePort
from src.application.interfaces.sync_state_port import SyncStatePort
from src.domain.exceptions import DomainError
from src.domain.repositories.module_repository import ModuleRepository
from src.domain.repositories.monitoring_repository import MonitoringRepository
from src.domain.repositories.snapshot_repository import SnapshotRepository
from src.domain.value_objects.monitoring_status import MonitoringState


class DeletionError(DomainError):
    """Base error for deletion authorization/execution failures."""

    pass


class MonitoringNotFoundError(DeletionError):
    """The target monitoring does not exist (Req 1.4)."""

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(
            f"Monitoreo {monitoring_id} no encontrado."
        )


class MonitoringStateNotDeletableError(DeletionError):
    """The monitoring is in a state that prohibits deletion (Req 1.2).

    The message names the current state and states that this state does not
    allow deletion, so the UI/route can surface an actionable message without
    modifying the monitoring or its associated data.
    """

    def __init__(self, monitoring_id: int, current_state: str) -> None:
        self.monitoring_id = monitoring_id
        self.current_state = current_state
        super().__init__(
            f"El monitoreo {monitoring_id} está en estado '{current_state}', "
            f"que no permite la eliminación."
        )


class MonitoringWorkerActiveError(DeletionError):
    """An active capture/analysis worker exists for the monitoring (Req 3.1).

    The deletion is rejected without altering the monitoring's state or data.
    The message signals that an active worker is present so the route/UI can
    surface an actionable message.
    """

    def __init__(self, monitoring_id: int) -> None:
        self.monitoring_id = monitoring_id
        super().__init__(
            f"El monitoreo {monitoring_id} tiene un worker de captura o análisis "
            f"activo; no se permite la eliminación mientras esté en ejecución."
        )


@runtime_checkable
class MonitoringRuntimeRegistryPort(Protocol):
    """Structural port for the runtime registry used in the no-interference check.

    Only the per-monitoring signals needed by Task 4.2 are declared. The concrete
    ``MonitoringRuntimeRegistry`` (application layer) satisfies this protocol, and
    tests can pass a lightweight fake. This intentionally does NOT declare the
    device-global signals (``has_active_capture``/``is_global_analysis_active``),
    because those over-reject unrelated monitorings and must not be used as the
    per-target signal.
    """

    def get_worker(self, monitoring_id: int) -> object | None:
        ...

    def get_thread(self, monitoring_id: int):  # -> threading.Thread | None
        ...

    def is_finalization_claimed(self, monitoring_id: int) -> bool:
        ...

    def is_analysis_claimed(self, monitoring_id: int) -> bool:
        ...


# Classification of monitoring states for deletion authorization (Req 1.1, 1.2).
# Deletable states are safe to delete because no capture/analysis phase is in
# progress from an FSM standpoint. Prohibited states represent active or
# in-transition sessions that must not be interrupted.
DELETABLE_STATES: frozenset[MonitoringState] = frozenset(
    {
        MonitoringState.READY_FOR_ANALYSIS,
        MonitoringState.COMPLETED,
        MonitoringState.ERROR,
        MonitoringState.ABORTED,
    }
)

PROHIBITED_STATES: frozenset[MonitoringState] = frozenset(
    {
        MonitoringState.INITIALIZING,
        MonitoringState.RUNNING,
        MonitoringState.PAUSED,
        MonitoringState.FINISHING,
        MonitoringState.ANALYZING,
    }
)


# Root entity_type -> remote table used for the single root DELETE (Req 4.13,
# 6.2, 7.5). The remote backend relies on ON DELETE CASCADE to remove descendant
# rows, so only the root table is recorded in the outbox entry.
ENTITY_TYPE_TO_REMOTE_TABLE: dict[str, str] = {
    "greenhouse": "greenhouses",
    "module": "modules",
    "monitoring": "monitorings",
}


class DeletionPayloadUnsupportedEntityError(DeletionError):
    """The requested entity_type is not one of greenhouse/module/monitoring."""

    def __init__(self, entity_type: str) -> None:
        self.entity_type = entity_type
        super().__init__(
            f"Tipo de entidad '{entity_type}' no soportado para eliminación; "
            f"debe ser uno de 'greenhouse', 'module' o 'monitoring'."
        )


class DeletionPayloadPortsUnavailableError(DeletionError):
    """The ports required to build a deletion payload were not injected.

    Task 4.3 needs the sync-state port and the domain repositories to capture
    remote ids, Storage paths and local artifacts. When they were not provided
    at construction time (legacy call sites), payload building is unavailable.
    """

    def __init__(self) -> None:
        super().__init__(
            "El servicio de eliminación no fue configurado con los puertos "
            "necesarios para construir el payload de eliminación "
            "(sync-state y repositorios de dominio)."
        )


class DeletionOutboxRegistrationError(DeletionError):
    """TX1 failed: the outbox entry could not be durably registered (Req 4.7).

    Raised when ``DeletionOutboxPort.enqueue`` fails. In this case the
    ``Local_Cascade`` is never executed and the local records are left intact,
    so the caller can surface an actionable message and retry later.
    """

    def __init__(self, entity_type: str, entity_local_id: int) -> None:
        self.entity_type = entity_type
        self.entity_local_id = entity_local_id
        super().__init__(
            f"No se pudo registrar de forma duradera la eliminación de "
            f"'{entity_type}' {entity_local_id}; la operación se abortó y los "
            f"datos locales se conservaron sin modificación."
        )


class LocalCascadeFailedError(DeletionError):
    """TX2 failed: the Local_Cascade rolled back (Req 4.10, 5.5).

    The hierarchy is left intact, the outbox entry is not marked ``completed``
    (it stays ``prepared``/``failed``), and the deletion is NOT propagated
    remotely. Carries the id of the entity that could not be deleted.
    """

    def __init__(
        self,
        entity_type: str,
        entity_local_id: int,
        error_message: str | None = None,
    ) -> None:
        self.entity_type = entity_type
        self.entity_local_id = entity_local_id
        self.error_message = error_message
        detail = f" Detalle: {error_message}" if error_message else ""
        super().__init__(
            f"No se pudo eliminar localmente '{entity_type}' {entity_local_id}; "
            f"la jerarquía se conservó intacta y la eliminación no se propagará "
            f"al backend remoto.{detail}"
        )


class ContainerDescendantNotDeletableError(DeletionError):
    """A descendant monitoring is in a state that prohibits container deletion.

    Raised by the module/greenhouse descendant pre-validation (Task 5.1, Req
    6.5, 6.7) when ANY descendant monitoring is in a prohibited FSM state. The
    WHOLE operation is rejected: no outbox entry is created and no
    ``Local_Cascade`` runs (the hierarchy stays intact). The message identifies
    the offending descendant and its state so the route/UI can surface an
    actionable message.
    """

    def __init__(
        self,
        entity_type: str,
        entity_local_id: int,
        monitoring_id: int,
        current_state: str,
    ) -> None:
        self.entity_type = entity_type
        self.entity_local_id = entity_local_id
        self.monitoring_id = monitoring_id
        self.current_state = current_state
        super().__init__(
            f"No se puede eliminar '{entity_type}' {entity_local_id}: el "
            f"monitoreo descendiente {monitoring_id} está en estado "
            f"'{current_state}', que no permite la eliminación. La operación se "
            f"rechazó por completo y la jerarquía se conservó intacta."
        )


class ContainerDescendantWorkerActiveError(DeletionError):
    """A descendant monitoring has an active capture/analysis worker.

    Raised by the module/greenhouse descendant pre-validation (Task 5.1, Req
    6.6, 6.8) when ANY descendant monitoring has an active worker in the runtime
    registry. The WHOLE operation is rejected: no outbox entry is created and no
    ``Local_Cascade`` runs (the hierarchy stays intact). The message identifies
    the offending descendant so the route/UI can surface an actionable message.
    """

    def __init__(
        self,
        entity_type: str,
        entity_local_id: int,
        monitoring_id: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_local_id = entity_local_id
        self.monitoring_id = monitoring_id
        super().__init__(
            f"No se puede eliminar '{entity_type}' {entity_local_id}: el "
            f"monitoreo descendiente {monitoring_id} tiene un worker de captura "
            f"o análisis activo. La operación se rechazó por completo y la "
            f"jerarquía se conservó intacta."
        )


class ContainerDescendantValidationPortsUnavailableError(DeletionError):
    """The repositories required to traverse container descendants are missing.

    The module descendant pre-validation (Task 5.1) needs the monitoring
    repository; the greenhouse pre-validation additionally needs the module
    repository. When they were not injected at construction time, descendant
    validation is unavailable.
    """

    def __init__(self) -> None:
        super().__init__(
            "El servicio de eliminación no fue configurado con los "
            "repositorios necesarios para validar los monitoreos descendientes "
            "de un módulo o invernadero."
        )


class DeletionOrchestrationPortsUnavailableError(DeletionError):
    """The ports required by the two-transaction delete flow were not injected.

    ``delete``/``delete_monitoring`` need both the deletion outbox port (TX1)
    and the local cascade port (TX2) in addition to the payload-building ports.
    Guard-only call sites (Tasks 4.1/4.2) remain constructible without them.
    """

    def __init__(self) -> None:
        super().__init__(
            "El servicio de eliminación no fue configurado con los puertos "
            "necesarios para ejecutar el protocolo de dos transacciones "
            "(outbox de eliminación y cascade local)."
        )


class AuthorizationOutcome(str, Enum):
    """Result category of a deletion authorization check."""

    ALLOWED = "allowed"
    NOT_FOUND = "not_found"
    STATE_PROHIBITED = "state_prohibited"
    WORKER_ACTIVE = "worker_active"


class DescendantValidationOutcome(str, Enum):
    """Result category of a module/greenhouse descendant pre-validation (Task 5.1)."""

    ALLOWED = "allowed"
    DESCENDANT_STATE_PROHIBITED = "descendant_state_prohibited"
    DESCENDANT_WORKER_ACTIVE = "descendant_worker_active"


@dataclass(frozen=True)
class AuthorizationResult:
    """Lightweight, composable result of a deletion authorization check.

    This result type lets callers (and later sub-tasks 4.2–4.4) branch on the
    outcome without relying on exceptions, while `authorize_or_raise` provides
    the raise-based flow consistent with the rest of the application layer.
    """

    outcome: AuthorizationOutcome
    monitoring_id: int
    current_state: MonitoringState | None = None
    message: str | None = None

    @property
    def allowed(self) -> bool:
        """True when the deletion is authorized to proceed."""
        return self.outcome is AuthorizationOutcome.ALLOWED


@dataclass(frozen=True)
class DescendantValidationResult:
    """Composable result of a module/greenhouse descendant pre-validation (Task 5.1).

    Reports whether ALL descendant monitorings of a container root are safe to
    delete (every one in a deletable FSM state AND worker-free), or, on the
    first offending descendant, which monitoring blocked the operation and why.

    This mirrors ``AuthorizationResult`` but is scoped to the container's
    descendants so the module/greenhouse delete flow (Task 5.2) can branch on
    the outcome (or raise via ``validate_descendants_or_raise``) BEFORE TX1.

    Attributes:
        outcome: ALLOWED when every descendant is deletable and worker-free;
            otherwise the reason of the first offending descendant.
        entity_type: The container root type (``module`` or ``greenhouse``).
        entity_local_id: Local id of the container root.
        offending_monitoring_id: Id of the first descendant that blocked the
            operation, or ``None`` when ALLOWED.
        offending_state: FSM state of the offending descendant when the block
            was a prohibited state, else ``None``.
        message: Human-readable reason (Spanish), or ``None`` when ALLOWED.
        checked_monitoring_ids: Descendant monitoring ids that were traversed.
    """

    outcome: DescendantValidationOutcome
    entity_type: str
    entity_local_id: int
    offending_monitoring_id: int | None = None
    offending_state: MonitoringState | None = None
    message: str | None = None
    checked_monitoring_ids: tuple[int, ...] = ()

    @property
    def allowed(self) -> bool:
        """True when every descendant is deletable and worker-free."""
        return self.outcome is DescendantValidationOutcome.ALLOWED


@dataclass(frozen=True)
class DeletionResult:
    """Outcome of a successful two-transaction deletion (Task 4.4).

    Returned by ``delete``/``delete_monitoring`` when the local deletion is
    durable and complete. Failures are signalled via exceptions
    (``DeletionOutboxRegistrationError``, ``LocalCascadeFailedError``, or the
    authorization errors), so a returned ``DeletionResult`` always represents a
    committed local deletion.

    Attributes:
        entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
        entity_local_id: Local id of the deleted root entity.
        outbox_id: Id of the durable outbox entry driving remote propagation.
        deleted_at: UTC timestamp of the successful local deletion (TX2).
        reused_outbox_entry: True when an existing non-completed outbox entry
            was reused (idempotent retry), rather than a fresh one created.
    """

    entity_type: str
    entity_local_id: int
    outbox_id: int
    deleted_at: datetime
    reused_outbox_entry: bool = False


def _classify_state(raw_state: str) -> MonitoringState | None:
    """Map a persisted status string to a MonitoringState, or None if unknown."""
    try:
        return MonitoringState(raw_state)
    except ValueError:
        return None


class DeletionService:
    """Authorize and (in later sub-tasks) execute durable entity deletion.

    Constructor collaborators are injected as abstractions so later sub-tasks
    can add ports (runtime registry, deletion outbox, local cascade, sync-state)
    without importing infrastructure here. Task 4.1 only requires a way to look
    up a monitoring's current status, provided by ``MonitoringRepository``.
    """

    def __init__(
        self,
        monitoring_repository: MonitoringRepository,
        runtime_registry: MonitoringRuntimeRegistryPort | None = None,
        sync_state: SyncStatePort | None = None,
        snapshot_repository: SnapshotRepository | None = None,
        module_repository: ModuleRepository | None = None,
        deletion_outbox: DeletionOutboxPort | None = None,
        local_cascade: LocalCascadePort | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            monitoring_repository: Port used to look up monitorings by id.
                Its ``get_by_id`` returns ``None`` when the id does not exist.
            runtime_registry: Optional runtime registry used for the
                no-interference check (Task 4.2). When ``None``, the
                no-interference check treats the target as having no active
                worker (registry-less contexts, e.g. some unit tests). Injected
                as a structural port so infrastructure is never imported here.
            sync_state: Optional sync-state port (Task 4.3) used to capture the
                root ``remote_id`` and each descendant snapshot's remote Storage
                paths. Optional to preserve existing call sites; payload
                building requires it together with the domain repositories.
            snapshot_repository: Optional snapshot repository (Task 4.3) used to
                traverse the snapshots of each descendant monitoring.
            module_repository: Optional module repository (Task 4.3) used to
                traverse modules of a greenhouse root.
            deletion_outbox: Optional deletion-outbox port (Task 4.4). Required
                by the two-transaction delete flow to enqueue the outbox entry
                (TX1) and to mark it failed if TX2 rolls back. Optional so
                guard-only call sites (Tasks 4.1/4.2) stay constructible.
            local_cascade: Optional local-cascade port (Task 4.4). Required by
                the two-transaction delete flow to run the cascade and mark the
                entry completed (TX2). Optional for the same reason as above.

        Note:
            The Task 4.3 collaborators (``sync_state``, ``snapshot_repository``,
            ``module_repository``) and the Task 4.4 ports (``deletion_outbox``,
            ``local_cascade``) default to ``None`` so pre-existing constructor
            call sites (Tasks 4.1/4.2) keep working. The authorization/guard
            methods never touch them; ``build_deletion_payload`` requires the
            4.3 ports, and ``delete``/``delete_monitoring`` require the 4.4 ports
            (a ``DeletionOrchestrationPortsUnavailableError`` is raised if any is
            missing).
        """
        self._monitorings = monitoring_repository
        self._runtime_registry = runtime_registry
        self._sync_state = sync_state
        self._snapshots = snapshot_repository
        self._modules = module_repository
        self._deletion_outbox = deletion_outbox
        self._local_cascade = local_cascade

    def authorize_monitoring_deletion(self, monitoring_id: int) -> AuthorizationResult:
        """Authorize deletion of a monitoring by existence and FSM state.

        Rules (Req 1.1, 1.2, 1.4):
            - If the monitoring does not exist -> NOT_FOUND.
            - If its state is prohibited (initializing, running, paused,
              finishing, analyzing) -> STATE_PROHIBITED (do not modify data).
            - If its state is deletable (ready_for_analysis, completed, error,
              aborted) -> ALLOWED.

        Args:
            monitoring_id: Local id of the monitoring to delete.

        Returns:
            An ``AuthorizationResult`` describing the outcome. This method never
            mutates state; it only reads the persisted status.
        """
        monitoring = self._monitorings.get_by_id(monitoring_id)
        if monitoring is None:
            return AuthorizationResult(
                outcome=AuthorizationOutcome.NOT_FOUND,
                monitoring_id=monitoring_id,
                message=f"Monitoreo {monitoring_id} no encontrado.",
            )

        state = _classify_state(monitoring.status)
        # An unknown/unmapped status is treated as non-deletable to avoid
        # destructive actions on an inconsistent record.
        if state is None or state in PROHIBITED_STATES:
            current = monitoring.status
            return AuthorizationResult(
                outcome=AuthorizationOutcome.STATE_PROHIBITED,
                monitoring_id=monitoring_id,
                current_state=state,
                message=(
                    f"El monitoreo {monitoring_id} está en estado '{current}', "
                    f"que no permite la eliminación."
                ),
            )

        return AuthorizationResult(
            outcome=AuthorizationOutcome.ALLOWED,
            monitoring_id=monitoring_id,
            current_state=state,
        )

    def has_active_worker(self, monitoring_id: int) -> bool:
        """Return True if the TARGET monitoring has an active capture/analysis worker.

        No-interference signal for Task 4.2 (Req 3.1). This method NEVER mutates
        state — it only reads per-monitoring signals from the runtime registry.

        A monitoring is considered to have an active worker when ANY of the
        following per-monitoring signals hold:
            - a registered thread is alive (``get_thread(mid).is_alive()``), or
            - a worker object is registered (``get_worker(mid) is not None``), or
            - a finalization claim is held for it, or
            - a deferred-analysis claim is held for it.

        Device-global signals (``has_active_capture``/``is_global_analysis_active``)
        are deliberately NOT consulted: they are device-wide and would over-reject
        deletions of unrelated monitorings. When no registry is injected, there is
        no runtime information and the target is treated as having no active worker.

        Args:
            monitoring_id: Local id of the target monitoring.

        Returns:
            True when an active worker exists for THIS monitoring, else False.
        """
        registry = self._runtime_registry
        if registry is None:
            return False

        thread = registry.get_thread(monitoring_id)
        if thread is not None:
            # Guard against fakes/threads that may not expose is_alive.
            is_alive = getattr(thread, "is_alive", None)
            if is_alive is None or is_alive():
                return True

        if registry.get_worker(monitoring_id) is not None:
            return True

        if registry.is_finalization_claimed(monitoring_id):
            return True

        if registry.is_analysis_claimed(monitoring_id):
            return True

        return False

    def check_no_active_worker(self, monitoring_id: int) -> AuthorizationResult:
        """No-interference check for a monitoring (Req 3.1, 3.2).

        Returns a composable ``AuthorizationResult``. This never inspects other
        monitorings and never mutates state, so deleting a monitoring with no
        active worker cannot stop/pause/alter workers of OTHER monitorings
        (Req 3.2 is satisfied by construction — no worker of any monitoring is
        touched here).

        Args:
            monitoring_id: Local id of the target monitoring.

        Returns:
            ``WORKER_ACTIVE`` outcome when a worker is active for this monitoring,
            otherwise ``ALLOWED``.
        """
        if self.has_active_worker(monitoring_id):
            return AuthorizationResult(
                outcome=AuthorizationOutcome.WORKER_ACTIVE,
                monitoring_id=monitoring_id,
                message=(
                    f"El monitoreo {monitoring_id} tiene un worker de captura o "
                    f"análisis activo; no se permite la eliminación mientras esté "
                    f"en ejecución."
                ),
            )
        return AuthorizationResult(
            outcome=AuthorizationOutcome.ALLOWED,
            monitoring_id=monitoring_id,
        )

    def guard_monitoring_deletion(self, monitoring_id: int) -> AuthorizationResult:
        """Composed guard: FSM-state authorization (4.1) AND no-interference (4.2).

        This is the entry point the future delete flow (Task 4.4) calls before
        creating any outbox entry or running any ``Local_Cascade``. It performs,
        in order and without mutating state:
            1. existence + FSM-state authorization (Task 4.1), then
            2. the no-interference check against the runtime registry (Task 4.2).

        Args:
            monitoring_id: Local id of the target monitoring.

        Returns:
            The first non-ALLOWED ``AuthorizationResult`` encountered, or an
            ALLOWED result carrying the deletable ``MonitoringState``.
        """
        state_result = self.authorize_monitoring_deletion(monitoring_id)
        if not state_result.allowed:
            return state_result

        worker_result = self.check_no_active_worker(monitoring_id)
        if not worker_result.allowed:
            # Preserve the known current_state so callers can report it.
            return AuthorizationResult(
                outcome=worker_result.outcome,
                monitoring_id=monitoring_id,
                current_state=state_result.current_state,
                message=worker_result.message,
            )

        return state_result

    def authorize_monitoring_deletion_or_raise(self, monitoring_id: int) -> MonitoringState:
        """Raise-based guard used by the future delete flow.

        Consistent with the rest of the application layer (see
        ``monitoring_service``), this raises domain-style errors so that the
        two-transaction delete flow (Task 4.4) can guard-clause early.

        Args:
            monitoring_id: Local id of the monitoring to delete.

        Returns:
            The deletable ``MonitoringState`` when authorization succeeds.

        Raises:
            MonitoringNotFoundError: If the monitoring id does not exist.
            MonitoringStateNotDeletableError: If the current state is prohibited.
            MonitoringWorkerActiveError: If an active capture/analysis worker
                exists for this monitoring (Task 4.2, Req 3.1).
        """
        result = self.guard_monitoring_deletion(monitoring_id)
        if result.outcome is AuthorizationOutcome.NOT_FOUND:
            raise MonitoringNotFoundError(monitoring_id)
        if result.outcome is AuthorizationOutcome.STATE_PROHIBITED:
            current = (
                result.current_state.value
                if result.current_state is not None
                else "desconocido"
            )
            raise MonitoringStateNotDeletableError(monitoring_id, current)
        if result.outcome is AuthorizationOutcome.WORKER_ACTIVE:
            raise MonitoringWorkerActiveError(monitoring_id)
        assert result.current_state is not None  # ALLOWED implies a known state
        return result.current_state

    # ------------------------------------------------------------------ #
    # Task 5.1 -- module/greenhouse descendant pre-validation BEFORE TX1. #
    #   Reject the WHOLE container deletion when ANY descendant monitoring #
    #   is in a prohibited state OR has an active worker. Read-only: no    #
    #   outbox entry, no Local_Cascade, hierarchy left intact.            #
    # ------------------------------------------------------------------ #

    def validate_descendants(
        self,
        entity_type: str,
        entity_local_id: int,
    ) -> DescendantValidationResult:
        """Pre-validate ALL descendant monitorings of a module/greenhouse (Task 5.1).

        Runs BEFORE any outbox entry (TX1) and BEFORE any ``Local_Cascade``.
        Traverses every descendant monitoring of the container root (reusing the
        read-only traversal from Task 4.3: ``MonitoringRepository.get_by_module``
        and, for a greenhouse, ``ModuleRepository.get_by_greenhouse`` then
        per-module monitorings) and authorizes the operation ONLY IF (Req 6.5,
        6.6):

            - EVERY descendant monitoring is in a deletable FSM state
              ``{ready_for_analysis, completed, error, aborted}`` (Req 6.6), AND
            - NO descendant monitoring has an active capture/analysis worker in
              the runtime registry (reusing :meth:`has_active_worker`).

        On the FIRST offending descendant the WHOLE operation is rejected (Req
        6.7, 6.8): the returned result identifies the offending monitoring and
        the reason, and the caller MUST NOT create any outbox entry or run any
        cascade. An empty container (no descendants) is ALLOWED.

        This method is READ-ONLY and NEVER mutates state (it only reads the
        persisted status via the repositories and per-monitoring runtime
        signals). It stays composable with the single-monitoring guard.

        Args:
            entity_type: The container root type; must be ``module`` or
                ``greenhouse``.
            entity_local_id: Local id of the container root.

        Returns:
            A ``DescendantValidationResult``: ALLOWED when every descendant is
            deletable and worker-free, otherwise the reason and offending
            monitoring of the first descendant that blocked the operation.

        Raises:
            DeletionPayloadUnsupportedEntityError: If ``entity_type`` is not
                ``module`` or ``greenhouse``.
            ContainerDescendantValidationPortsUnavailableError: If the
                repositories required for descendant traversal were not injected
                (module repository for a greenhouse root).
        """
        if entity_type not in ("module", "greenhouse"):
            # Task 5.1 concerns containers only; a monitoring root uses the
            # single-target guard (guard_monitoring_deletion).
            raise DeletionPayloadUnsupportedEntityError(entity_type)
        if entity_type == "greenhouse" and self._modules is None:
            raise ContainerDescendantValidationPortsUnavailableError()

        # Traverse the descendant Monitoring ENTITIES directly (not just ids)
        # so a descendant in a prohibited state is never silently skipped, even
        # if it lacks an assigned id. Order follows repository iteration order.
        descendants = self._descendant_monitorings(entity_type, entity_local_id)
        checked = tuple(m.id for m in descendants if m.id is not None)

        for monitoring in descendants:
            # 1) FSM-state authorization (reuses the Task 4.1 classification via
            #    _classify_state). A prohibited/unknown state on ANY descendant
            #    rejects the whole operation (Req 6.7).
            state = _classify_state(monitoring.status)
            if state is None or state in PROHIBITED_STATES:
                return DescendantValidationResult(
                    outcome=DescendantValidationOutcome.DESCENDANT_STATE_PROHIBITED,
                    entity_type=entity_type,
                    entity_local_id=entity_local_id,
                    offending_monitoring_id=monitoring.id,
                    offending_state=state,
                    message=(
                        f"El monitoreo descendiente {monitoring.id} está en "
                        f"estado '{monitoring.status}', que no permite la "
                        f"eliminación del {entity_type}."
                    ),
                    checked_monitoring_ids=checked,
                )

            # 2) No-interference (reuses the Task 4.2 per-monitoring signal). An
            #    active worker on ANY descendant rejects the whole operation
            #    (Req 6.8). Worker signals are keyed by id; an id-less monitoring
            #    cannot own a runtime worker, so skip the check in that case.
            if monitoring.id is not None and self.has_active_worker(monitoring.id):
                return DescendantValidationResult(
                    outcome=DescendantValidationOutcome.DESCENDANT_WORKER_ACTIVE,
                    entity_type=entity_type,
                    entity_local_id=entity_local_id,
                    offending_monitoring_id=monitoring.id,
                    message=(
                        f"El monitoreo descendiente {monitoring.id} tiene un "
                        f"worker de captura o análisis activo; no se permite la "
                        f"eliminación del {entity_type}."
                    ),
                    checked_monitoring_ids=checked,
                )

        # Empty container or every descendant deletable and worker-free.
        return DescendantValidationResult(
            outcome=DescendantValidationOutcome.ALLOWED,
            entity_type=entity_type,
            entity_local_id=entity_local_id,
            checked_monitoring_ids=checked,
        )

    def guard_container_deletion(
        self,
        entity_type: str,
        entity_local_id: int,
    ) -> DescendantValidationResult:
        """Composable descendant guard for a module/greenhouse container (Task 5.1).

        Thin alias over :meth:`validate_descendants` mirroring the naming of the
        single-monitoring :meth:`guard_monitoring_deletion`. This is the entry
        point the module/greenhouse delete flow (Task 5.2) calls BEFORE TX1; it
        does not create any outbox entry and does not run any cascade.

        Args:
            entity_type: The container root type (``module`` or ``greenhouse``).
            entity_local_id: Local id of the container root.

        Returns:
            The ``DescendantValidationResult`` from :meth:`validate_descendants`.
        """
        return self.validate_descendants(entity_type, entity_local_id)

    def validate_descendants_or_raise(
        self,
        entity_type: str,
        entity_local_id: int,
    ) -> tuple[int, ...]:
        """Raise-based descendant pre-validation for a container (Task 5.1).

        Consistent with the rest of the application layer, this raises
        domain-style errors so the module/greenhouse delete flow (Task 5.2) can
        guard-clause BEFORE TX1. On rejection NO outbox entry is created and NO
        cascade runs (the hierarchy stays intact).

        Args:
            entity_type: The container root type (``module`` or ``greenhouse``).
            entity_local_id: Local id of the container root.

        Returns:
            The tuple of descendant monitoring ids that were validated (possibly
            empty for a container with no monitorings), when validation passes.

        Raises:
            DeletionPayloadUnsupportedEntityError: If ``entity_type`` is not a
                container root.
            ContainerDescendantValidationPortsUnavailableError: If the
                repositories required for descendant traversal were not injected.
            ContainerDescendantNotDeletableError: If ANY descendant is in a
                prohibited FSM state (Req 6.7).
            ContainerDescendantWorkerActiveError: If ANY descendant has an
                active worker (Req 6.8).
        """
        result = self.validate_descendants(entity_type, entity_local_id)
        if result.outcome is DescendantValidationOutcome.DESCENDANT_STATE_PROHIBITED:
            assert result.offending_monitoring_id is not None
            current = (
                result.offending_state.value
                if result.offending_state is not None
                else "desconocido"
            )
            raise ContainerDescendantNotDeletableError(
                entity_type,
                entity_local_id,
                result.offending_monitoring_id,
                current,
            )
        if result.outcome is DescendantValidationOutcome.DESCENDANT_WORKER_ACTIVE:
            assert result.offending_monitoring_id is not None
            raise ContainerDescendantWorkerActiveError(
                entity_type,
                entity_local_id,
                result.offending_monitoring_id,
            )
        return result.checked_monitoring_ids

    # ------------------------------------------------------------------ #
    # Task 4.3 -- capture remote_id / Storage paths / local artifacts     #
    # before the Local_Cascade (BUILD only; no persistence, no cascade).  #
    # ------------------------------------------------------------------ #

    def build_deletion_payload(
        self,
        entity_type: str,
        entity_local_id: int,
    ) -> DeletionOutboxEntryInput:
        """Build (not persist) the outbox payload for a deletion root.

        This captures, BEFORE any Local_Cascade, everything Task 4.4 needs to
        enqueue a single outbox entry (TX1) for a ``monitoring``, ``module`` or
        ``greenhouse`` root:

            - ``remote_id``: the remote UUID of the ROOT entity, read via
              ``SyncStatePort.get_remote_id(entity_type, entity_local_id)``
              (``None`` when the root was never synced).
            - ``remote_table``: the remote table of the root
              (``greenhouses`` | ``modules`` | ``monitorings``). The remote
              backend relies on ON DELETE CASCADE, so only the root table is
              recorded (Req 4.13, 6.2, 7.5).
            - ``storage_paths``: one row per non-null raw/annotated remote path
              of every DESCENDANT snapshot, read via
              ``SyncStatePort.get_storage_paths(snapshot_id)``. Only non-null
              paths are included (Req 6.3, 8.1).
            - ``local_artifacts``: relative to OUTPUTS_DIR as
              ``monitorings/{monitoring_id}`` (never with an ``outputs/``
              prefix). One row for the monitoring itself when the root is a
              monitoring, or one row per DESCENDANT monitoring for a module /
              greenhouse root (Req 4.12, 6.3).

        This method is READ-ONLY: it queries the injected domain repositories
        and the sync-state port but never mutates state, never enqueues, and
        never runs the cascade. It stays composable with the 4.1/4.2 guards
        (callers guard first, then build).

        Args:
            entity_type: One of ``greenhouse`` | ``module`` | ``monitoring``.
            entity_local_id: Local id of the deletion root entity.

        Returns:
            A ``DeletionOutboxEntryInput`` describing the root DELETE target and
            the descendant Storage/local-artifact cleanup work.

        Raises:
            DeletionPayloadUnsupportedEntityError: If ``entity_type`` is not a
                supported root type.
            DeletionPayloadPortsUnavailableError: If the ports required to build
                the payload were not injected at construction time.
        """
        remote_table = ENTITY_TYPE_TO_REMOTE_TABLE.get(entity_type)
        if remote_table is None:
            raise DeletionPayloadUnsupportedEntityError(entity_type)

        if self._sync_state is None or self._snapshots is None:
            raise DeletionPayloadPortsUnavailableError()
        if entity_type == "greenhouse" and self._modules is None:
            raise DeletionPayloadPortsUnavailableError()

        # Collect the descendant monitoring ids for the requested root.
        monitoring_ids = self._collect_descendant_monitoring_ids(
            entity_type, entity_local_id
        )

        # Local artifacts: one row per descendant monitoring, relative to
        # OUTPUTS_DIR (no 'outputs/' prefix).
        local_artifacts = [
            OutboxLocalArtifactInput(relative_path=f"monitorings/{mid}")
            for mid in monitoring_ids
        ]

        # Storage paths: every non-null raw/annotated remote path of every
        # descendant snapshot, aggregated under the single root entry.
        storage_paths = self._collect_descendant_storage_paths(monitoring_ids)

        remote_id = self._sync_state.get_remote_id(entity_type, entity_local_id)

        # Capture the durable LOCAL owner of the root's hierarchy BEFORE the
        # Local_Cascade removes it (Spec 022). Resolved via the effective owner
        # of the root greenhouse; never inferred from the current user. May be
        # None for legacy/unowned roots.
        owner_user_id = self._sync_state.get_effective_owner_local_user_id(
            entity_type, entity_local_id
        )

        return DeletionOutboxEntryInput(
            entity_type=entity_type,
            entity_local_id=entity_local_id,
            remote_table=remote_table,
            remote_id=remote_id,
            owner_user_id=owner_user_id,
            storage_paths=storage_paths,
            local_artifacts=local_artifacts,
        )

    def _collect_descendant_monitoring_ids(
        self,
        entity_type: str,
        entity_local_id: int,
    ) -> list[int]:
        """Return the descendant monitoring ids for a deletion root.

        Read-only traversal via the injected domain repository ports:
            - ``monitoring`` root: the monitoring itself.
            - ``module`` root: every monitoring of the module.
            - ``greenhouse`` root: every monitoring of every module of the
              greenhouse.

        Monitorings without an assigned id are skipped (they cannot own a
        local artifact directory). Order follows repository iteration order.
        """
        if entity_type == "monitoring":
            return [entity_local_id]

        if entity_type == "module":
            return self._monitoring_ids_for_module(entity_local_id)

        # greenhouse
        monitoring_ids: list[int] = []
        assert self._modules is not None  # guarded by build_deletion_payload
        for module in self._modules.get_by_greenhouse(entity_local_id):
            if module.id is None:
                continue
            monitoring_ids.extend(self._monitoring_ids_for_module(module.id))
        return monitoring_ids

    def _monitoring_ids_for_module(self, module_id: int) -> list[int]:
        """Return the ids of all monitorings under a module (id-bearing only)."""
        return [
            monitoring.id
            for monitoring in self._monitorings.get_by_module(module_id)
            if monitoring.id is not None
        ]

    def _descendant_monitorings(
        self,
        entity_type: str,
        entity_local_id: int,
    ):
        """Return the descendant Monitoring ENTITIES for a container root (Task 5.1).

        Sibling of :meth:`_collect_descendant_monitoring_ids` that yields the
        full ``Monitoring`` entities (with ``.status`` and ``.id``) instead of
        only their ids, so the descendant pre-validation can classify every
        descendant's state WITHOUT skipping id-less rows. Read-only traversal
        via the injected domain repositories:

            - ``module`` root: every monitoring of the module.
            - ``greenhouse`` root: every monitoring of every module of the
              greenhouse.

        Order follows repository iteration order. Only ``module``/``greenhouse``
        roots are supported here (containers); callers guard the entity type.
        """
        if entity_type == "module":
            return list(self._monitorings.get_by_module(entity_local_id))

        # greenhouse
        assert self._modules is not None  # guarded by validate_descendants
        monitorings = []
        for module in self._modules.get_by_greenhouse(entity_local_id):
            if module.id is None:
                continue
            monitorings.extend(self._monitorings.get_by_module(module.id))
        return monitorings

    def _collect_descendant_storage_paths(
        self,
        monitoring_ids: list[int],
    ) -> list[OutboxStoragePathInput]:
        """Collect non-null remote Storage paths for all descendant snapshots.

        For each monitoring, traverse its snapshots and, via the sync-state
        port, read each snapshot's ``raw``/``annotated`` remote paths. Only
        non-null paths are emitted (Req 6.3, 8.1). Snapshots without an id are
        skipped, since a Storage path is keyed by the snapshot id.
        """
        assert self._snapshots is not None  # guarded by build_deletion_payload
        assert self._sync_state is not None  # guarded by build_deletion_payload

        storage_paths: list[OutboxStoragePathInput] = []
        for monitoring_id in monitoring_ids:
            for snapshot in self._snapshots.get_by_monitoring(monitoring_id):
                if snapshot.id is None:
                    continue
                paths = self._sync_state.get_storage_paths(snapshot.id)
                if paths.raw_storage_path:
                    storage_paths.append(
                        OutboxStoragePathInput(storage_path=paths.raw_storage_path)
                    )
                if paths.annotated_storage_path:
                    storage_paths.append(
                        OutboxStoragePathInput(
                            storage_path=paths.annotated_storage_path
                        )
                    )
        return storage_paths

    # ------------------------------------------------------------------ #
    # Task 4.4 -- two-transaction protocol                                #
    #   TX1: enqueue outbox entry (prepared) + COMMIT                     #
    #   TX2: Local_Cascade + mark completed (deleted_at) in one txn       #
    # No network calls anywhere -- deletion is connectivity-independent.  #
    # ------------------------------------------------------------------ #

    def delete_monitoring(self, monitoring_id: int) -> DeletionResult:
        """Durably delete a monitoring via the two-transaction protocol.

        Convenience wrapper over :meth:`delete` for the ``monitoring`` root.
        Guards first (Tasks 4.1/4.2), builds the payload (Task 4.3), then runs
        TX1 (outbox ``prepared`` + COMMIT) and TX2 (``Local_Cascade`` + mark
        ``completed``).

        Args:
            monitoring_id: Local id of the monitoring to delete.

        Returns:
            A ``DeletionResult`` describing the durable local deletion.

        Raises:
            MonitoringNotFoundError / MonitoringStateNotDeletableError /
            MonitoringWorkerActiveError: If the guard rejects the deletion; no
                outbox entry or cascade is created in that case.
            DeletionOutboxRegistrationError: If TX1 (enqueue) fails.
            LocalCascadeFailedError: If TX2 (cascade) fails.
            DeletionOrchestrationPortsUnavailableError: If the 4.4 ports were
                not injected.
        """
        return self.delete("monitoring", monitoring_id)

    def delete_module(self, module_id: int) -> DeletionResult:
        """Durably delete a module and its hierarchy (Task 5.2).

        Convenience wrapper over :meth:`delete` for the ``module`` root,
        mirroring :meth:`delete_monitoring`. Runs the Task 5.1 descendant
        pre-validation BEFORE TX1, then the same durable two-transaction path
        as monitoring (TX1 outbox ``prepared`` + COMMIT, TX2 ``Local_Cascade``
        + mark ``completed``).

        Args:
            module_id: Local id of the module to delete.

        Returns:
            A ``DeletionResult`` describing the durable local deletion.

        Raises:
            ContainerDescendantNotDeletableError / ContainerDescendantWorkerActiveError:
                If any descendant monitoring is in a prohibited state or has an
                active worker; NO outbox entry or cascade is created (Req 6.5-6.8).
            DeletionOutboxRegistrationError: If TX1 (enqueue) fails.
            LocalCascadeFailedError: If TX2 (cascade) fails.
            DeletionOrchestrationPortsUnavailableError: If the 4.4 ports were
                not injected.
        """
        return self.delete("module", module_id)

    def delete_greenhouse(self, greenhouse_id: int) -> DeletionResult:
        """Durably delete a greenhouse and its hierarchy (Task 5.2).

        Convenience wrapper over :meth:`delete` for the ``greenhouse`` root,
        mirroring :meth:`delete_monitoring`. Runs the Task 5.1 descendant
        pre-validation BEFORE TX1, then the same durable two-transaction path
        as monitoring.

        Args:
            greenhouse_id: Local id of the greenhouse to delete.

        Returns:
            A ``DeletionResult`` describing the durable local deletion.

        Raises:
            ContainerDescendantNotDeletableError / ContainerDescendantWorkerActiveError:
                If any descendant monitoring is in a prohibited state or has an
                active worker; NO outbox entry or cascade is created (Req 6.5-6.8).
            DeletionOutboxRegistrationError: If TX1 (enqueue) fails.
            LocalCascadeFailedError: If TX2 (cascade) fails.
            DeletionOrchestrationPortsUnavailableError: If the 4.4 ports were
                not injected.
        """
        return self.delete("greenhouse", greenhouse_id)

    def delete(self, entity_type: str, entity_local_id: int) -> DeletionResult:
        """Durably delete a root entity via the two-transaction protocol.

        Flow (Req 1.3, 2.1, 2.3, 4.3, 4.7, 4.8, 4.9, 4.10, 4.11):

            1. Guard first. For a ``monitoring`` root, reuse the composed
               FSM-state + no-interference guard (Tasks 4.1/4.2). For a
               ``module``/``greenhouse`` root, run the Task 5.1 descendant
               pre-validation (every descendant monitoring must be deletable and
               worker-free). If the guard rejects, NO outbox entry and NO
               cascade are created (the guard's error propagates).
            2. Build the outbox payload BEFORE touching persistence (Task 4.3).
            3. TX1 -- enqueue the outbox entry via ``DeletionOutboxPort.enqueue``.
               This commits synchronously with ``local_delete_status='prepared'``
               (Req 4.8). Enqueue is idempotent by (entity_type, entity_local_id),
               so re-invoking for a ``prepared``/``failed`` entry reuses the
               existing entry instead of creating a duplicate (Req 4.11). If
               enqueue fails, abort WITHOUT the cascade — the hierarchy stays
               intact — and raise ``DeletionOutboxRegistrationError`` (Req 4.7).
            4. TX2 -- call ``LocalCascadePort.execute_cascade`` with a UTC
               ``deleted_at`` captured NOW (the moment of successful local
               deletion; never set during TX1). The cascade deletes the
               hierarchy AND sets ``local_delete_status='completed'`` +
               ``deleted_at`` in the SAME transaction (Req 4.9). On success,
               zero dependent records remain.
            5. If TX2 fails, mark the entry ``failed`` via
               ``mark_local_failed`` so it stays non-completed, visible and
               retriable; the deletion is NOT propagated remotely (only
               ``completed`` entries are), the hierarchy is intact, and
               ``LocalCascadeFailedError`` is raised (Req 4.10).

        This flow performs NO network calls: the deletion is durable and
        connectivity-independent.

        For a ``module``/``greenhouse`` root the deletion reuses the SAME
        durable two-transaction path as a monitoring: a SINGLE root outbox entry
        (the root's ``remote_id`` + ``remote_table``, relying on remote ON
        DELETE CASCADE for descendant rows -- Req 7.5), with descendant snapshot
        Storage paths aggregated and one local-artifact row per descendant
        monitoring. The Task 5.1 descendant pre-validation runs BEFORE TX1.

        Args:
            entity_type: Root entity type. ``monitoring`` uses the single-target
                guard; ``module``/``greenhouse`` use the Task 5.1 descendant
                pre-validation before the shared TX1/TX2 path.
            entity_local_id: Local id of the deletion root entity.

        Returns:
            A ``DeletionResult`` for the durable local deletion.

        Raises:
            DeletionPayloadUnsupportedEntityError: If ``entity_type`` is not a
                supported root type.
            DeletionOrchestrationPortsUnavailableError: If the 4.4 ports were
                not injected at construction time.
            MonitoringNotFoundError / MonitoringStateNotDeletableError /
            MonitoringWorkerActiveError: On guard rejection (monitoring root).
            ContainerDescendantNotDeletableError / ContainerDescendantWorkerActiveError:
                On descendant pre-validation rejection (module/greenhouse root);
                no outbox entry or cascade is created.
            DeletionOutboxRegistrationError: If TX1 (enqueue) fails.
            LocalCascadeFailedError: If TX2 (cascade) fails.
        """
        if entity_type not in ENTITY_TYPE_TO_REMOTE_TABLE:
            raise DeletionPayloadUnsupportedEntityError(entity_type)
        if self._deletion_outbox is None or self._local_cascade is None:
            raise DeletionOrchestrationPortsUnavailableError()

        # 1. Guard first -- reject WITHOUT creating any outbox entry or cascade.
        #    - monitoring root: single-target FSM-state + no-interference guard.
        #    - module/greenhouse root: Task 5.1 descendant pre-validation. If
        #      ANY descendant is in a prohibited state or has an active worker,
        #      the WHOLE operation is rejected here (no payload, no TX1, no TX2)
        #      with the hierarchy left intact (Req 6.5, 6.6, 6.7, 6.8).
        if entity_type == "monitoring":
            self.authorize_monitoring_deletion_or_raise(entity_local_id)
        else:
            self.validate_descendants_or_raise(entity_type, entity_local_id)

        # 2. Build the payload (read-only) before any persistence mutation.
        payload = self.build_deletion_payload(entity_type, entity_local_id)

        # 3. TX1 -- enqueue (prepared + COMMIT). Idempotent by identity, so a
        #    retry of a prepared/failed entry reuses the existing one.
        try:
            entry = self._deletion_outbox.enqueue(payload)
        except Exception as exc:  # noqa: BLE001 - re-wrapped as a durable-reg error
            raise DeletionOutboxRegistrationError(
                entity_type, entity_local_id
            ) from exc

        reused = self._is_reused_entry(entry)

        # 4. TX2 -- Local_Cascade + mark completed (deleted_at) in one txn.
        #    deleted_at marks the moment of successful local deletion.
        deleted_at = datetime.now(timezone.utc)
        result = self._local_cascade.execute_cascade(
            entity_type=entity_type,
            entity_local_id=entity_local_id,
            outbox_id=entry.id,
            deleted_at=deleted_at,
        )

        # 5. On TX2 failure, keep the entry non-completed (mark failed) so it is
        #    never propagated remotely; hierarchy stays intact.
        if not result.success:
            self._deletion_outbox.mark_local_failed(entry.id)
            raise LocalCascadeFailedError(
                entity_type, entity_local_id, result.error_message
            )

        return DeletionResult(
            entity_type=entity_type,
            entity_local_id=entity_local_id,
            outbox_id=entry.id,
            deleted_at=deleted_at,
            reused_outbox_entry=reused,
        )

    @staticmethod
    def _is_reused_entry(entry: DeletionOutboxEntry) -> bool:
        """Best-effort signal of whether an existing outbox entry was reused.

        Idempotent ``enqueue`` returns the existing entry for a non-completed
        (``prepared``/``failed``) pair instead of creating a duplicate. A
        ``failed`` local_delete_status is a strong indicator of a prior attempt;
        a positive ``retry_count`` also points at reuse. This is informational
        only and never affects correctness of the deletion.
        """
        if entry.local_delete_status == "failed":
            return True
        return entry.retry_count > 0
