"""
Compute-device probe and selection for AI-assisted (SAM) detection
(UPDATE 4 item 10b).

The UI offers "AI-Assisted (GPU)" and "AI-Assisted (CPU)".  This module
answers, without Qt and without ever raising:

* :func:`ai_devices` -- is the AI component (PyTorch) importable, is an
  NVIDIA GPU *actually usable* (``torch.cuda.is_available()`` AND a tiny
  tensor op on the card succeeds, which catches driver/DLL mismatches and
  cards too old for the bundled CUDA build), the card name, VRAM total/free
  and -- when the GPU cannot be used -- a plain-language reason for the
  tooltip of the greyed-out GPU choice.
* :func:`resolve_device` -- maps the user's choice ``"auto" | "gpu" |
  "cpu"`` to the torch device string (``"cuda"`` / ``"cpu"``).  An explicit
  ``"gpu"`` that cannot be honoured raises :class:`GpuUnavailableError`
  (whose ``str()`` is the user-facing message); it is never silently run on
  the CPU.  ``"auto"`` uses the GPU when usable, otherwise the CPU.
* :func:`is_cuda_oom` / :func:`release_gpu_memory` -- used by the detector's
  out-of-memory fallback (that image is re-run on the CPU and flagged).

``torch`` is imported lazily (it takes seconds) and the probe result is
cached, so :func:`ai_devices` is cheap after the first call and safe to
call from a worker thread.  Offline (D-14): nothing here touches the
network.
"""
from __future__ import annotations

import threading
import warnings
from dataclasses import asdict, dataclass, replace
from typing import Optional

# ----------------------------------------------------------------------
# User-facing reasons (lab language, no code terms)
# ----------------------------------------------------------------------
REASON_NO_TORCH = ("The AI component is missing from this installation. "
                   "Please reinstall SEM Grain Analyzer.")
REASON_NO_GPU_BUILD = "This installation has no GPU support."
REASON_NO_CARD = "No NVIDIA graphics card found."
REASON_DRIVER_OLD = ("Graphics driver too old. Ask IT to update the NVIDIA "
                     "graphics driver.")
REASON_CARD_TOO_OLD = ("This graphics card is too old for the AI component. "
                       "Use the CPU instead.")
REASON_GPU_FAILED = ("The graphics card could not be used for AI detection "
                     "(self-test failed). Use the CPU instead.")

NOTE_OOM_FALLBACK = ("The graphics card ran out of memory on this image, so "
                     "it was processed on the CPU instead. Results are "
                     "equivalent; it only took longer.")
NOTE_GPU_ERROR_FALLBACK = ("The graphics card reported an error on this "
                           "image, so it was processed on the CPU instead.")

DEVICE_CHOICES = ("auto", "gpu", "cpu")


class GpuUnavailableError(RuntimeError):
    """Raised when ``device="gpu"`` was requested but no usable GPU exists.

    ``str(exc)`` is a complete user-facing sentence; ``exc.reason`` is the
    short reason from :class:`AiDeviceInfo` (e.g. "Graphics driver too
    old. ...")."""

    def __init__(self, reason: str):
        self.reason = reason or REASON_GPU_FAILED
        super().__init__(
            "AI-assisted detection on the GPU is not available: "
            f"{self.reason} Choose AI-Assisted (CPU) instead.")


class AiUnavailableError(RuntimeError):
    """The AI component (PyTorch) cannot be imported at all."""

    def __init__(self, reason: str = REASON_NO_TORCH):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AiDeviceInfo:
    """What the UI needs to offer / grey out the AI device choices.

    ``gpu_reason`` is empty when ``gpu_available`` is True, otherwise a
    plain-language sentence.  VRAM figures are MiB (0 when unknown)."""
    torch_available: bool = False
    cpu_available: bool = False
    gpu_available: bool = False
    gpu_name: str = ""
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    gpu_reason: str = REASON_NO_TORCH
    torch_version: str = ""
    cuda_version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Probe
# ----------------------------------------------------------------------
_LOCK = threading.Lock()
_CACHE: Optional[AiDeviceInfo] = None


def _import_torch():
    """Import torch lazily (monkeypatched in tests). Any failure -- missing
    package, broken DLLs (``OSError``) -- is reported as ``None``."""
    try:
        import torch  # noqa: WPS433 (lazy on purpose: slow import)
    except Exception:  # ImportError, OSError (DLL load failed), ...
        return None
    try:                   # item 7: cap torch threads the moment it loads
        from core.perf import apply_torch_threads
        apply_torch_threads(torch)
    except Exception:
        pass
    return torch


