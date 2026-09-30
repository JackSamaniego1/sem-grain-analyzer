"""
Background probe of the AI compute device (UPDATE 4 item 10b).

:func:`core.ai_device.ai_devices` imports PyTorch, which takes seconds, so it
is never called on the GUI thread.  :func:`device_probe` returns the one
process-wide :class:`AiDeviceProbe`; the Analyze page asks it to
:meth:`~AiDeviceProbe.start` and updates its "AI-Assisted (GPU)" entry when
:attr:`~AiDeviceProbe.ready` arrives.  Until then :meth:`~AiDeviceProbe.info`
is ``None`` ("still checking").  Offline: nothing here touches the network.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

GPU_PACK_HINT = ("To use an NVIDIA graphics card, run the Grain Analyzer installer again "
                 "and tick the optional GPU pack.")


def _probe(refresh: bool = False):
    """Worker thread: the (cached) device probe.  Looked up on the module at
    call time so tests can monkeypatch ``core.ai_device.ai_devices``."""
    from core import ai_device
    return ai_device.ai_devices(refresh=refresh)


class AiDeviceProbe(QObject):
    """Runs the device probe once on the thread pool and remembers the answer."""

    ready = Signal(object)            # core.ai_device.AiDeviceInfo

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._info = None
        self._running = False

    def info(self):
        """The last probe result, or ``None`` while it is still running /
        not started."""
        return self._info

    def is_running(self) -> bool:
        return self._running

    def start(self, again: bool = False) -> bool:
        """Probe in the background (once; ``again`` re-reads the cached core
        answer, e.g. after the graphics card failed during a run).  Returns
        True when a probe was started."""
        if self._running or (self._info is not None and not again):
            return False
        from ui.workers import run_task
        self._running = True
        run_task(_probe, on_done=self._done, on_error=self._failed)
        return True

    def set_info(self, info) -> None:
        """Deliver a probe result (also used by tests)."""
        self._running = False
        self._info = info
        self.ready.emit(info)

    def _done(self, info) -> None:
        self.set_info(info)

    def _failed(self, _msg: str) -> None:
        # ai_devices() never raises; this is belt and braces
        from core.ai_device import AiDeviceInfo
        self.set_info(AiDeviceInfo())


_PROBE: Optional[AiDeviceProbe] = None


def device_probe() -> AiDeviceProbe:
    global _PROBE
    if _PROBE is None:
        _PROBE = AiDeviceProbe()
    return _PROBE


def reset_device_probe() -> None:
    """Forget the probe (tests)."""
    global _PROBE
    _PROBE = None


def gpu_tooltip(info) -> str:
    """Plain-language tooltip of the GPU entry for a probe result."""
    if info is None:
        return "Checking whether this PC's graphics card can be used…"
    if getattr(info, "gpu_available", False):
        name = getattr(info, "gpu_name", "") or "NVIDIA graphics card"
        mb = int(getattr(info, "vram_total_mb", 0) or 0)
        mem = f" ({mb / 1024:.0f} GB)" if mb >= 1024 else ""
        return (f"Runs the AI model on the graphics card: {name}{mem}. Much faster than the "
                "CPU; results are the same.")
    reason = (getattr(info, "gpu_reason", "") or "The graphics card cannot be used.").strip()
    if not getattr(info, "torch_available", False):
        return reason
    if "no gpu support" in reason.lower():         # CPU-only install: the pack helps
        return f"{reason} {GPU_PACK_HINT}"
    return reason


__all__ = ["AiDeviceProbe", "device_probe", "reset_device_probe", "gpu_tooltip",
           "GPU_PACK_HINT"]
