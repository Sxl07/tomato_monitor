"""Port: RemoteDataPort.

Defines the abstract contract for remote data persistence operations.
Concrete implementations (e.g., Supabase PostgREST via httpx) reside
in the infrastructure layer.

The port is agnostic to the remote database engine, REST API format,
and transport library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RemoteUpsertResult:
    """Result of a remote upsert operation.

    Attributes:
        success: Whether the upsert completed successfully.
        remote_id: Remote record identifier (UUID string) on success.
        error_type: Classification of the error on failure.
            One of: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED,
            UNKNOWN.
        error_message: Human-readable error description on failure.
    """

    success: bool
    remote_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteDataPort(Protocol):
    """Abstract port for remote data persistence operations.

    Implementations handle upsert of records to a remote data store.
    The port does not know about specific table schemas, RLS policies,
    or the underlying REST API format.

    Contract:
        - upsert inserts or updates a single record in the specified table.
        - Authentication is provided via an ephemeral access_token per call.
        - The port does not manage tokens or sessions.
    """

    def upsert(
        self,
        access_token: str,
        table: str,
        data: dict,
    ) -> RemoteUpsertResult:
        """Insert or update a record in the remote data store.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            table: Target table name in the remote store.
            data: Dictionary of column-value pairs to upsert.

        Returns:
            RemoteUpsertResult with success=True and remote_id on success,
            or success=False with error classification on failure.
        """
        ...
