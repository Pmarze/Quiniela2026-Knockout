from __future__ import annotations

from typing import Any


def simulate_penalty_shootout(
    knockout_config: dict[str, Any],
    p_convert_a: float | None = None,
    p_convert_b: float | None = None,
) -> dict[str, Any]:
    default_conv = float(knockout_config.get("default_penalty_conversion", 0.75))
    pa = p_convert_a if p_convert_a is not None else default_conv
    pb = p_convert_b if p_convert_b is not None else default_conv

    p_a_wins, p_b_wins = _markov_penalties(pa, pb, rounds=5)

    return {
        "p_a_wins_penalties": round(p_a_wins, 10),
        "p_b_wins_penalties": round(p_b_wins, 10),
        "p_convert_a": round(pa, 4),
        "p_convert_b": round(pb, 4),
    }


def _markov_penalties(pa: float, pb: float, rounds: int = 5) -> tuple[float, float]:
    states: dict[tuple[int, int], float] = {(0, 0): 1.0}

    total_a_wins = 0.0
    total_b_wins = 0.0

    for rnd in range(rounds):
        after_a: dict[tuple[int, int], float] = {}
        for (sa, sb), prob in states.items():
            _add(after_a, (sa + 1, sb), prob * pa)
            _add(after_a, (sa, sb), prob * (1 - pa))

        next_states: dict[tuple[int, int], float] = {}
        remaining_after = rounds - rnd - 1
        for (sa, sb), prob in after_a.items():
            for new_sb, p_kick in [(sb + 1, pb), (sb, 1 - pb)]:
                if _is_decided(sa, new_sb, remaining_after):
                    if sa > new_sb:
                        total_a_wins += prob * p_kick
                    else:
                        total_b_wins += prob * p_kick
                else:
                    _add(next_states, (sa, new_sb), prob * p_kick)

        states = next_states

    tied_prob = 0.0
    for (sa, sb), prob in states.items():
        if sa > sb:
            total_a_wins += prob
        elif sb > sa:
            total_b_wins += prob
        else:
            tied_prob += prob

    if tied_prob > 1e-15:
        sd_a, sd_b = _sudden_death(pa, pb)
        total_a_wins += tied_prob * sd_a
        total_b_wins += tied_prob * sd_b

    total = total_a_wins + total_b_wins
    if total > 0:
        total_a_wins /= total
        total_b_wins /= total

    return total_a_wins, total_b_wins


def _is_decided(sa: int, sb: int, remaining: int) -> bool:
    return sa > sb + remaining or sb > sa + remaining


def _sudden_death(pa: float, pb: float) -> tuple[float, float]:
    p_a_scores_b_misses = pa * (1 - pb)
    p_b_scores_a_misses = (1 - pa) * pb
    p_decisive = p_a_scores_b_misses + p_b_scores_a_misses

    if p_decisive < 1e-15:
        return 0.5, 0.5

    return p_a_scores_b_misses / p_decisive, p_b_scores_a_misses / p_decisive


def _add(d: dict, key: tuple, value: float) -> None:
    d[key] = d.get(key, 0.0) + value
