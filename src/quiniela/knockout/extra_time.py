from __future__ import annotations

from typing import Any

from quiniela.models.common import build_score_matrix, parse_score, summarize_score_matrix


def simulate_extra_time(
    lambda_a_90: float,
    lambda_b_90: float,
    knockout_config: dict[str, Any],
) -> dict[str, Any]:
    et_fraction = float(knockout_config.get("et_lambda_fraction", 0.33))
    max_goals_et = int(knockout_config.get("max_goals_et", 4))

    lambda_a_et = lambda_a_90 * et_fraction
    lambda_b_et = lambda_b_90 * et_fraction

    et_matrix = build_score_matrix(lambda_a_et, lambda_b_et, max_goals_et)

    p_a_wins_et = 0.0
    p_b_wins_et = 0.0
    p_still_tied = 0.0
    for score, prob in et_matrix["scores"].items():
        ga, gb = parse_score(score)
        if ga > gb:
            p_a_wins_et += prob
        elif ga < gb:
            p_b_wins_et += prob
        else:
            p_still_tied += prob

    return {
        "lambda_a_et": round(lambda_a_et, 6),
        "lambda_b_et": round(lambda_b_et, 6),
        "et_score_matrix": et_matrix,
        "p_a_wins_et": round(p_a_wins_et, 10),
        "p_b_wins_et": round(p_b_wins_et, 10),
        "p_still_tied": round(p_still_tied, 10),
    }
