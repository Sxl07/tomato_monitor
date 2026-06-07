"""Infrastructure persistence exceptions."""


class DatabaseInitError(Exception):
    """Database initialization failed.

    Raised when the database engine, directory creation, or schema
    initialization encounters a fatal error (permission denied,
    disk full, corrupted file, locked database, etc.).
    """

    def __init__(self, cause: str, original: Exception | None = None) -> None:
        self.cause = cause
        self.original = original
        message = f"Database initialization failed: {cause}"
        if original:
            super().__init__(message)
            self.__cause__ = original
        else:
            super().__init__(message)
