"""Domain exceptions for the Tomato Monitor agricultural data model.

Each exception provides descriptive attributes to facilitate error handling
and debugging. The hierarchy is rooted in DomainError so that application
layers can catch all domain-level errors uniformly.
"""


class DomainError(Exception):
    """Base exception for all domain-layer errors."""

    pass


class InvalidTransitionError(DomainError):
    """State machine transition not allowed.

    Raised when a monitoring status transition is attempted that is not
    in the set of valid transitions for the current state.
    """

    def __init__(
        self,
        current_state: str,
        target_state: str,
        allowed_transitions: list[str],
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        self.allowed_transitions = allowed_transitions
        super().__init__(
            f"Cannot transition from '{current_state}' to '{target_state}'. "
            f"Allowed transitions: {allowed_transitions}"
        )


class DuplicateModuleError(DomainError):
    """Module name already exists in greenhouse.

    Raised when attempting to create a module with a name that is already
    used within the same greenhouse.
    """

    def __init__(self, greenhouse_id: int, module_name: str) -> None:
        self.greenhouse_id = greenhouse_id
        self.module_name = module_name
        super().__init__(
            f"Module '{module_name}' already exists in greenhouse {greenhouse_id}."
        )


class ParentNotFoundError(DomainError):
    """Referenced parent entity does not exist.

    Raised when a child entity references a parent ID that does not
    correspond to any existing record.
    """

    def __init__(self, parent_type: str, parent_id: int) -> None:
        self.parent_type = parent_type
        self.parent_id = parent_id
        super().__init__(
            f"{parent_type} with id {parent_id} does not exist."
        )


class InvalidImagePathError(DomainError):
    """Image path fails validation rules.

    Raised when an image path exceeds length limits, contains path
    traversal sequences, or uses absolute path prefixes.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"Invalid image path '{path}': {reason}"
        )


class MetricsNotAllowedError(DomainError):
    """Cannot create metrics for monitoring in current status.

    Raised when attempting to create MonitoringMetrics for a monitoring
    session that has not reached a terminal state (completed or aborted).
    """

    def __init__(self, monitoring_id: int, current_status: str) -> None:
        self.monitoring_id = monitoring_id
        self.current_status = current_status
        super().__init__(
            f"Cannot create metrics for monitoring {monitoring_id} "
            f"with status '{current_status}'. "
            f"Monitoring must be in 'completed' or 'aborted' state."
        )
