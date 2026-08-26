"""Port: RemoteStoragePort.

Defines the abstract contract for remote file storage operations.
Concrete implementations (e.g., Supabase Storage via httpx) reside
in the infrastructure layer.

The port is agnostic to buckets, signed URLs, and the transport library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RemoteUploadResult:
    """Result of a remote file upload operation.

    Attributes:
        success: Whether the upload completed successfully.
        object_path: Remote object path on success (provider-relative).
        error_type: Classification of the error on failure.
            One of: CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED,
            STORAGE_ERROR, UNKNOWN.
        error_message: Human-readable error description on failure.
    """

    success: bool
    object_path: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteStoragePort(Protocol):
    """Abstract port for remote file storage operations.

    Implementations handle uploading local files to a remote object store.
    The port does not know about specific buckets, URL construction, or
    the underlying HTTP transport.

    Contract:
        - upload_file uploads a single local file to the specified remote path.
        - Authentication is provided via an ephemeral access_token per call.
        - The port does not construct public/signed URLs.
        - The port does not read environment variables.
    """

    def upload_file(
        self,
        access_token: str,
        local_file_path: str,
        remote_path: str,
    ) -> RemoteUploadResult:
        """Upload a local file to the remote storage.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            local_file_path: Path to the local file to upload.
            remote_path: Destination path in the remote storage.

        Returns:
            RemoteUploadResult with success=True and object_path on success,
            or success=False with error classification on failure.
        """
        ...
