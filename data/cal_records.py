"""
Calibration-verification records (INN-29) - standards, checks, lookup.

Workspace layout (``<workspace_root>/calibration/``)::

    standards.json   {"schema_version": 1, "standards": [CalibrationStandard...]}
                     rewritten atomically (temp file + os.replace)
    checks.jsonl     one CalibrationCheck per line, APPEND-ONLY - an
                     existing line is never rewritten (ISO/IEC 17025
                     sec. 6.4.13 equipment records must not be altered
                     after the fact; corrections are new checks)
    images/          copy of every verification image, named
                     ``<check id>_<sha256[:12]><ext>``

Lookup rule (what a session/report cites): the newest PASSED check for the
same instrument and magnification whose age is within the instrument's
``check_interval_days``. If a newer check for that instrument +
magnification FAILED, the scale is "not verified" (a later failure
invalidates an earlier pass). Otherwise ``status == "not verified"`` with a
human-readable reason. Never blocking - reports just print the status.

OPTIONAL FEATURE (user requirement): verification is opt-in. It is active
for an instrument only if ``AppSettings.calibration_verification_enabled``
is True (default False) or at least one check has been recorded for that
instrument. When inactive, every lookup returns ``status == "off"`` with no
reason/warnings, the status chip is empty and the report stamp is "" - no
"not verified" / "Cal due" nags, and nothing ever blocks analysis/export.

No Qt imports; no network.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Union

from data.models import (
    SCHEMA_VERSION, _generic_from_dict, _generic_to_dict, _json_default,
    default_operator, read_json, sha256_file, write_json_atomic,
)

CAL_DIRNAME = "calibration"
STANDARDS_FILE = "standards.json"
CHECKS_FILE = "checks.jsonl"
IMAGES_DIRNAME = "images"

DEFAULT_TOLERANCE_PCT = 2.0
DEFAULT_CHECK_INTERVAL_DAYS = 7
MAG_MATCH_REL = 0.005          # magnifications equal within 0.5 %

STATUS_VERIFIED = "verified"
STATUS_NOT_VERIFIED = "not verified"
STATUS_OFF = "off"               # feature not in use: show nothing
WARN_CERT_EXPIRED = "standard certificate expired"
WARN_LOW_CONFIDENCE = "automatic pitch measurement had low confidence"

STANDARD_TYPES = ("line grating", "square grid", "stage micrometer")


# ======================================================================
# Models
# ======================================================================

@dataclass
class CalibrationStandard:
    schema_version: int = SCHEMA_VERSION
    id: str = ""
    name: str = ""
    type: str = "line grating"
    certified_pitch_um: float = 0.0
    expanded_uncertainty_um: float = 0.0     # certificate U (k = 2)
    certificate_no: str = ""
    cert_expiry: str = ""                    # ISO date "YYYY-MM-DD"; "" = none
    notes: str = ""

    def is_expired(self, on: Optional[Union[date, datetime]] = None) -> bool:
        exp = _parse_date(self.cert_expiry)
        if exp is None:
            return False
        if on is None:
            on = date.today()
        if isinstance(on, datetime):
            on = on.astimezone().date() if on.tzinfo else on.date()
        return on > exp

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "CalibrationStandard":
        return _generic_from_dict(cls, d)


@dataclass
class InstrumentConfig:
    name: str = ""
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT
    check_interval_days: int = DEFAULT_CHECK_INTERVAL_DAYS

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "InstrumentConfig":
        return _generic_from_dict(cls, d)


@dataclass
class CalibrationCheck:
    schema_version: int = SCHEMA_VERSION
    id: str = ""
    datetime: str = ""                  # UTC ISO-8601
    operator: str = ""
    instrument: str = ""
    magnification: str = ""
    standard_id: str = ""
    standard_name: str = ""
    certificate_no: str = ""
    certified_pitch_um: float = 0.0
    cert_expanded_uncertainty_um: float = 0.0
    measured_pitch_um: float = 0.0
    px_per_um: float = 0.0
    error_pct: float = 0.0
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT
    passed: bool = False
    method: str = "fft"                 # "fft" | "manual"
    confidence: str = "high"
    expanded_uncertainty_um: float = 0.0
    uncertainty: dict = field(default_factory=dict)
    standard_expired: bool = False
    flags: List[str] = field(default_factory=list)
    image_sha256: str = ""
    image_copy_path: str = ""           # relative to the workspace root
    notes: str = ""

    def to_dict(self) -> dict:
        return _generic_to_dict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "CalibrationCheck":
        return _generic_from_dict(cls, d)

    def when(self) -> Optional[datetime]:
        return _parse_dt(self.datetime)


@dataclass
class CalibrationLookup:
    status: str = STATUS_OFF
    check: Optional[CalibrationCheck] = None
    reason: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def check_id(self) -> Optional[str]:
        return self.check.id if self.check is not None else None

    @property
    def verified(self) -> bool:
        return self.status == STATUS_VERIFIED


# ======================================================================
# Helpers
# ======================================================================

def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        dt = s
    else:
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_magnification(m) -> Optional[float]:
    """``"5000x"``, ``"5,000 X"``, ``"5 kx"``, ``5000`` -> 5000.0; else None."""
    if m is None:
        return None
    if isinstance(m, (int, float)):
        return float(m) if m > 0 else None
    s = str(m).strip().lower().replace(",", "").replace(" ", "").replace("×", "x")
    mo = re.match(r"^([0-9]*\.?[0-9]+)(k)?x?$", s)
    if not mo:
        return None
    v = float(mo.group(1)) * (1000.0 if mo.group(2) else 1.0)
    return v if v > 0 else None


def same_magnification(a, b) -> bool:
    va, vb = normalize_magnification(a), normalize_magnification(b)
    if va is None or vb is None:
        return False
    return abs(va - vb) <= MAG_MATCH_REL * max(va, vb)


def _same_instrument(a: str, b: str) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def instrument_config(instruments: Optional[Iterable], name: str) -> InstrumentConfig:
    """Find ``name`` in ``AppSettings.instruments`` (list of dicts or
    InstrumentConfig); defaults (+/-2 %, 7 days) if absent."""
    for it in instruments or []:
        cfg = it if isinstance(it, InstrumentConfig) else InstrumentConfig.from_dict(it)
        if _same_instrument(cfg.name, name):
            return cfg
    return InstrumentConfig(name=name)


def _fmt_pct(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s


def _local_date(dt: Optional[datetime]) -> str:
    return dt.astimezone().date().isoformat() if dt else "?"


# ======================================================================
# Store
# ======================================================================

class CalibrationStore:
    """Standards + append-only check log under ``<root>/calibration``."""

    def __init__(self, workspace_root: Union[str, Path], *, enabled: bool = False):
        """``enabled`` = ``AppSettings.calibration_verification_enabled``."""
        self.root = Path(workspace_root)
        self.enabled = bool(enabled)
        self.dir = self.root / CAL_DIRNAME
        self.standards_path = self.dir / STANDARDS_FILE
        self.checks_path = self.dir / CHECKS_FILE
        self.images_dir = self.dir / IMAGES_DIRNAME

    # ---- standards ---------------------------------------------------
    def load_standards(self) -> List[CalibrationStandard]:
        if not self.standards_path.exists():
            return []
        try:
            d = read_json(self.standards_path)
        except (OSError, ValueError):
            return []
        return [CalibrationStandard.from_dict(x) for x in d.get("standards", [])
                if isinstance(x, dict)]

    def save_standards(self, standards: List[CalibrationStandard]) -> None:
        write_json_atomic(self.standards_path, {
            "schema_version": SCHEMA_VERSION,
            "standards": [s.to_dict() for s in standards],
        })

    def get_standard(self, standard_id: str) -> Optional[CalibrationStandard]:
        for s in self.load_standards():
            if s.id == standard_id:
                return s
        return None

    def add_standard(self, std: CalibrationStandard) -> CalibrationStandard:
        if std.certified_pitch_um <= 0:
            raise ValueError("certified pitch must be > 0")
        items = self.load_standards()
        if not std.id:
            std.id = "std-" + uuid.uuid4().hex[:10]
        if any(s.id == std.id for s in items):
            raise ValueError(f"standard id {std.id!r} already exists")
        items.append(std)
        self.save_standards(items)
        return std

    def update_standard(self, standard_id: str, **fields_) -> CalibrationStandard:
        items = self.load_standards()
        for s in items:
            if s.id == standard_id:
                for k, v in fields_.items():
                    if k in ("id", "schema_version") or not hasattr(s, k):
                        continue
                    setattr(s, k, v)
                self.save_standards(items)
                return s
        raise KeyError(standard_id)

    def remove_standard(self, standard_id: str) -> None:
        """Removes the standard from the list. Past checks keep their copy
        of name / certificate / pitch, so the record stays self-contained."""
        items = [s for s in self.load_standards() if s.id != standard_id]
        self.save_standards(items)

    # ---- checks ------------------------------------------------------
    def load_checks(self) -> List[CalibrationCheck]:
        """All checks in file order. A torn last line (crash mid-append) or
        any unparsable line is skipped, never repaired in place."""
        if not self.checks_path.exists():
            return []
        out = []
        with open(self.checks_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(CalibrationCheck.from_dict(d))
        return out

    def _append_line(self, line: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = line.encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(self.checks_path, flags, 0o644)
        try:
            # a torn previous append must not glue onto this record
            size = os.fstat(fd).st_size
            if size > 0:
                with open(self.checks_path, "rb") as rf:
                    rf.seek(size - 1)
                    if rf.read(1) != b"\n":
                        data = b"\n" + data
            os.write(fd, data)       # single write of the whole record
            os.fsync(fd)
        finally:
            os.close(fd)

    def _copy_image(self, check: CalibrationCheck, image_path: Path) -> None:
        image_path = Path(image_path)
        digest = sha256_file(image_path)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        dest = self.images_dir / f"{check.id}_{digest[:12]}{image_path.suffix.lower()}"
        tmp = dest.with_name(f".{dest.name}.tmp{os.getpid()}")
        try:
            shutil.copy2(image_path, tmp)
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        check.image_sha256 = digest
        check.image_copy_path = dest.relative_to(self.root).as_posix()

    def record_check(self, check: CalibrationCheck,
                     image_path: Optional[Union[str, Path]] = None) -> CalibrationCheck:
        """Append ``check`` to ``checks.jsonl`` (never rewrites existing
        lines). Fills id / datetime / operator if empty, copies the image,
        snapshots the standard (name, certificate, U) and flags an expired
        certificate - the check is still saved."""
        if not check.id:
            check.id = "cal-" + _now_utc().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        if not check.datetime:
            check.datetime = _now_utc().isoformat(timespec="seconds")
        if not check.operator:
            check.operator = default_operator()
        std = self.get_standard(check.standard_id) if check.standard_id else None
        if std is not None:
            check.standard_name = check.standard_name or std.name
            check.certificate_no = check.certificate_no or std.certificate_no
            if not check.certified_pitch_um:
                check.certified_pitch_um = std.certified_pitch_um
            if not check.cert_expanded_uncertainty_um:
                check.cert_expanded_uncertainty_um = std.expanded_uncertainty_um
            if std.is_expired(check.when()):
                check.standard_expired = True
        if check.standard_expired and WARN_CERT_EXPIRED not in check.flags:
            check.flags.append(WARN_CERT_EXPIRED)
        if check.confidence == "low" and WARN_LOW_CONFIDENCE not in check.flags:
            check.flags.append(WARN_LOW_CONFIDENCE)
        if image_path is not None:
            self._copy_image(check, Path(image_path))
        line = json.dumps(check.to_dict(), default=_json_default,
                          separators=(",", ":"), ensure_ascii=False) + "\n"
        self._append_line(line)
        return check

    # ---- lookup ------------------------------------------------------
    def is_active(self, instrument: Optional[str] = None) -> bool:
        """Opt-in gate: enabled in settings, or at least one check recorded
        (for ``instrument`` if given, else for any instrument)."""
        if self.enabled:
            return True
        if instrument is None:
            return bool(self.load_checks())
        return bool(self.checks_for(instrument))

    def checks_for(self, instrument: str, magnification=None) -> List[CalibrationCheck]:
        """Checks for an instrument (optionally one magnification), newest first."""
        out = [c for c in self.load_checks() if _same_instrument(c.instrument, instrument)]
        if magnification is not None:
            out = [c for c in out if same_magnification(c.magnification, magnification)]
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        out.sort(key=lambda c: c.when() or epoch, reverse=True)
        return out

    def find_applicable_check(self, instrument: str, magnification, *,
                              now: Optional[datetime] = None,
                              interval_days: Optional[int] = None,
                              instruments: Optional[Iterable] = None) -> CalibrationLookup:
        """The check a session at ``instrument`` + ``magnification`` cites."""
        if not self.is_active(instrument if (instrument or "").strip() else None):
            return CalibrationLookup(status=STATUS_OFF)
        NV = STATUS_NOT_VERIFIED
        now = _parse_dt(now) if now is not None else _now_utc()
        if interval_days is None:
            interval_days = instrument_config(instruments, instrument).check_interval_days
        if not (instrument or "").strip():
            return CalibrationLookup(NV, reason="instrument not recorded")
        if normalize_magnification(magnification) is None:
            return CalibrationLookup(NV, reason="magnification not recorded")
        if not self.checks_for(instrument):
            return CalibrationLookup(NV, reason=f"no calibration check recorded for {instrument}")
        # checks from the future (clock skew > 1 day) are ignored
        cands = [c for c in self.checks_for(instrument, magnification)
                 if c.when() is not None and c.when() <= now + timedelta(days=1)]
        if not cands:
            return CalibrationLookup(
                NV, reason=f"no calibration check at {magnification} on {instrument}")
        newest = cands[0]
        passing = next((c for c in cands if c.passed), None)
        if not newest.passed and (passing is None or newest.when() > passing.when()):
            return CalibrationLookup(
                NV, reason=f"latest check {_local_date(newest.when())} failed "
                       f"(error {newest.error_pct:+.2f} %)")
        if passing is None:
            return CalibrationLookup(NV, reason="no passing calibration check")
        age = now - passing.when()
        if age > timedelta(days=interval_days):
            return CalibrationLookup(
                NV, reason=f"last passing check {_local_date(passing.when())} is older "
                       f"than {interval_days} days")
        warnings = []
        if passing.standard_expired:
            warnings.append(WARN_CERT_EXPIRED)
        if passing.confidence == "low" and passing.method == "fft":
            warnings.append(WARN_LOW_CONFIDENCE)
        return CalibrationLookup(status=STATUS_VERIFIED, check=passing, warnings=warnings)

    def instrument_status(self, instrument: str, *, now: Optional[datetime] = None,
                          interval_days: Optional[int] = None,
                          instruments: Optional[Iterable] = None) -> dict:
        """Status-bar chip for the current instrument (any magnification).

        ``state``: ``"ok"`` (newest check passed, within interval),
        ``"due"`` (none, or older than the interval), ``"failed"`` (newest
        check failed), ``"off"`` (feature not in use - show no chip).
        ``text`` is ready to show."""
        if not self.is_active(instrument):
            return {"state": "off", "days_ago": None, "check": None, "text": ""}
        now = _parse_dt(now) if now is not None else _now_utc()
        if interval_days is None:
            interval_days = instrument_config(instruments, instrument).check_interval_days
        checks = [c for c in self.checks_for(instrument) if c.when() is not None]
        if not checks:
            return {"state": "due", "days_ago": None, "check": None, "text": "Cal due"}
        c = checks[0]
        days = max(0, (now - c.when()).days)
        if not c.passed:
            return {"state": "failed", "days_ago": days, "check": c, "text": "Cal failed"}
        if (now - c.when()) > timedelta(days=interval_days):
            return {"state": "due", "days_ago": days, "check": c, "text": "Cal due"}
        return {"state": "ok", "days_ago": days, "check": c,
                "text": f"Cal ✔ {days} d ago"}


# ======================================================================
# Building checks, report/session integration
# ======================================================================

def build_check(*, standard: CalibrationStandard, instrument: str, magnification,
                measured_pitch_um: float, tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
                method: str = "fft", px_per_um: float = 0.0, confidence: str = "high",
                repeatability_um: float = 0.0, span_px: float = 0.0,
                operator: str = "", notes: str = "") -> CalibrationCheck:
    """Assemble a check (error %, PASS/FAIL, uncertainty) from a measurement;
    pass it to :meth:`CalibrationStore.record_check` to save."""
    from core.cal_verify import evaluate
    v = evaluate(measured_pitch_um, standard.certified_pitch_um, tolerance_pct=tolerance_pct,
                 cert_expanded_um=standard.expanded_uncertainty_um,
                 repeatability_um=repeatability_um, span_px=span_px, method=method)
    return CalibrationCheck(
        operator=operator, instrument=instrument, magnification=str(magnification),
        standard_id=standard.id, standard_name=standard.name,
        certificate_no=standard.certificate_no,
        certified_pitch_um=standard.certified_pitch_um,
        cert_expanded_uncertainty_um=standard.expanded_uncertainty_um,
        measured_pitch_um=v.measured_pitch_um, px_per_um=float(px_per_um or 0.0),
        error_pct=v.error_pct, tolerance_pct=v.tolerance_pct, passed=v.passed,
        method=method, confidence=confidence,
        expanded_uncertainty_um=v.uncertainty.get("expanded_um", 0.0),
        uncertainty=v.uncertainty, notes=notes)


def stamp_text(lookup: CalibrationLookup) -> str:
    """One-line report stamp, e.g. ``Scale verified 2026-09-21, error +0.6 %
    (limit ±2 %), standard SN 1234``. ``""`` when the feature is off."""
    if lookup.status == STATUS_OFF:
        return ""
    if not lookup.verified or lookup.check is None:
        return f"Scale not verified ({lookup.reason})" if lookup.reason else "Scale not verified"
    c = lookup.check
    txt = (f"Scale verified {_local_date(c.when())}, error {c.error_pct:+.1f} % "
           f"(limit ±{_fmt_pct(c.tolerance_pct)} %)")
    if c.certificate_no:
        txt += f", standard SN {c.certificate_no}"
    elif c.standard_name:
        txt += f", standard {c.standard_name}"
    if lookup.warnings:
        txt += " - " + "; ".join(lookup.warnings)
    return txt


def report_calibration(lookup: CalibrationLookup, *, source: str = "metadata",
                       px_per_um: float = 0.0) -> dict:
    """Payload for ``ReportModel.calibration`` (INN-29 spec)."""
    return {
        "source": source,                 # metadata | manual | scale-bar
        "px_per_um": float(px_per_um or 0.0),
        "check": lookup.check.to_dict() if lookup.check is not None else None,
        "status": lookup.status,
        "reason": lookup.reason,
        "warnings": list(lookup.warnings),
        "text": stamp_text(lookup),
    }


def apply_to_session(session_meta, lookup: CalibrationLookup) -> None:
    """Record the cited check on a ``SessionMeta`` (fields added by INN-29)."""
    session_meta.calibration_check_id = lookup.check_id
    session_meta.calibration_status = lookup.status
    if lookup.status == STATUS_OFF:
        session_meta.calibration_check_id = None
        session_meta.calibration_status = ""
        session_meta.calibration_reason = ""
        return
    session_meta.calibration_reason = lookup.reason if not lookup.verified else "; ".join(lookup.warnings)
