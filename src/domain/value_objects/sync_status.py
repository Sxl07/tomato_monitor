"""Value objects for synchronization and export status tracking."""

from enum import Enum


class SyncStatus(str, Enum):
    """Synchronization status for records (monitorings, activities)."""

    PENDING = "pending"
    EXPORTED = "exported"
    SYNCED = "synced"
    ERROR = "error"
    LOCAL_ONLY = "local_only"
    PENDING_SYNC = "pending_sync"


class ExportStatus(str, Enum):
    """Status of an export package generation."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    ERROR = "error"
