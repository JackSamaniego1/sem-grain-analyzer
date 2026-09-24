"""
INN-02: specification limits and the ILAC-G8:09/2019 conformity decision
rule (PASS / FAIL / INCONCLUSIVE).

A :class:`Spec` (name, revision, decision rule, a list of :class:`Rule`
limits) attaches to a project, optionally restricted to specific samples
(``applies_to.sample_ids``). :func:`evaluate` compares a lot's
``core.metrics.SampleStatistics`` against a spec and returns a
:class:`Verdict`.

No Qt imports (CLAUDE.md: core/, data/, reports/ stay Qt-free). Pure
Python + dataclasses; storage is plain dicts inside ``project.json``
(``ProjectMeta.specs``, see ``data/models.py``) so this module owns the
shape of that list without ``data.models`` needing to import it.

References: ISO/IEC 17025:2017 §7.8.6 (statements of conformity must state
the decision rule); ILAC-G8:09/2019 (simple vs. guarded acceptance).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

SCHEMA_VERSION = 1

DECISION_RULES = ("guarded", "simple")
DEFAULT_DECISION_RULE = "guarded"

# Overall / per-rule verdict strings (UI badge colours: pass=green,
# fail=red, inconclusive=amber, no_spec=grey).
PASS, FAIL, INCONCLUSIVE, NO_SPEC = "pass", "fail", "inconclusive", "no_spec"

# metric -> evaluation kind:
#   "ci"       -- has a mean and (for guarded acceptance) a 95 % CI; PASS if
#                 the whole CI lies within the limits, FAIL if the whole CI
#                 lies outside, else INCONCLUSIVE (simple rule: mean only).
#   "limit"    -- plain mean-vs-limit comparison, no CI concept (PASS/FAIL
#                 only; INCONCLUSIVE only if the value is unavailable).
#   "adequacy" -- a sample-size/coverage count: below the limit means the
#                 data is not yet sufficient to decide, not that the lot
#                 fails, so a violation is INCONCLUSIVE, never FAIL.
_METRIC_KINDS: Dict[str, str] = {
    "G_mean": "ci",
    "ecd_mean_um": "ci",
    "ala_G": "ci",  # INN-33 not landed yet: value simply reads as unavailable until it is
    "RA_pct": "limit",
    "invalid_area_pct_max": "limit",
    "n_fields": "adequacy",
}

_METRIC_LABELS: Dict[str, str] = {
    "G_mean": "G",
    "ecd_mean_um": "ECD",
    "ala_G": "ALA G",
    "RA_pct": "%RA",
    "invalid_area_pct_max": "invalid area %",
    "n_fields": "fields",
}

_STATEMENTS: Dict[str, str] = {
    "guarded": ("Conformity decided by guarded acceptance: the 95 % confidence "
                "interval must lie within the specification (ILAC-G8:09/2019)."),
    "simple": ("Conformity decided by simple acceptance: the mean value must "
               "lie within the specification (ILAC-G8:09/2019)."),
}


class SpecError(ValueError):
    """An invalid spec definition (e.g. a rule naming an unknown metric)."""


def known_metrics() -> List[str]:
    """Metric names usable in a :class:`Rule` (for a UI dropdown)."""
    return sorted(_METRIC_KINDS)


def _label(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric)


def _num(v: Any) -> str:
    """``5`` not ``5.0``, but ``7.4`` stays ``7.4`` -- for reason strings."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


# ======================================================================
# Storage: Rule / Spec
# ======================================================================

@dataclass
class Rule:
    metric: str = ""
    lower: Optional[float] = None
    upper: Optional[float] = None
    unit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Rule":
        d = d or {}
        lower, upper = d.get("lower"), d.get("upper")
        return cls(
            metric=str(d.get("metric", "")),
            lower=float(lower) if lower is not None else None,
            upper=float(upper) if upper is not None else None,
            unit=str(d.get("unit", "")),
        )

    def problem(self) -> Optional[str]:
        """``None`` if this rule is well-formed, else a human message."""
        if not self.metric:
            return "Rule has no metric."
        if self.metric not in _METRIC_KINDS:
            return (f"Unknown metric {self.metric!r}. Known metrics: "
                    f"{', '.join(known_metrics())}.")
        if self.lower is None and self.upper is None:
            return f"Rule for {self.metric!r} has neither a lower nor an upper limit."
        return None


