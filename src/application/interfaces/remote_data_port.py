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


@dataclass
class RemoteDeleteResult:
    """Result of a remote idempotent delete operation.

    A delete is considered successful when the record was removed or when
    it was already absent from the remote store (idempotency).

    Attributes:
        success: Whether the delete completed successfully. True also when
            the record was already absent (already_absent=True).
        already_absent: Whether the record did not exist remotely
            (e.g., a 404/not-found response), treated as success.
        error_type: Classification of the error on failure.
            One of: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED, UNKNOWN.
        error_message: Human-readable error description on failure.
    """

    success: bool
    already_absent: bool = False
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteDataPort(Protocol):
    """Abstract port for remote data persistence operations.

    Implementations handle upsert and idempotent delete of records in a
    remote data store. The port does not know about specific table schemas,
    RLS policies, or the underlying REST API format.

    Contract:
        - upsert inserts or updates a single record in the specified table.
        - delete_by_id removes a single record and is idempotent: deleting a
          record that no longer exists is reported as success.
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

    def delete_by_id(
        self,
        access_token: str,
        table: str,
        remote_id: str,
    ) -> RemoteDeleteResult:
        """Delete a single record from the remote data store idempotently.

        The operation is idempotent: attempting to delete a record that no
        longer exists in the remote store (e.g., a 404/not-found response)
        is treated as success with already_absent=True. Consecutive calls
        for the same remote_id therefore yield identical successful results.

        Deleting a parent record relies on the remote store's existing
        ON DELETE CASCADE relationships to remove dependent child records.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            table: Target table name in the remote store.
            remote_id: Identifier (UUID string) of the record to delete.

        Returns:
            RemoteDeleteResult with success=True on removal or when the
            record was already absent; success=False with a retryable
            error classification when the delete fails.
        """
        ...
