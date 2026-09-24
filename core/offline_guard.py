"""
Offline guard — enforces HARD CONSTRAINT #1 (see CLAUDE.md): this
application must never use the network and no loaded data may leave the
machine.

``install()`` must be called as the very first thing in ``main.py``,
before importing PySide6, torch, cv2, or anything else that might try to
phone home. It:

  1. Sets environment variables that tell well-known ML libraries
     (huggingface_hub / transformers) to never attempt a network call, and
     redirects torch's cache directory to a local, writable folder instead
     of the user's home directory.
  2. Monkeypatches the low-level ``socket`` primitives so that any attempt
     to connect to a non-loopback address raises ``OfflineViolation``
     (a subclass of ``OSError``) instead of touching the network. Loopback
     addresses (127.0.0.0/8, ::1, "localhost") and AF_UNIX sockets are left
     alone so Qt's local IPC (e.g. D-Bus-less local sockets on some
     platforms) keeps working.

This module intentionally has no Qt imports so it is safe to import first,
before the Qt bindings are loaded, and so it can be unit-tested without a
display.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timezone


class OfflineViolation(OSError):
    """Raised when code in this process attempts a non-loopback network call."""


_LOCK = threading.Lock()
_INSTALLED = False

# Saved originals so uninstall() can restore them exactly.
_ORIGINALS = {}

_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}

# socket.AF_UNIX does not exist on Windows (Python only defines it on
# platforms that support it). Resolve once, defensively, to None.
_AF_UNIX = getattr(socket, "AF_UNIX", None)


def _is_loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", "ignore")
        except Exception:
            return False
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_address(address) -> bool:
    """``address`` is whatever gets passed to connect()/connect_ex() —
    a (host, port[, ...]) tuple for AF_INET/AF_INET6, or a path/str for
    AF_UNIX."""
    if address is None:
        return True
    if isinstance(address, (tuple, list)):
        if not address:
            return True
        return _is_loopback_host(address[0])
    # AF_UNIX path, or something we don't recognise — allow it; it isn't a
    # network address.
    return True


def _log(message: str) -> None:
    """Append a line to the offline-guard log. Never raises — a logging
    failure must not crash or (worse) leak an exception that changes
    control flow in calling code."""
    try:
        line = f"{datetime.now(timezone.utc).isoformat()} {message}\n"
        log_dir = _log_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "offline_guard.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _local_appdata_root() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return os.path.join(local_appdata, "GrainAnalyzer")
    # Non-Windows / no LOCALAPPDATA: fall back to a per-user cache dir.
    return os.path.join(os.path.expanduser("~"), ".grainanalyzer")


def _log_dir() -> str:
    try:
        root = _local_appdata_root()
        return os.path.join(root, "logs")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "GrainAnalyzer", "logs")


def _torch_home_dir() -> str:
    try:
        root = _local_appdata_root()
        return os.path.join(root, "torch_cache")
    except Exception:
        return os.path.join(tempfile.gettempdir(), "GrainAnalyzer", "torch_cache")


def _set_env_vars() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    torch_home = _torch_home_dir()
    try:
        os.makedirs(torch_home, exist_ok=True)
    except Exception:
        pass
    os.environ["TORCH_HOME"] = torch_home


def _blocked(name: str):
    def _raise(*args, **kwargs):
        addr = args[0] if args else kwargs.get("address")
        _log(f"BLOCKED {name}({addr!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.{name}({addr!r}). "
            f"This application must never access the network."
        )
    return _raise


def _guarded_connect(orig):
    def _connect(self, address, *a, **kw):
        if self.family == _AF_UNIX or _is_loopback_address(address):
            return orig(self, address, *a, **kw)
        _log(f"BLOCKED connect({address!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.connect({address!r}). "
            f"This application must never access the network."
        )
    return _connect


def _guarded_connect_ex(orig):
    def _connect_ex(self, address, *a, **kw):
        if self.family == _AF_UNIX or _is_loopback_address(address):
            return orig(self, address, *a, **kw)
        _log(f"BLOCKED connect_ex({address!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.connect_ex({address!r}). "
            f"This application must never access the network."
        )
    return _connect_ex


def _guarded_sendto(orig):
    # sendto(data, address) or sendto(data, flags, address): address is last.
    def _sendto(self, *args):
        address = args[-1] if len(args) >= 2 else None
        if self.family == _AF_UNIX or _is_loopback_address(address):
            return orig(self, *args)
        _log(f"BLOCKED sendto({address!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.sendto({address!r}). "
            f"This application must never access the network."
        )
    return _sendto


def _guarded_sendmsg(orig):
    # sendmsg(buffers[, ancdata[, flags[, address]]])
    def _sendmsg(self, *args, **kw):
        address = args[3] if len(args) >= 4 else kw.get("address")
        if self.family == _AF_UNIX or _is_loopback_address(address):
            return orig(self, *args, **kw)
        _log(f"BLOCKED sendmsg({address!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.sendmsg({address!r}). "
            f"This application must never access the network."
        )
    return _sendmsg


def _guarded_create_connection(orig):
    def _create_connection(address, *a, **kw):
        if _is_loopback_address(address):
            return orig(address, *a, **kw)
        _log(f"BLOCKED create_connection({address!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.create_connection({address!r}). "
            f"This application must never access the network."
        )
    return _create_connection


def _guarded_getaddrinfo(orig):
    def _getaddrinfo(host, port, *a, **kw):
        if _is_loopback_host(host):
            return orig(host, port, *a, **kw)
        _log(f"BLOCKED getaddrinfo({host!r}, {port!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.getaddrinfo({host!r}, {port!r}). "
            f"This application must never access the network."
        )
    return _getaddrinfo


def _guarded_gethostbyname(orig):
    def _gethostbyname(host):
        if _is_loopback_host(host):
            return orig(host)
        _log(f"BLOCKED gethostbyname({host!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.gethostbyname({host!r}). "
            f"This application must never access the network."
        )
    return _gethostbyname


def _guarded_gethostbyname_ex(orig):
    def _gethostbyname_ex(host):
        if _is_loopback_host(host):
            return orig(host)
        _log(f"BLOCKED gethostbyname_ex({host!r})")
        raise OfflineViolation(
            f"Blocked offline-only violation: socket.gethostbyname_ex({host!r}). "
            f"This application must never access the network."
        )
    return _gethostbyname_ex


def is_installed() -> bool:
    return _INSTALLED


def install() -> None:
    """Idempotent: calling this more than once is a no-op after the first
    call."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return

        _set_env_vars()

        _ORIGINALS["Socket.connect"] = socket.socket.connect
        _ORIGINALS["Socket.connect_ex"] = socket.socket.connect_ex
        _ORIGINALS["Socket.sendto"] = socket.socket.sendto
        _ORIGINALS["Socket.sendmsg"] = getattr(socket.socket, "sendmsg", None)
        _ORIGINALS["create_connection"] = socket.create_connection
        _ORIGINALS["getaddrinfo"] = socket.getaddrinfo
        _ORIGINALS["gethostbyname"] = socket.gethostbyname
        _ORIGINALS["gethostbyname_ex"] = socket.gethostbyname_ex

        socket.socket.connect = _guarded_connect(_ORIGINALS["Socket.connect"])
        socket.socket.connect_ex = _guarded_connect_ex(_ORIGINALS["Socket.connect_ex"])
        socket.socket.sendto = _guarded_sendto(_ORIGINALS["Socket.sendto"])
        if _ORIGINALS["Socket.sendmsg"] is not None:  # absent on Windows
            socket.socket.sendmsg = _guarded_sendmsg(_ORIGINALS["Socket.sendmsg"])
        socket.create_connection = _guarded_create_connection(_ORIGINALS["create_connection"])
        socket.getaddrinfo = _guarded_getaddrinfo(_ORIGINALS["getaddrinfo"])
        socket.gethostbyname = _guarded_gethostbyname(_ORIGINALS["gethostbyname"])
        socket.gethostbyname_ex = _guarded_gethostbyname_ex(_ORIGINALS["gethostbyname_ex"])

        _INSTALLED = True
        _log("offline_guard installed")


def uninstall() -> None:
    """Restore the original socket functions. Used by tests; not called in
    the shipped application."""
    global _INSTALLED
    with _LOCK:
        if not _INSTALLED:
            return
        socket.socket.connect = _ORIGINALS["Socket.connect"]
        socket.socket.connect_ex = _ORIGINALS["Socket.connect_ex"]
        socket.socket.sendto = _ORIGINALS["Socket.sendto"]
        if _ORIGINALS["Socket.sendmsg"] is not None:
            socket.socket.sendmsg = _ORIGINALS["Socket.sendmsg"]
        socket.create_connection = _ORIGINALS["create_connection"]
        socket.getaddrinfo = _ORIGINALS["getaddrinfo"]
        socket.gethostbyname = _ORIGINALS["gethostbyname"]
        socket.gethostbyname_ex = _ORIGINALS["gethostbyname_ex"]
        _ORIGINALS.clear()
        _INSTALLED = False
        _log("offline_guard uninstalled")
