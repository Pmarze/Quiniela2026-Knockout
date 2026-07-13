from __future__ import annotations

from quiniela.knockout.adjustments import apply_knockout_adjustments, compute_et_dual_picks, is_knockout_match
from quiniela.knockout.extra_time import simulate_extra_time
from quiniela.knockout.penalties import simulate_penalty_shootout
from quiniela.knockout.resolver import (
    KnockoutResolution,
    build_knockout_consensus,
    resolve_knockout_outcome,
)

__all__ = [
    "apply_knockout_adjustments",
    "build_knockout_consensus",
    "compute_et_dual_picks",
    "is_knockout_match",
    "KnockoutResolution",
    "resolve_knockout_outcome",
    "simulate_extra_time",
    "simulate_penalty_shootout",
]
