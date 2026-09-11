import hashlib
import os
import threading
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

_CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "ciak", "posters")
_lock = threading.Lock()
_last_prune = 0.0
_settings = None


def _ensure_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _key(url):
    return hashlib.sha256(url.encode()).hexdigest() + ".jpg"


def _scaled_key(url, w, h):
    """Thumbnail entries live in the same dir (so get_size/prune/clear and
    invalidate's siblings stay in one place) under {sha}@{W}x{H}.jpg."""
    return hashlib.sha256(url.encode()).hexdigest() + f"@{w}x{h}.jpg"


def get(url):
    path = os.path.join(_CACHE_DIR, _key(url))
    if os.path.isfile(path):
        return path
    return None


def get_scaled(url, w, h):
    path = os.path.join(_CACHE_DIR, _scaled_key(url, w, h))
    if os.path.isfile(path):
        return path
    return None


def put_scaled(url, w, h, data):
    _ensure_dir()
    path = os.path.join(_CACHE_DIR, _scaled_key(url, w, h))
    with _lock:
        with open(path, "wb") as f:
            f.write(data)
    global _last_prune
    now = time.monotonic()
    if now - _last_prune > 2.0:
        _last_prune = now
        prune(_max_cache_bytes())
    return path


def put(url, data):
    _ensure_dir()
    path = os.path.join(_CACHE_DIR, _key(url))
    with _lock:
        with open(path, "wb") as f:
            f.write(data)
    global _last_prune
    now = time.monotonic()
    if now - _last_prune > 2.0:
        _last_prune = now
        prune(_max_cache_bytes())
    return path


def invalidate(url):
    """Drop the source file and any pre-scaled thumbnails for this URL."""
    base = hashlib.sha256(url.encode()).hexdigest()
    with _lock:
        try:
            for name in os.listdir(_CACHE_DIR):
                if name == (base + ".jpg") or name.startswith(base + "@"):
                    path = os.path.join(_CACHE_DIR, name)
                    if os.path.isfile(path):
                        os.unlink(path)
        except FileNotFoundError:
            pass


def _max_cache_bytes():
    from . import config

    global _settings
    if _settings is None:
        try:
            _settings = Gio.Settings.new(config.APP_ID)
        except GLib.Error:
            return 512 * 1024 * 1024
    try:
        return _settings.get_int("cache-max-size-mb") * 1024 * 1024
    except GLib.Error:
        return 512 * 1024 * 1024


def get_size():
    total = 0
    try:
        for name in os.listdir(_CACHE_DIR):
            path = os.path.join(_CACHE_DIR, name)
            if os.path.isfile(path):
                total += os.path.getsize(path)
    except FileNotFoundError:
        pass
    return total


def clear():
    with _lock:
        try:
            for name in os.listdir(_CACHE_DIR):
                path = os.path.join(_CACHE_DIR, name)
                if os.path.isfile(path):
                    os.unlink(path)
        except FileNotFoundError:
            pass


def prune(max_bytes):
    if max_bytes <= 0:
        return
    with _lock:
        try:
            files = []
            for name in os.listdir(_CACHE_DIR):
                path = os.path.join(_CACHE_DIR, name)
                if os.path.isfile(path):
                    files.append((os.path.getmtime(path), path, os.path.getsize(path)))
        except FileNotFoundError:
            return

        total = sum(size for _, _, size in files)
        if total <= max_bytes:
            return

        files.sort()
        for _, path, size in files:
            if total <= max_bytes:
                break
            try:
                os.unlink(path)
            except OSError:
                continue
            total -= size