def _reason_from_text(text: str) -> str:
    t = (text or "").lower()
    if ("too old" in t or "insufficient" in t
            or "driver version" in t or "cuda driver version" in t):
        return REASON_DRIVER_OLD
    if "no kernel image" in t or "sm_" in t or "capability" in t:
        return REASON_CARD_TOO_OLD
    if "no nvidia driver" in t or "no cuda gpus" in t or "no cuda-capable" in t:
        return REASON_NO_CARD
    return ""


def _probe(torch) -> AiDeviceInfo:
    if torch is None:
        return AiDeviceInfo()
    base = AiDeviceInfo(
        torch_available=True, cpu_available=True, gpu_available=False,
        torch_version=str(getattr(torch, "__version__", "")),
        cuda_version=str(getattr(getattr(torch, "version", None), "cuda", "") or ""),
        gpu_reason=REASON_NO_GPU_BUILD)
    if not base.cuda_version:
        return base                                   # CPU-only build of torch
    cuda = torch.cuda
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            ok = bool(cuda.is_available())
            err_text = ""
        except Exception as exc:
            ok, err_text = False, str(exc)
    warn_text = " ".join(str(w.message) for w in caught)
    if not ok:
        reason = _reason_from_text(err_text + " " + warn_text)
        if not reason:
            try:
                count = int(cuda.device_count())
            except Exception:
                count = 0
            reason = REASON_NO_CARD if count == 0 else REASON_GPU_FAILED
        return replace(base, gpu_reason=reason)
    # is_available() can be True while kernels cannot launch (driver/DLL
    # mismatch, compute capability not in this build): run a tiny op.
    try:
        x = torch.ones(8, device="cuda")
        val = float((x * 2).sum().item())
        cuda.synchronize()
        if abs(val - 16.0) > 1e-6:
            raise RuntimeError(f"self-test returned {val}")
    except Exception as exc:
        return replace(base, gpu_reason=_reason_from_text(str(exc)) or REASON_GPU_FAILED)
    name = ""
    total = free = 0
    try:
        name = str(cuda.get_device_name(0))
    except Exception:
        pass
    try:
        free_b, total_b = cuda.mem_get_info(0)
        free, total = int(free_b) // (1024 * 1024), int(total_b) // (1024 * 1024)
    except Exception:
        try:
            total = int(cuda.get_device_properties(0).total_memory) // (1024 * 1024)
        except Exception:
            pass
    return replace(base, gpu_available=True, gpu_reason="", gpu_name=name,
                   vram_total_mb=total, vram_free_mb=free)


