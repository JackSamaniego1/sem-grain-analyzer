"""
Enforces HARD CONSTRAINT #1 (CLAUDE.md): the installed app must never use
the network and no loaded data may leave the machine.

Two layers are tested:
  1. Static — an AST scan of first-party source rejects imports/attribute
     accesses/URL string literals that could reach the network.
  2. Runtime — `core.offline_guard.install()` actually blocks real socket
     calls, leaves loopback traffic alone, and a full detect+export run
     makes zero network attempts.
"""
import ast
import re
import os
import socket
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import offline_guard
from core.offline_guard import OfflineViolation

# ---------------------------------------------------------------------------
# Static AST scan
# ---------------------------------------------------------------------------

# Directories to scan (relative to repo root). data/ and reports/ don't
# exist yet in this codebase revision but are scanned when present, per
# the v3 target architecture (handoff/00_START_HERE.md §4).
SCAN_DIRS = ["core", "ui", "utils", "data", "reports"]

EXCLUDE_DIR_NAMES = {".venv", "tests", "build", "dist", "scratch", "__pycache__"}

# Top-level runtime modules scanned in addition to SCAN_DIRS. The entry
# point ships in the installer, so it is part of the protected surface.
TOP_LEVEL_FILES = ["main.py", "version.py"]

# Build tooling (never shipped / never run on the work PC).
EXCLUDED_FILES = {"create_nsis_script.py"}

URL_ALLOWLIST_FILES = set()
_URL_RE = re.compile(r"https?://", re.IGNORECASE)

FORBIDDEN_IMPORT_MODULES = {
    "socket", "urllib", "http", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "webbrowser",
}
# Submodule roots that are also forbidden, e.g. "urllib.request".
FORBIDDEN_IMPORT_PREFIXES = tuple(FORBIDDEN_IMPORT_MODULES)

FORBIDDEN_QT_MODULES = {
    "PyQt6", "PyQt5",
}
# PySide6.QtNetwork / PySide6.QtWebEngine* are forbidden by full dotted name.


def _module_is_forbidden(dotted: str) -> bool:
    if not dotted:
        return False
    root = dotted.split(".")[0]
    if root in FORBIDDEN_IMPORT_MODULES:
        return True
    if root in FORBIDDEN_QT_MODULES:
        return True
    if dotted.startswith("PySide6.QtNetwork"):
        return True
    if dotted.startswith("PySide6.QtWebEngine"):
        return True
    return False


def _iter_first_party_files():
    for fn in TOP_LEVEL_FILES:
        full = os.path.join(ROOT, fn)
        if os.path.isfile(full):
            yield fn, full
    for scan_dir in SCAN_DIRS:
        base = os.path.join(ROOT, scan_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT)
                if os.path.basename(full) in EXCLUDED_FILES:
                    continue
                yield rel, full


def _check_file(rel_path: str, full_path: str):
    """Returns a list of violation strings for this file (empty if clean)."""
    with open(full_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=full_path)

    is_offline_guard_itself = rel_path.replace("\\", "/") == "core/offline_guard.py"
    url_allowlisted = rel_path in URL_ALLOWLIST_FILES or \
        rel_path.replace("/", os.sep) in URL_ALLOWLIST_FILES

    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "socket" and is_offline_guard_itself:
                    continue
                if _module_is_forbidden(name):
                    violations.append(
                        f"{rel_path}:{node.lineno}: forbidden import '{name}'")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "socket" and is_offline_guard_itself:
                continue
            if _module_is_forbidden(mod):
                violations.append(
                    f"{rel_path}:{node.lineno}: forbidden import-from '{mod}'")
        elif isinstance(node, ast.Attribute):
            # torch.hub.* and *.openUrl(...)
            if node.attr == "openUrl":
                violations.append(
                    f"{rel_path}:{node.lineno}: forbidden attribute access '.openUrl'")
            if node.attr == "hub" and isinstance(node.value, ast.Name) and node.value.id == "torch":
                violations.append(
                    f"{rel_path}:{node.lineno}: forbidden attribute access 'torch.hub'")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not url_allowlisted and _URL_RE.search(node.value):
                violations.append(
                    f"{rel_path}:{node.lineno}: forbidden URL literal {node.value!r}")

    return violations


