from __future__ import annotations

from quiniela.knockout.adjustments import apply_knockout_adjustments, is_knockout_match
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
    "is_knockout_match",
    "KnockoutResolution",
    "resolve_knockout_outcome",
    "simulate_extra_time",
    "simulate_penalty_shootout",
]
