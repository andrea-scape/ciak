"""GNOME Keyring (libsecret) wrapper for storing sync credentials."""

import logging

_log = logging.getLogger(__name__)

try:
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret

    _SCHEMA = Secret.Schema.new(
        "io.github.andrea_scape.ciak.Credentials",
        Secret.SchemaFlags.NONE,
        {
            "application": Secret.SchemaAttributeType.STRING,
            "service": Secret.SchemaAttributeType.STRING,
            "key": Secret.SchemaAttributeType.STRING,
        },
    )
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _log.warning("libsecret unavailable; sync credentials will not persist across restarts")


def store_credential(service: str, key: str, value: str) -> bool:
    """Store a credential in the keyring. Returns True on success."""
    if not _AVAILABLE:
        return False
    try:
        return Secret.password_store_sync(
            _SCHEMA,
            {"application": "ciak", "service": service, "key": key},
            Secret.COLLECTION_DEFAULT,
            f"Ciak — {service} {key}",
            value,
            None,
        )
    except Exception:
        _log.exception("Failed to store credential %s/%s", service, key)
        return False


def load_credential(service: str, key: str) -> str | None:
    """Load a credential from the keyring. Returns None if not found."""
    if not _AVAILABLE:
        return None
    try:
        return Secret.password_lookup_sync(
            _SCHEMA,
            {"application": "ciak", "service": service, "key": key},
            None,
        )
    except Exception:
        _log.exception("Failed to load credential %s/%s", service, key)
        return None


def delete_credential(service: str, key: str) -> bool:
    """Delete a single credential. Returns True on success."""
    if not _AVAILABLE:
        return False
    try:
        return Secret.password_clear_sync(
            _SCHEMA,
            {"application": "ciak", "service": service, "key": key},
            None,
        )
    except Exception:
        _log.exception("Failed to delete credential %s/%s", service, key)
        return False


def delete_service(service: str) -> bool:
    """Delete all credentials for a service."""
    if not _AVAILABLE:
        return False
    deleted = True
    for key in ("session_id", "account_id", "access_token", "client_id",
                "username", "password", "session_cookie", "csrf_token"):
        if not delete_credential(service, key):
            deleted = False
    return deleted
