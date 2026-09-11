"""Port: RemoteReadPort.

Defines the abstract contract for remote data read operations used by the
recovery flow (Spec 022, block D1). Concrete implementations (e.g., Supabase
PostgREST via httpx) reside in the infrastructure layer.

The port is agnostic to the remote database engine, REST API format, and
transport library. It performs owner-scoped reads to reconstruct the local
hierarchy from the remote store.

Ownership and filtering model:
    - Only the ``greenhouses`` table has an ``owner_user_id`` column. Child
      tables (modules, monitorings, monitoring_metrics, snapshots,
      inspection_results, activity_logs) do NOT carry an owner column; they
      inherit ownership transitively and are protected server-side by
      Row-Level Security (the caller's JWT is the security boundary).
    - Because of this, callers MUST NOT pass ``owner_user_id`` as a filter for
      child tables. For child tables ownership is enforced by RLS via the JWT.
    - ``owner_user_id`` is the REMOTE owner UUID (Supabase Auth user id /
      ``auth.uid()``), NOT a local integer id.
    - ``filters`` are for real table keys (e.g. ``greenhouse_id``,
      ``module_id``, ``monitoring_id``, ``snapshot_id``) and are applied as
      PostgREST equality only. Callers pass plain values; operator syntax is
      not accepted.

Error taxonomy (``error_type`` on failure):
    - CONNECTIVITY: network failure or timeout reaching the remote store.
    - REMOTE_UNAVAILABLE: remote store returned a 5xx server error.
    - RLS_DENIED: permission / row-level security denial (401 or 403).
    - UNKNOWN: any other failure (e.g. disallowed table, unexpected status).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class RemoteQueryResult:
    """Result of a remote owner-scoped read operation.

    Attributes:
        success: Whether the query completed successfully.
        rows: Rows returned by the remote store on success. Each row is a dict
            of column-value pairs as returned by the remote REST API. Empty
            list when the query succeeded but matched no rows.
        error_type: Classification of the error on failure. One of:
            CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, UNKNOWN.
        error_message: Short, safe human-readable error description on failure.
    """

    success: bool
    rows: list[dict] = field(default_factory=list)
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteReadPort(Protocol):
    """Abstract port for owner-scoped remote read operations.

    Implementations handle reading records from a remote data store for the
    recovery flow. The port does not know about specific table schemas, the
    underlying REST API format, or RLS policies.

    Contract:
        - fetch_by_owner returns all rows of a table visible to the caller,
          optionally narrowed by real table-key ``filters``.
        - Authentication is provided via an ephemeral ``access_token`` per call.
        - The port does not manage tokens or sessions and never persists them.
        - See the module docstring for the ownership/filtering model and the
          error taxonomy.
    """

    def fetch_by_owner(
        self,
        access_token: str,
        table: str,
        owner_user_id: str,
        filters: Optional[dict] = None,
    ) -> RemoteQueryResult:
        """Read records of ``table`` visible to the given owner.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            table: Target table name in the remote store.
            owner_user_id: Remote owner UUID (Supabase Auth user id). Applied
                as a defense-in-depth filter ONLY for the ``greenhouses`` table;
                ignored as a filter for child tables (RLS enforces ownership).
            filters: Optional mapping of real table keys to plain values,
                applied as PostgREST equality only (e.g.
                ``{"greenhouse_id": "<uuid>"}``). Operator syntax is not
                accepted.

        Returns:
            RemoteQueryResult with success=True and rows on success, or
            success=False with an error classification on failure.
        """
        ...
