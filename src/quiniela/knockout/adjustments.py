from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

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
_ET_MODEL_CACHE: dict[str, Any] | None | bool = False


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


# ============================================================
# ET dual-pick: computes picks for 90min and AET quinielas
# ============================================================

def _load_et_model_config() -> dict[str, Any] | None:
    global _ET_MODEL_CACHE
    if _ET_MODEL_CACHE is not False:
        return _ET_MODEL_CACHE
    et_path = Path(__file__).resolve().parents[3] / "configs" / "et_model.json"
    if not et_path.exists():
        _ET_MODEL_CACHE = None
        return None
    with open(et_path, encoding="utf-8") as f:
        _ET_MODEL_CACHE = json.load(f)
    return _ET_MODEL_CACHE


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _build_et_extra_matrix(
    xg_a: float, xg_b: float,
    intensity: float, asymmetry: float, collapse_prob: float,
    max_extra: int = 5,
) -> np.ndarray:
    total_et = intensity * (xg_a + xg_b) * (30.0 / 90.0)
    xg_ratio = xg_a / (xg_a + xg_b) if (xg_a + xg_b) > 0 else 0.5
    share_a = 0.5 + asymmetry * (xg_ratio - 0.5)
    la = total_et * share_a
    lb = total_et * (1.0 - share_a)
    m = np.zeros((max_extra + 1, max_extra + 1))
    for ga in range(max_extra + 1):
        for gb in range(max_extra + 1):
            m[ga][gb] = _poisson_pmf(ga, la) * _poisson_pmf(gb, lb)
    if collapse_prob > 0:
        cm = np.zeros_like(m)
        if xg_ratio >= 0.5:
            for ga in range(2, max_extra + 1):
                cm[ga][0] = _poisson_pmf(ga, 2.0)
        else:
            for gb in range(2, max_extra + 1):
                cm[0][gb] = _poisson_pmf(gb, 2.0)
        tc = cm.sum()
        if tc > 0:
            cm /= tc
        m = (1.0 - collapse_prob) * m + collapse_prob * cm
    t = m.sum()
    if t > 0:
        m /= t
    return m


def _build_aet_matrix(
    base_matrix: np.ndarray,
    xg_a: float, xg_b: float,
    intensity: float, asymmetry: float, collapse_prob: float,
    max_goals: int = 8,
) -> np.ndarray:
    et_m = _build_et_extra_matrix(xg_a, xg_b, intensity, asymmetry, collapse_prob)
    max_et = et_m.shape[0] - 1
    aet = np.zeros((max_goals + 1, max_goals + 1))
    brows, bcols = base_matrix.shape
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            if x != y:
                prob = base_matrix[x][y] if x < brows and y < bcols else 0.0
                for d in range(min(max_goals + 1, brows)):
                    pd = base_matrix[d][d]
                    if pd <= 0:
                        continue
                    ea, eb = x - d, y - d
                    if ea < 0 or eb < 0 or ea > max_et or eb > max_et or ea == eb:
                        continue
                    prob += pd * et_m[ea][eb]
                aet[x][y] = prob
            else:
                prob = 0.0
                for d in range(min(max_goals + 1, brows)):
                    pd = base_matrix[d][d]
                    if pd <= 0:
                        continue
                    extra = x - d
                    if extra < 0 or extra > max_et:
                        continue
                    prob += pd * et_m[extra][extra]
                aet[x][y] = prob
    t = aet.sum()
    if t > 0:
        aet /= t
    return aet


