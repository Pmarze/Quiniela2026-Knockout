from __future__ import annotations

from typing import Any

from quiniela.models.common import (
    ModelContext,
    ModelPrediction,
    PredictionMatch,
    adjust_score_matrix_to_1x2,
    build_score_matrix,
    summarize_score_matrix,
    successful_prediction_from_matrix,
)
from quiniela.scoring.quiniela import select_best_score

_GROUP_STAGES = {"group", "groups", "group_stage", "group stage"}


def is_knockout_match(match: PredictionMatch) -> bool:
    stage = str(match.stage or "").strip().lower()
    return stage != "" and stage not in _GROUP_STAGES


def apply_knockout_adjustments(
    prediction: ModelPrediction,
    match: PredictionMatch,
    context: ModelContext,
    knockout_config: dict[str, Any],
    scoring_config: dict[str, Any],
) -> ModelPrediction:
    if prediction.status != "ok" or prediction.score_matrix is None:
        return prediction

    goal_deflator = float(knockout_config.get("goal_deflator", 0.92))
    draw_inflation = float(knockout_config.get("draw_inflation", 1.15))

    lambda_a = (prediction.expected_goals_a or 1.0) * goal_deflator
    lambda_b = (prediction.expected_goals_b or 1.0) * goal_deflator

    max_goals = int(prediction.score_matrix.get("max_goals", 8))
    deflated_matrix = build_score_matrix(lambda_a, lambda_b, max_goals)

    summary = summarize_score_matrix(deflated_matrix)
    inflated_draw = min(summary["p_draw"] * draw_inflation, 0.60)
    remaining = 1.0 - inflated_draw
    original_non_draw = summary["p_team_a_win"] + summary["p_team_b_win"]
    if original_non_draw > 0:
        scale = remaining / original_non_draw
        target_p1 = summary["p_team_a_win"] * scale
        target_p2 = summary["p_team_b_win"] * scale
    else:
        target_p1 = remaining / 2
        target_p2 = remaining / 2

    adjusted_matrix = adjust_score_matrix_to_1x2(
        deflated_matrix,
        {"1": target_p1, "X": inflated_draw, "2": target_p2},
    )

    scoring = _resolve_scoring(scoring_config)
    best = select_best_score(adjusted_matrix, scoring)

    warnings = list(prediction.warnings) + [
        f"knockout_goal_deflator={goal_deflator}",
        f"knockout_draw_inflation={draw_inflation}",
    ]

    return successful_prediction_from_matrix(
        context=context,
        model_id=prediction.model_id,
        model_version=prediction.model_version,
        match=match,
        lambda_a=lambda_a,
        lambda_b=lambda_b,
        score_matrix=adjusted_matrix,
        selected_score=best["score"],
        selected_expected_points=best["expected_points"],
        warnings=warnings,
    )


def _resolve_scoring(scoring_config: dict[str, Any]) -> dict[str, Any]:
    profiles = scoring_config.get("profiles")
    if profiles is None:
        return scoring_config
    name = scoring_config.get("default_profile", "5-3-1")
    return dict(profiles.get(name, {}))