def _fresh_free_mb(torch, info: AiDeviceInfo) -> AiDeviceInfo:
    try:
        free_b, total_b = torch.cuda.mem_get_info(0)
        return replace(info, vram_free_mb=int(free_b) // (1024 * 1024),
                       vram_total_mb=int(total_b) // (1024 * 1024))
    except Exception:
        return info


def ai_devices(refresh: bool = False) -> AiDeviceInfo:
    """Return the (cached) :class:`AiDeviceInfo`.  Never raises.

    The first call imports torch (slow -- call it from a worker thread);
    later calls are cheap.  When a GPU is usable the free-VRAM figure is
    re-read on every call.  ``refresh=True`` re-runs the whole probe."""
    global _CACHE, _GPU_FAILED_REASON
    try:
        with _LOCK:
            if _CACHE is None or refresh:
                _GPU_FAILED_REASON = ""
                _CACHE = _probe(_import_torch())
            info = _CACHE
            if _GPU_FAILED_REASON and info.gpu_available:
                return replace(info, gpu_available=False, vram_free_mb=0,
                               gpu_reason=_GPU_FAILED_REASON)
        if info.gpu_available:
            torch = _import_torch()
            if torch is not None:
                info = _fresh_free_mb(torch, info)
        return info
    except Exception as exc:  # the probe must never take the app down
        return AiDeviceInfo(gpu_reason=f"{REASON_GPU_FAILED} ({exc})")


def clear_cache() -> None:
    """Forget the probe result (tests; after a driver update)."""
    global _CACHE, _GPU_FAILED_REASON
    with _LOCK:
        _CACHE = None
        _GPU_FAILED_REASON = ""


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------
def normalize_device_choice(choice) -> str:
    """``"auto" | "gpu" | "cpu"``; accepts ``"cuda"``/``"GPU"``/None/"".
    Unknown values mean ``"auto"``."""
    c = str(choice or "auto").strip().lower()
    if c in ("gpu", "cuda", "nvidia"):
        return "gpu"
    if c == "cpu":
        return "cpu"
    return "auto"


def resolve_device(choice="auto", info: Optional[AiDeviceInfo] = None) -> str:
    """Torch device string (``"cuda"`` or ``"cpu"``) for the user's choice.

    * ``"cpu"``  -> ``"cpu"``.
    * ``"gpu"``  -> ``"cuda"``, or :class:`GpuUnavailableError` when the GPU
      is not usable (no silent CPU fallback for an explicit GPU request).
    * ``"auto"`` -> ``"cuda"`` if usable, else ``"cpu"`` (v3.0 behaviour).

    Raises :class:`AiUnavailableError` when torch is missing entirely."""
    info = info or ai_devices()
    if not info.torch_available:
        raise AiUnavailableError()
    c = normalize_device_choice(choice)
    if c == "cpu":
        return "cpu"
    if info.gpu_available:
        return "cuda"
    if c == "gpu":
        raise GpuUnavailableError(info.gpu_reason)
    return "cpu"


# ----------------------------------------------------------------------
# GPU robustness helpers
# ----------------------------------------------------------------------
def is_cuda_oom(exc: BaseException) -> bool:
    """True for a CUDA out-of-memory error (``torch.cuda.OutOfMemoryError``
    or the older ``RuntimeError("CUDA out of memory ...")``)."""
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    if not isinstance(exc, RuntimeError):
        return False
    t = str(exc).lower()
    return "out of memory" in t and ("cuda" in t or "cublas" in t or "cudnn" in t)


_CUDA_ERROR_MARKERS = ("cuda error", "cuda runtime error", "cuda driver error",
                       "cudnn error", "cudnn_status", "cublas error",
                       "cublas_status", "cuda kernel errors")


def is_cuda_error(exc: BaseException) -> bool:
    """True for a CUDA/cuDNN/cuBLAS runtime failure raised during inference.

    Only ``RuntimeError`` subclasses (torch raises ``RuntimeError``,
    ``torch.cuda.CudaError`` / ``AcceleratorError`` for these) carrying a
    CUDA/cuDNN/cuBLAS *error* message qualify.  Anything else -- e.g.
    ``AssertionError("Torch not compiled with CUDA enabled")`` or a
    ValueError mentioning "cuda" -- is a real bug and must be raised, not
    silently re-run on the CPU."""
    if not isinstance(exc, RuntimeError):
        return False
    t = str(exc).lower()
    return any(k in t for k in _CUDA_ERROR_MARKERS)


REASON_GPU_ERROR_STICKY = ("The graphics card reported an error; using the CPU "
                           "until the app is restarted.")
_GPU_FAILED_REASON = ""


def mark_gpu_failed(reason: str = REASON_GPU_ERROR_STICKY) -> None:
    """Declare the GPU unusable for the rest of the process (after a
    non-memory CUDA error the CUDA context is usually broken).  From now on
    :func:`ai_devices` reports ``gpu_available=False`` with ``reason``, so
    "auto" goes straight to the CPU and "gpu" raises GpuUnavailableError.
    ``ai_devices(refresh=True)`` clears the mark and re-probes."""
    global _GPU_FAILED_REASON
    with _LOCK:
        _GPU_FAILED_REASON = reason or REASON_GPU_ERROR_STICKY


def release_gpu_memory() -> None:
    """Free cached CUDA blocks after an error. Never raises; no-op without
    torch/CUDA."""
    try:
        # No gc.collect() here: this runs on the analysis thread, and a
        # cycle collection there can destroy Qt objects that belong to the
        # UI thread (see ui/gc_guard.py).  empty_cache() is enough.
        torch = _import_torch()
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def configure_cuda_determinism(torch) -> None:
    """Make GPU results match the CPU up to float noise: full FP32 matmul /
    convolution (no TF32 -- it keeps only 10 mantissa bits and can flip
    masks whose IoU/stability score sits near a threshold) and no cuDNN
    autotuning (algorithm choice varies run to run).  No half precision or
    autocast is used anywhere in the SAM path."""
    try:
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
    except Exception:
        pass


__all__ = [
    "AiDeviceInfo", "ai_devices", "clear_cache", "resolve_device",
    "normalize_device_choice", "GpuUnavailableError", "AiUnavailableError",
    "is_cuda_oom", "is_cuda_error", "release_gpu_memory", "mark_gpu_failed",
    "REASON_GPU_ERROR_STICKY",
    "configure_cuda_determinism", "DEVICE_CHOICES",
    "NOTE_OOM_FALLBACK", "NOTE_GPU_ERROR_FALLBACK",
]