def _optimal_pick_from_np(matrix: np.ndarray, scoring: dict[str, Any], max_goals: int = 6) -> dict[str, Any]:
    exact_pts = float(scoring.get("exact_score", 3))
    margin_pts = float(scoring.get("same_margin_or_draw", scoring.get("margin_or_draw", 1)))
    winner_pts = float(scoring.get("winner", 0))
    rows, cols = matrix.shape
    best_score, best_ep = "0-0", 0.0
    for ca in range(min(max_goals + 1, rows)):
        for cb in range(min(max_goals + 1, cols)):
            ep = 0.0
            for aa in range(rows):
                for ab in range(cols):
                    p = matrix[aa][ab]
                    if p <= 0:
                        continue
                    if ca == aa and cb == ab:
                        ep += p * exact_pts
                    elif (ca - cb) == (aa - ab):
                        ep += p * margin_pts
                    elif ((ca > cb and aa > ab) or (ca < cb and aa < ab) or (ca == cb and aa == ab)):
                        ep += p * winner_pts
            if ep > best_ep:
                best_ep = ep
                best_score = f"{ca}-{cb}"
    return {"score": best_score, "expected_points": round(best_ep, 4)}


def compute_et_dual_picks(
    prediction: ModelPrediction,
    knockout_config: dict[str, Any],
    scoring_config: dict[str, Any],
) -> dict[str, Any] | None:
    et_cfg = _load_et_model_config()
    if not et_cfg or "best_params" not in et_cfg:
        return None
    if prediction.status != "ok" or prediction.score_matrix is None:
        return None

    params = et_cfg["best_params"]
    et_draw_infl = float(params.get("draw_inflation", 1.0))
    intensity = float(params.get("intensity", 0.5))
    asymmetry = float(params.get("asymmetry", 0.0))
    collapse_prob = float(params.get("collapse_prob", 0.0))

    scoring = _resolve_scoring(scoring_config)
    lambda_a = prediction.expected_goals_a or 1.0
    lambda_b = prediction.expected_goals_b or 1.0

    goal_deflator = float(knockout_config.get("goal_deflator", 1.0))
    la_def = lambda_a * goal_deflator
    lb_def = lambda_b * goal_deflator

    max_goals = int(prediction.score_matrix.get("max_goals", 8))
    base_matrix = build_score_matrix(la_def, lb_def, max_goals)

    # 90min pick: inflate draws heavily
    summary = summarize_score_matrix(base_matrix)
    inflated_draw = min(summary["p_draw"] * et_draw_infl, 0.80)
    remaining = 1.0 - inflated_draw
    orig_non_draw = summary["p_team_a_win"] + summary["p_team_b_win"]
    if orig_non_draw > 0:
        scale = remaining / orig_non_draw
        tp1 = summary["p_team_a_win"] * scale
        tp2 = summary["p_team_b_win"] * scale
    else:
        tp1 = remaining / 2
        tp2 = remaining / 2

    # Build numpy base matrix for ET computations
    base_np = np.zeros((max_goals + 1, max_goals + 1))
    for score_str, prob in base_matrix["scores"].items():
        parts = score_str.split("-")
        a, b = int(parts[0]), int(parts[1])
        if a <= max_goals and b <= max_goals:
            base_np[a][b] = prob

    # Inflated matrix for 90min pick
    inflated_matrix = adjust_score_matrix_to_1x2(
        base_matrix, {"1": tp1, "X": inflated_draw, "2": tp2},
    )
    inflated_np = np.zeros((max_goals + 1, max_goals + 1))
    for score_str, prob in inflated_matrix["scores"].items():
        parts = score_str.split("-")
        a, b = int(parts[0]), int(parts[1])
        if a <= max_goals and b <= max_goals:
            inflated_np[a][b] = prob

    pick_90 = _optimal_pick_from_np(inflated_np, scoring)

    # AET pick: base matrix + ET extension
    aet_np = _build_aet_matrix(base_np, la_def, lb_def, intensity, asymmetry, collapse_prob, max_goals)
    pick_aet = _optimal_pick_from_np(aet_np, scoring)

    return {
        "pick_90min": pick_90["score"],
        "ep_90min": pick_90["expected_points"],
        "pick_aet": pick_aet["score"],
        "ep_aet": pick_aet["expected_points"],
        "et_params": params,
    }
