class DomainError(Exception):
    """Base exception for all domain-level errors."""

    pass


class MediaNotFoundError(DomainError):
    """Raised when a requested media item cannot be found locally or remotely."""

    pass


class NetworkError(DomainError):
    """Raised when a network request to an external provider fails."""

    pass


class CacheExpiredError(DomainError):
    """Raised when cached metadata has exceeded its TTL."""

    pass


class RepositoryError(DomainError):
    """Raised when an operation on a repository fails (e.g. DB constraint)."""

    pass