def test_no_network_capable_code_in_first_party_source():
    all_violations = []
    scanned = set()
    for rel_path, full_path in _iter_first_party_files():
        scanned.add(rel_path.replace("\\", "/"))
        all_violations.extend(_check_file(rel_path, full_path))
    for must in ("main.py", "core/grain_detector.py", "core/offline_guard.py",
                 "ui/app_shell.py"):
        assert must in scanned, f"offline scan did not cover {must}"
    assert not all_violations, "Offline violations found:\n" + "\n".join(all_violations)


# ---------------------------------------------------------------------------
# Runtime behaviour
# ---------------------------------------------------------------------------

@pytest.fixture
def guard():
    offline_guard.install()
    yield
    offline_guard.uninstall()


def test_guard_is_idempotent():
    offline_guard.install()
    offline_guard.install()
    assert offline_guard.is_installed()
    offline_guard.uninstall()
    assert not offline_guard.is_installed()


def test_create_connection_to_remote_blocked(guard):
    with pytest.raises(OfflineViolation):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_getaddrinfo_remote_blocked(guard):
    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("example.com", 80)


def test_udp_sendto_remote_blocked(guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(OfflineViolation):
            s.sendto(b"x", ("8.8.8.8", 53))
        with pytest.raises(OfflineViolation):
            s.sendto(b"x", 0, ("8.8.8.8", 53))
    finally:
        s.close()


def test_udp_sendto_loopback_allowed(guard):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tx.sendto(b"ok", rx.getsockname())
        rx.settimeout(2)
        assert rx.recvfrom(16)[0] == b"ok"
    finally:
        tx.close(); rx.close()


def test_installer_adds_firewall_block_rules():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "create_nsis_script.py"), encoding="utf-8").read()
    assert 'dir=out action=block program="$INSTDIR' in src
    assert 'dir=in action=block program="$INSTDIR' in src
    assert src.count("firewall delete rule") >= 4  # clean re-install + uninstall


def test_loopback_server_client_still_works(guard):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    result = {}

    def _accept():
        conn, _addr = srv.accept()
        result["server_recv"] = conn.recv(5)
        conn.sendall(b"pong")
        conn.close()

    import threading
    t = threading.Thread(target=_accept)
    t.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    client.sendall(b"ping!")
    reply = client.recv(4)
    client.close()
    t.join(timeout=5)
    srv.close()

    assert result.get("server_recv") == b"ping!"
    assert reply == b"pong"


# ---------------------------------------------------------------------------
# Full pipeline makes zero network attempts
# ---------------------------------------------------------------------------

class _NetworkAttemptSpy:
    """Counts calls to the socket entry points the guard patches, by
    wrapping them a second time (outermost layer) purely for counting."""

    def __init__(self):
        self.count = 0
        self._originals = {}

    def __enter__(self):
        targets = [
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ]
        for obj, name in targets:
            orig = getattr(obj, name)
            self._originals[(obj, name)] = orig

            def make_wrapper(orig_fn):
                def wrapper(*a, **kw):
                    self.count += 1
                    return orig_fn(*a, **kw)
                return wrapper

            setattr(obj, name, make_wrapper(orig))
        return self

    def __exit__(self, exc_type, exc, tb):
        for (obj, name), orig in self._originals.items():
            setattr(obj, name, orig)


def test_full_analysis_and_export_makes_no_network_attempts(mosaic_bgr, tmp_path, guard):
    from core.grain_detector import GrainDetector, DetectionParams
    from utils.excel_export import export_to_excel

    with _NetworkAttemptSpy() as spy:
        detector = GrainDetector()
        params = DetectionParams(detection_mode="boundary")
        result = detector.analyze(mosaic_bgr, px_per_um=0.0, params=params)

        out_path = str(tmp_path / "r.xlsx")
        export_to_excel(result, "img.png", out_path, image_bgr=mosaic_bgr)

    assert spy.count == 0, f"expected zero network attempts, saw {spy.count}"
    assert os.path.isfile(out_path)
