class DomainError(Exception):
    """Base exception for all domain-level errors."""

    pass


class NetworkError(DomainError):
    """Raised when a network request to an external provider fails."""

    pass
