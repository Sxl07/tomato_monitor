"""Port: RecoveryTombstonePort (Spec 022, block D3.1).

Anti-resurrection guard used by the RecoveryService. Before recovering (or
reusing) a remote entity, the service asks this port whether a durable local
tombstone (from the ``deletion_outbox``) blocks that remote identity.

A tombstone blocks recovery when its ``entity_type`` and ``remote_id`` match
EXACTLY and its remote-propagation status is not yet ``synced`` (i.e. one of
``pending`` | ``syncing`` | ``error``). Matching is by remote identity only —
never by name, local id, timestamps, or owner. A tombstone with a NULL
``remote_id`` never blocks a valid remote UUID.

The application layer stays free of SQLAlchemy: concrete implementations live
in the infrastructure layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class TombstoneCheckResult:
    """Outcome of a tombstone check for a single remote identity.

    Attributes:
        success: Whether the check could be determined safely. False means the
            lookup failed and the caller must fail-safe (skip the row).
        blocked: True when a blocking tombstone exists. Only meaningful when
            ``success`` is True.
        status: The blocking tombstone's remote status when blocked (for
            diagnostics), else None.
        error_message: Short, safe description when ``success`` is False.
    """

    success: bool
    blocked: bool
    status: Optional[str] = None
    error_message: Optional[str] = None


@runtime_checkable
class RecoveryTombstonePort(Protocol):
    """Abstract anti-resurrection guard for recovery."""

    def check(self, entity_type: str, remote_id: str) -> TombstoneCheckResult:
        """Return whether a blocking tombstone exists for a remote identity.

        Args:
            entity_type: The deletion_outbox entity type
                (``greenhouse`` | ``module`` | ``monitoring``).
            remote_id: The remote UUID of the entity being considered.

        Returns:
            TombstoneCheckResult. ``success=True, blocked=True`` when a blocking
            tombstone exists; ``success=True, blocked=False`` when none exists;
            ``success=False`` when the lookup could not be determined safely.
        """
        ...
