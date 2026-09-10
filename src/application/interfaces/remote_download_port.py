"""Port: RemoteDownloadPort (Spec 022, block D3.2).

Abstract contract for downloading a single object from a remote object store
(e.g. Supabase Storage). Concrete implementations live in the infrastructure
layer and own the transport (httpx), authentication headers, and provider
specifics.

This port is agnostic to buckets, signed URLs, and the transport library. It:
    - Downloads exactly one object identified by a provider-relative path.
    - Authenticates with an ephemeral access_token per call.
    - Never constructs public/signed URLs and never uses service_role.
    - Never retries internally.
    - Never touches the local filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RemoteDownloadResult:
    """Result of a single remote object download.

    Attributes:
        success: Whether the object was retrieved (HTTP 200 with a body).
        content: Raw object bytes on success; None otherwise.
        error_type: Classification of the failure. One of:
            NOT_FOUND, CONNECTIVITY, REMOTE_UNAVAILABLE, RLS_DENIED,
            STORAGE_ERROR, UNKNOWN.
        error_message: Short, safe error description (never a full remote body,
            JWT, or key).
    """

    success: bool
    content: Optional[bytes] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteDownloadPort(Protocol):
    """Abstract port for downloading a single remote object.

    Contract:
        - download_object retrieves one object at the given provider-relative
          path, authenticating with the supplied ephemeral access_token.
        - Returns success=True with content on HTTP 200.
        - Returns success=False with an error_type on any failure; a missing
          object is reported as error_type="NOT_FOUND".
        - The port does not construct public/signed URLs, does not use
          service_role, does not retry, and does not read the filesystem.
    """

    def download_object(
        self,
        access_token: str,
        remote_path: str,
    ) -> RemoteDownloadResult:
        """Download a single object from the remote store.

        Args:
            access_token: Ephemeral JWT for authenticating the request.
            remote_path: Provider-relative object path within the bucket.

        Returns:
            RemoteDownloadResult with success=True and content on success, or
            success=False with an error classification on failure.
        """
        ...
