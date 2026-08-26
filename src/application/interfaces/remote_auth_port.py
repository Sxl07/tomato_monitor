"""Port: RemoteAuthPort.

Defines the abstract contract for remote authentication operations.
Concrete implementations (e.g., Supabase Auth via httpx) reside in
the infrastructure layer.

The port does NOT persist tokens, manage sessions, or handle profiles.
Profiles are created remotely by the auth provider's server-side triggers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RemoteAuthResult:
    """Result of a remote authentication operation.

    Attributes:
        success: Whether the operation completed successfully.
        user_id: Remote user identifier (UUID string) on success.
        access_token: Ephemeral JWT token on success. Not persisted.
        email: User email returned by the provider on success.
        full_name: User display name returned by the provider on success.
        error_type: Classification of the error on failure.
            One of: CONNECTIVITY, REMOTE_UNAVAILABLE, INVALID_CREDENTIALS,
            EMAIL_EXISTS, RATE_LIMITED, AUTH_FORBIDDEN, UNKNOWN.
        error_message: Human-readable error description on failure.
    """

    success: bool
    user_id: Optional[str] = None
    access_token: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class RemoteAuthPort(Protocol):
    """Abstract port for remote authentication operations.

    Implementations handle sign-up and sign-in against a remote
    auth provider. The port is agnostic to the provider's HTTP
    transport and internal mechanics.

    Contract:
        - sign_up registers a new user remotely.
        - sign_in authenticates an existing user remotely.
        - Neither method persists tokens or manages local sessions.
        - Remote profile creation is handled by provider-side triggers.
    """

    def sign_up(
        self,
        email: str,
        password: str,
        full_name: str,
    ) -> RemoteAuthResult:
        """Register a new user with the remote auth provider.

        Args:
            email: User email address.
            password: User password (plaintext; hashing is provider-side).
            full_name: User display name sent as metadata.

        Returns:
            RemoteAuthResult with success=True and user details on success,
            or success=False with error_type and error_message on failure.
        """
        ...

    def sign_in(
        self,
        email: str,
        password: str,
    ) -> RemoteAuthResult:
        """Authenticate an existing user with the remote auth provider.

        Args:
            email: User email address.
            password: User password (plaintext; verification is provider-side).

        Returns:
            RemoteAuthResult with success=True and ephemeral access_token
            on success, or success=False with error classification on failure.
        """
        ...