@dataclass
class Spec:
    schema_version: int = SCHEMA_VERSION
    id: str = ""
    name: str = ""
    revision: str = ""
    decision_rule: str = DEFAULT_DECISION_RULE
    rules: List[Rule] = field(default_factory=list)
    # {"sample_ids": [...]}; empty/omitted -> applies to every sample in the project.
    applies_to: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "revision": self.revision,
            "decision_rule": self.decision_rule,
            "rules": [r.to_dict() for r in self.rules],
            "applies_to": dict(self.applies_to or {}),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Spec":
        d = d or {}
        return cls(
            schema_version=int(d.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            revision=str(d.get("revision", "")),
            decision_rule=str(d.get("decision_rule") or DEFAULT_DECISION_RULE),
            rules=[Rule.from_dict(r) for r in (d.get("rules") or [])],
            applies_to=dict(d.get("applies_to") or {}),
        )

    def applies_to_sample(self, sample_id: str) -> bool:
        ids = (self.applies_to or {}).get("sample_ids")
        return True if not ids else sample_id in ids

    def problems(self) -> List[str]:
        """Validation problems (empty list = OK). Non-raising, for a UI
        editor to show inline; :func:`evaluate` raises :class:`SpecError`
        instead since it cannot compute a verdict past this point."""
        out = []
        if not self.name:
            out.append("Spec has no name.")
        if self.decision_rule not in DECISION_RULES:
            out.append(f"decision_rule must be one of {DECISION_RULES}, got {self.decision_rule!r}.")
        if not self.rules:
            out.append("Spec has no rules.")
        out.extend(p for p in (r.problem() for r in self.rules) if p)
        return out


def select_spec(specs: Sequence[Spec], sample_id: str = "") -> Optional[Spec]:
    """The spec that applies to ``sample_id``: a sample-specific override
    (``applies_to.sample_ids`` containing it) beats a project-wide spec
    (no ``sample_ids`` restriction); the last one defined wins within each
    tier so a newer override replaces an older one. ``None`` if no spec in
    ``specs`` applies."""
    specific = [s for s in specs if (s.applies_to or {}).get("sample_ids")
                and sample_id in s.applies_to["sample_ids"]]
    if specific:
        return specific[-1]
    generic = [s for s in specs if not (s.applies_to or {}).get("sample_ids")]
    return generic[-1] if generic else None


def specs_from_project_dict(d: Optional[dict]) -> List[Spec]:
    """``project.json``'s ``specs`` list -> ``[Spec, ...]``."""
    return [Spec.from_dict(s) for s in (d or {}).get("specs") or []]


def specs_to_project_dict(d: Optional[dict], specs: Sequence[Spec]) -> dict:
    """A copy of project dict ``d`` with ``specs`` replaced -- write the
    result back with ``data.workspace.Workspace.update_project_meta`` or
    ``data.models.write_json_atomic``."""
    out = dict(d or {})
    out["specs"] = [s.to_dict() for s in specs]
    return out


# ======================================================================
# Evaluation
# ======================================================================

@dataclass
class RuleResult:
    metric: str = ""
    value: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    unit: str = ""
    status: str = INCONCLUSIVE
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RuleResult":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in valid})


@dataclass
class Verdict:
    overall: str = NO_SPEC
    spec_id: str = ""
    spec_name: str = ""
    spec_revision: str = ""
    decision_rule: str = ""
    rules: List[RuleResult] = field(default_factory=list)
    statement: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rules"] = [r.to_dict() for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Verdict":
        d = d or {}
        return cls(
            overall=d.get("overall", NO_SPEC),
            spec_id=d.get("spec_id", ""),
            spec_name=d.get("spec_name", ""),
            spec_revision=d.get("spec_revision", ""),
            decision_rule=d.get("decision_rule", ""),
            rules=[RuleResult.from_dict(r) for r in (d.get("rules") or [])],
            statement=d.get("statement", ""),
        )

    def rule_hover(self, metric: str) -> str:
        for r in self.rules:
            if r.metric == metric:
                return r.text
        return ""


def _get(stats: Any, name: str) -> Any:
    if stats is None:
        return None
    if isinstance(stats, dict):
        return stats.get(name)
    return getattr(stats, name, None)


def _limits_text(lower: Optional[float], upper: Optional[float], unit: str = "") -> str:
    u = f" {unit}" if unit else ""
    if lower is not None and upper is not None:
        return f"{_num(lower)}–{_num(upper)}{u}"
    if lower is not None:
        return f"≥ {_num(lower)}{u}"
    return f"≤ {_num(upper)}{u}"


def _within(lo: float, hi: float, lower: Optional[float], upper: Optional[float]) -> bool:
    if lower is not None and lo < lower:
        return False
    if upper is not None and hi > upper:
        return False
    return True


def _outside(lo: float, hi: float, lower: Optional[float], upper: Optional[float]) -> bool:
    if lower is not None and hi < lower:
        return True
    if upper is not None and lo > upper:
        return True
    return False


def _ci_metric_values(metric: str, stats: Any):
    """(value, ci_low, ci_high) for a "ci"-kind metric; ``ci_low``/``high``
    are ``None`` when no CI is available (fewer than 2 fields)."""
    if metric == "G_mean":
        return _get(stats, "G_mean"), _get(stats, "G_ci_low"), _get(stats, "G_ci_high")
    if metric == "ecd_mean_um":
        mean, ci95 = _get(stats, "ecd_mean_um"), _get(stats, "ecd_ci95_um")
        if mean is None:
            return None, None, None
        if ci95 is None:
            return mean, None, None
        return mean, mean - ci95, mean + ci95
    # ala_G (INN-33): read if present, no dedicated CI fields defined yet.
    return _get(stats, metric), _get(stats, f"{metric}_ci_low"), _get(stats, f"{metric}_ci_high")


def _evaluate_ci_rule(rule: Rule, decision_rule: str, stats: Any) -> RuleResult:
    value, ci_low, ci_high = _ci_metric_values(rule.metric, stats)
    label, lim = _label(rule.metric), _limits_text(rule.lower, rule.upper, rule.unit)
    if value is None:
        return RuleResult(metric=rule.metric, lower=rule.lower, upper=rule.upper, unit=rule.unit,
                           status=INCONCLUSIVE, text=f"{label} not available")

    use_simple = decision_rule == "simple" or ci_low is None or ci_high is None
    if use_simple:
        ok = not ((rule.lower is not None and value < rule.lower)
                  or (rule.upper is not None and value > rule.upper))
        status = PASS if ok else FAIL
        text = f"{label} {value:.2f} {'within' if ok else 'outside'} {lim}"
        return RuleResult(metric=rule.metric, value=value, lower=rule.lower, upper=rule.upper,
                           unit=rule.unit, status=status, text=text)

    if _within(ci_low, ci_high, rule.lower, rule.upper):
        status = PASS
    elif _outside(ci_low, ci_high, rule.lower, rule.upper):
        status = FAIL
    else:
        status = INCONCLUSIVE
    verb = {"pass": "within", "fail": "outside", "inconclusive": "straddling"}[status]
    mark = " ✔" if status == PASS else ""
    text = f"{label} {value:.2f} [{ci_low:.2f}–{ci_high:.2f}] {verb} {lim}{mark}"
    return RuleResult(metric=rule.metric, value=value, ci_low=ci_low, ci_high=ci_high,
                       lower=rule.lower, upper=rule.upper, unit=rule.unit,
                       status=status, text=text)


def _evaluate_limit_rule(rule: Rule, stats: Any) -> RuleResult:
    value = _get(stats, rule.metric)
    label, lim = _label(rule.metric), _limits_text(rule.lower, rule.upper, rule.unit)
    if value is None:
        return RuleResult(metric=rule.metric, lower=rule.lower, upper=rule.upper, unit=rule.unit,
                           status=INCONCLUSIVE, text=f"{label} not available")
    ok = not ((rule.lower is not None and value < rule.lower)
              or (rule.upper is not None and value > rule.upper))
    status = PASS if ok else FAIL
    text = f"{label} {_num(value)} {'within' if ok else 'outside'} {lim}"
    return RuleResult(metric=rule.metric, value=value, lower=rule.lower, upper=rule.upper,
                       unit=rule.unit, status=status, text=text)


def _evaluate_adequacy_rule(rule: Rule, stats: Any) -> RuleResult:
    value = _get(stats, rule.metric)
    label = _label(rule.metric)
    if value is None:
        return RuleResult(metric=rule.metric, lower=rule.lower, upper=rule.upper, unit=rule.unit,
                           status=INCONCLUSIVE, text=f"{label} not available")
    reasons = []
    if rule.lower is not None and value < rule.lower:
        reasons.append(f"{label} {_num(value)} < {_num(rule.lower)}")
    if rule.upper is not None and value > rule.upper:
        reasons.append(f"{label} {_num(value)} > {_num(rule.upper)}")
    if reasons:
        return RuleResult(metric=rule.metric, value=value, lower=rule.lower, upper=rule.upper,
                           unit=rule.unit, status=INCONCLUSIVE, text="; ".join(reasons))
    lim = _limits_text(rule.lower, rule.upper, rule.unit)
    return RuleResult(metric=rule.metric, value=value, lower=rule.lower, upper=rule.upper,
                       unit=rule.unit, status=PASS, text=f"{label} {_num(value)} meets {lim}")


def evaluate(spec: Optional[Spec], sample_stats: Any) -> Verdict:
    """PASS / FAIL / INCONCLUSIVE verdict of ``sample_stats`` (a
    ``core.metrics.SampleStatistics`` or an equivalent dict) against
    ``spec``. ``spec is None`` -> ``Verdict(overall="no_spec")``.

    Raises :class:`SpecError` if any rule names a metric this module
    doesn't know how to evaluate (see :func:`known_metrics`).
    """
    if spec is None:
        return Verdict(overall=NO_SPEC, statement="No specification attached.")

    unknown = sorted({r.metric for r in spec.rules if r.metric not in _METRIC_KINDS})
    if unknown:
        raise SpecError(
            f"Spec {spec.name!r} has rule(s) with unknown metric(s) {unknown}. "
            f"Known metrics: {', '.join(known_metrics())}.")

    rule_results: List[RuleResult] = []
    for rule in spec.rules:
        kind = _METRIC_KINDS[rule.metric]
        if kind == "ci":
            rule_results.append(_evaluate_ci_rule(rule, spec.decision_rule, sample_stats))
        elif kind == "adequacy":
            rule_results.append(_evaluate_adequacy_rule(rule, sample_stats))
        else:
            rule_results.append(_evaluate_limit_rule(rule, sample_stats))

    data_not_adequate = _get(sample_stats, "adequate") is False
    if any(r.status == FAIL for r in rule_results):
        overall = FAIL
    elif any(r.status == INCONCLUSIVE for r in rule_results) or data_not_adequate:
        overall = INCONCLUSIVE
    else:
        overall = PASS

    return Verdict(
        overall=overall, spec_id=spec.id, spec_name=spec.name, spec_revision=spec.revision,
        decision_rule=spec.decision_rule, rules=rule_results,
        statement=_STATEMENTS.get(spec.decision_rule, _STATEMENTS[DEFAULT_DECISION_RULE]),
    )


def evaluate_lot(project_meta: Optional[dict], sample_id: str, sample_stats: Any) -> Verdict:
    """Convenience: resolve the applicable spec out of a ``project.json``
    dict's ``specs`` list for ``sample_id`` (:func:`select_spec`), then
    :func:`evaluate`. ``NO_SPEC`` verdict if the project defines none."""
    specs = specs_from_project_dict(project_meta)
    return evaluate(select_spec(specs, sample_id), sample_stats)
