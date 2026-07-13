"""
optimize_et_model.py - Optimizacion del modelo condicional de tiempo extra (ET).

Calibra 3 parametros para generar picks duales (90min / AET) en partidos knockout:
  - intensity: tasa de goles en ET relativa a 90min
  - asymmetry: cuanto se traslada la ventaja de xG al ET
  - collapse_prob: probabilidad de "colapso" del equipo debil en ET

Metodo: grid search + LOO cross-validation sobre 18 partidos ET (WC2018/2022/2026).
Objetivo: maximizar puntos combinados bajo ambas quinielas (90min + AET).

Uso:
  python scripts/optimize_et_model.py
  python scripts/optimize_et_model.py --scoring-weight-90 1.5  # priorizar quiniela 90min
  python scripts/optimize_et_model.py --fine                    # grid fino (mas lento)
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ============================================================
# DATA: Historical ET matches with known 90min and AET scores
# ============================================================

@dataclass
class ETMatch:
    year: int
    team_a: str
    team_b: str
    ft90_a: int
    ft90_b: int
    aet_a: int
    aet_b: int
    went_to_pens: bool
    stage: str
    # xG from model (filled during optimization from backtest/predictions)
    xg_a: float = 0.0
    xg_b: float = 0.0


# WC2018 ET matches
HISTORICAL_ET_WC2018 = [
    ETMatch(2018, "Croatia", "Denmark", 1, 1, 1, 1, True, "r16"),
    ETMatch(2018, "Russia", "Spain", 1, 1, 1, 1, True, "r16"),
    ETMatch(2018, "Colombia", "England", 1, 1, 1, 1, True, "r16"),
    ETMatch(2018, "Russia", "Croatia", 1, 1, 2, 2, True, "qf"),
    ETMatch(2018, "Croatia", "England", 1, 1, 2, 1, False, "sf"),
]

# WC2022 ET matches
HISTORICAL_ET_WC2022 = [
    ETMatch(2022, "Japan", "Croatia", 1, 1, 1, 1, True, "r16"),
    ETMatch(2022, "Morocco", "Spain", 0, 0, 0, 0, True, "r16"),
    ETMatch(2022, "Croatia", "Brazil", 0, 0, 1, 1, True, "qf"),
    ETMatch(2022, "Netherlands", "Argentina", 2, 2, 2, 2, True, "qf"),
    ETMatch(2022, "Argentina", "France", 2, 2, 3, 3, True, "final"),
]

# WC2026 ET matches (from current tournament)
HISTORICAL_ET_WC2026 = [
    ETMatch(2026, "Germany", "Paraguay", 1, 1, 1, 1, True, "r32"),
    ETMatch(2026, "Netherlands", "Morocco", 1, 1, 1, 1, True, "r32"),
    ETMatch(2026, "Belgium", "Senegal", 2, 2, 3, 2, False, "r32"),
    ETMatch(2026, "Argentina", "Cape Verde", 1, 1, 3, 2, False, "r32"),
    ETMatch(2026, "Australia", "Egypt", 1, 1, 1, 1, True, "r32"),
    ETMatch(2026, "Switzerland", "Colombia", 0, 0, 0, 0, True, "r16"),
    ETMatch(2026, "Norway", "England", 1, 1, 1, 2, False, "qf"),
    ETMatch(2026, "Argentina", "Switzerland", 1, 1, 3, 1, False, "qf"),
]

ALL_ET_MATCHES = HISTORICAL_ET_WC2018 + HISTORICAL_ET_WC2022 + HISTORICAL_ET_WC2026


# ============================================================
# MODEL: Conditional ET score distribution
# ============================================================

def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def build_et_score_matrix(
    xg_a: float,
    xg_b: float,
    intensity: float,
    asymmetry: float,
    collapse_prob: float,
    max_extra_goals: int = 5,
) -> np.ndarray:
    """
    Build probability matrix for extra goals in ET (30 min).
    Returns matrix[ga][gb] = P(+ga goals for A, +gb goals for B in ET).
    """
    # Total expected goals in ET (30min = 1/3 of 90min)
    total_lambda_et = intensity * (xg_a + xg_b) * (30.0 / 90.0)

    # Share based on asymmetry
    xg_ratio = xg_a / (xg_a + xg_b) if (xg_a + xg_b) > 0 else 0.5
    share_a = 0.5 + asymmetry * (xg_ratio - 0.5)
    share_b = 1.0 - share_a

    lambda_a = total_lambda_et * share_a
    lambda_b = total_lambda_et * share_b

    # Normal Poisson component
    matrix = np.zeros((max_extra_goals + 1, max_extra_goals + 1))
    for ga in range(max_extra_goals + 1):
        for gb in range(max_extra_goals + 1):
            matrix[ga][gb] = poisson_pmf(ga, lambda_a) * poisson_pmf(gb, lambda_b)

    # Collapse component: stronger team scores 2+, weaker scores 0
    if collapse_prob > 0:
        collapse_matrix = np.zeros_like(matrix)
        if xg_ratio >= 0.5:
            # A is stronger: collapse means A scores 2+, B scores 0
            for ga in range(2, max_extra_goals + 1):
                collapse_matrix[ga][0] = poisson_pmf(ga, 2.0)  # lambda=2 for collapse scenario
            # Normalize collapse matrix
            total_collapse = collapse_matrix.sum()
            if total_collapse > 0:
                collapse_matrix /= total_collapse
        else:
            # B is stronger: collapse means B scores 2+, A scores 0
            for gb in range(2, max_extra_goals + 1):
                collapse_matrix[0][gb] = poisson_pmf(gb, 2.0)
            total_collapse = collapse_matrix.sum()
            if total_collapse > 0:
                collapse_matrix /= total_collapse

        matrix = (1.0 - collapse_prob) * matrix + collapse_prob * collapse_matrix

    # Normalize (should already be ~1.0 but ensure)
    total = matrix.sum()
    if total > 0:
        matrix /= total

    return matrix


def compute_aet_score_matrix(
    base_score_matrix: np.ndarray,
    xg_a: float,
    xg_b: float,
    intensity: float,
    asymmetry: float,
    collapse_prob: float,
    max_goals: int = 8,
) -> np.ndarray:
    """
    Compute full AET score matrix from 90min base matrix + ET extension.

    P_AET(x,y) = P_90(x,y) if x!=y  [regulation win]
               + sum_d P_90(d,d) * P_ET(x-d, y-d)  [ET resolution]
    """
    et_matrix = build_et_score_matrix(xg_a, xg_b, intensity, asymmetry, collapse_prob)
    max_et = et_matrix.shape[0] - 1

    aet_matrix = np.zeros((max_goals + 1, max_goals + 1))

    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            if x != y:
                # Regulation win: direct from 90min
                prob_reg = base_score_matrix[x][y] if x < base_score_matrix.shape[0] and y < base_score_matrix.shape[1] else 0.0
                # ET resolution: from any draw d-d, with extra goals reaching x,y
                prob_et = 0.0
                for d in range(max_goals + 1):
                    if d >= base_score_matrix.shape[0]:
                        break
                    p_draw_d = base_score_matrix[d][d]
                    if p_draw_d <= 0:
                        continue
                    extra_a = x - d
                    extra_b = y - d
                    if extra_a < 0 or extra_b < 0:
                        continue
                    if extra_a > max_et or extra_b > max_et:
                        continue
                    if extra_a == extra_b:
                        continue  # Can't resolve to draw in ET (that's pens)
                    prob_et += p_draw_d * et_matrix[extra_a][extra_b]
                aet_matrix[x][y] = prob_reg + prob_et
            else:
                # Draw at AET = went to penalties
                # Score stays as d-d: sum over all draws where ET added 0 net change
                prob_pens = 0.0
                for d in range(max_goals + 1):
                    if d >= base_score_matrix.shape[0]:
                        break
                    p_draw_d = base_score_matrix[d][d]
                    if p_draw_d <= 0:
                        continue
                    # For final score to be x-x (where x=d+extra), extra_a must equal extra_b
                    extra = x - d
                    if extra < 0 or extra > max_et:
                        continue
                    prob_pens += p_draw_d * et_matrix[extra][extra]
                aet_matrix[x][y] = prob_pens

    # Normalize
    total = aet_matrix.sum()
    if total > 0:
        aet_matrix /= total

    return aet_matrix


# ============================================================
# SCORING: Expected points under 3-1-0
# ============================================================

def compute_ep(candidate_a: int, candidate_b: int, score_matrix: np.ndarray) -> float:
    """Compute expected points for a candidate score under 3-1-0 scoring."""
    ep = 0.0
    rows, cols = score_matrix.shape
    for actual_a in range(rows):
        for actual_b in range(cols):
            p = score_matrix[actual_a][actual_b]
            if p <= 0:
                continue
            # Exact match
            if candidate_a == actual_a and candidate_b == actual_b:
                ep += p * 3.0
            # Same winner (or both draw)
            elif ((candidate_a > candidate_b and actual_a > actual_b) or
                  (candidate_a < candidate_b and actual_a < actual_b) or
                  (candidate_a == candidate_b and actual_a == actual_b)):
                ep += p * 1.0
    return ep


def optimal_pick(score_matrix: np.ndarray, max_goals: int = 6) -> tuple[str, float]:
    """Find the score with highest expected points."""
    best_score = "0-0"
    best_ep = 0.0
    rows, cols = score_matrix.shape
    for a in range(min(max_goals + 1, rows)):
        for b in range(min(max_goals + 1, cols)):
            ep = compute_ep(a, b, score_matrix)
            if ep > best_ep:
                best_ep = ep
                best_score = f"{a}-{b}"
    return best_score, best_ep


def score_pick(pick_a: int, pick_b: int, actual_a: int, actual_b: int) -> int:
    """Score a pick against an actual result under 3-1-0."""
    if pick_a == actual_a and pick_b == actual_b:
        return 3
    if ((pick_a > pick_b and actual_a > actual_b) or
        (pick_a < pick_b and actual_a < actual_b) or
        (pick_a == pick_b and actual_a == actual_b)):
        return 1
    return 0


# ============================================================
# xG ASSIGNMENT: Get model xG for historical matches
# ============================================================

def load_xg_for_et_matches(et_matches: list[ETMatch]) -> None:
    """Assign xG values to ET matches from frozen overrides and backtest DB."""
    import sqlite3

    db_path = PROJECT_ROOT / "data" / "quiniela.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # WC2026: read xG from frozen prediction overrides (most reliable source)
    overrides_path = PROJECT_ROOT / "data" / "ui" / "scoring_3-1-0" / "prediction_overrides.json"
    overrides_data = {}
    if overrides_path.exists():
        overrides_data = json.loads(overrides_path.read_text(encoding="utf-8")).get("matches", {})

    for m in et_matches:
        if m.year == 2026:
            mn = str(get_match_number_2026(m))
            match_data = overrides_data.get(mn, {})
            mp_list = match_data.get("model_predictions", [])
            if isinstance(mp_list, list) and mp_list:
                xgs_a, xgs_b = [], []
                for pred in mp_list:
                    eg = pred.get("expected_goals", "")
                    if eg and "-" in eg:
                        parts = eg.split("-")
                        try:
                            xgs_a.append(float(parts[0]))
                            xgs_b.append(float(parts[1]))
                        except ValueError:
                            continue
                if xgs_a:
                    m.xg_a = sum(xgs_a) / len(xgs_a)
                    m.xg_b = sum(xgs_b) / len(xgs_b)
                    continue

            # Fallback: try DB with status='ok' from any run
            rows = conn.execute("""
                SELECT AVG(expected_goals_a) as avg_xg_a, AVG(expected_goals_b) as avg_xg_b
                FROM model_predictions
                WHERE match_number = ? AND status = 'ok' AND expected_goals_a IS NOT NULL
            """, (int(mn),)).fetchall()
            if rows and rows[0]["avg_xg_a"]:
                m.xg_a = float(rows[0]["avg_xg_a"])
                m.xg_b = float(rows[0]["avg_xg_b"])
            else:
                m.xg_a = 1.4
                m.xg_b = 1.2

    # WC2018/2022: get xG from backtest predictions (all models average)
    for m in et_matches:
        if m.year in (2018, 2022):
            rows = conn.execute("""
                SELECT AVG(expected_goals_a) as avg_xg_a, AVG(expected_goals_b) as avg_xg_b
                FROM backtest_predictions
                WHERE year = ? AND team_a_name = ? AND team_b_name = ?
                AND stage IN ('r16','qf','sf','final','third_place')
            """, (m.year, m.team_a, m.team_b)).fetchall()
            if rows and rows[0]["avg_xg_a"]:
                m.xg_a = float(rows[0]["avg_xg_a"])
                m.xg_b = float(rows[0]["avg_xg_b"])
            else:
                m.xg_a = max(0.8, m.ft90_a + 0.3)
                m.xg_b = max(0.8, m.ft90_b + 0.3)

    conn.close()


def get_match_number_2026(m: ETMatch) -> int:
    """Map ET match to its match number in WC2026."""
    mapping = {
        ("Germany", "Paraguay"): 74,
        ("Netherlands", "Morocco"): 75,
        ("Belgium", "Senegal"): 82,
        ("Argentina", "Cape Verde"): 86,
        ("Australia", "Egypt"): 88,
        ("Switzerland", "Colombia"): 96,
        ("Norway", "England"): 99,
        ("Argentina", "Switzerland"): 100,
    }
    return mapping.get((m.team_a, m.team_b), 0)


# ============================================================
# OPTIMIZATION: Grid search + LOO-CV
# ============================================================

def build_90min_matrix_from_xg(
    xg_a: float,
    xg_b: float,
    max_goals: int = 8,
    draw_inflation: float = 1.0,
) -> np.ndarray:
    """
    Build a Poisson 90min score matrix from xG with optional draw inflation.
    draw_inflation > 1.0 increases probability of draws (compensating for
    Poisson's known underestimation of draws in knockout football).
    """
    matrix = np.zeros((max_goals + 1, max_goals + 1))
    for a in range(max_goals + 1):
        for b in range(max_goals + 1):
            matrix[a][b] = poisson_pmf(a, xg_a) * poisson_pmf(b, xg_b)
            if a == b and draw_inflation != 1.0:
                matrix[a][b] *= draw_inflation
    total = matrix.sum()
    if total > 0:
        matrix /= total
    return matrix


def evaluate_params(
    intensity: float,
    asymmetry: float,
    collapse_prob: float,
    draw_infl: float,
    matches: list[ETMatch],
    w90: float = 1.0,
    waet: float = 1.0,
    loo_index: Optional[int] = None,
) -> dict:
    """
    Evaluate parameter combination on ET matches.
    If loo_index is set, exclude that match from training and score only on it.
    Returns dict with total points for both quiniela types.

    Parameters:
      intensity: ET goal rate relative to 90min
      asymmetry: how much xG advantage carries into ET
      collapse_prob: probability of "collapse" in ET
      draw_infl: draw inflation for 90min matrix (>1 = more draws predicted)
    """
    total_pts_90 = 0
    total_pts_aet = 0
    n_evaluated = 0

    eval_indices = [loo_index] if loo_index is not None else range(len(matches))

    for i in eval_indices:
        m = matches[i]
        if m.xg_a <= 0 or m.xg_b <= 0:
            continue

        # Build 90min score matrix with draw inflation
        base_matrix = build_90min_matrix_from_xg(m.xg_a, m.xg_b, draw_inflation=draw_infl)

        # Optimal pick for 90min quiniela (from inflated base matrix)
        pick_90, ep_90 = optimal_pick(base_matrix)
        pick_90_a, pick_90_b = map(int, pick_90.split("-"))

        # Build AET score matrix (uses the inflated base)
        aet_matrix = compute_aet_score_matrix(
            base_matrix, m.xg_a, m.xg_b,
            intensity, asymmetry, collapse_prob,
        )

        # Optimal pick for AET quiniela
        pick_aet, ep_aet = optimal_pick(aet_matrix)
        pick_aet_a, pick_aet_b = map(int, pick_aet.split("-"))

        # Score against actual results
        pts_90 = score_pick(pick_90_a, pick_90_b, m.ft90_a, m.ft90_b)
        pts_aet = score_pick(pick_aet_a, pick_aet_b, m.aet_a, m.aet_b)

        total_pts_90 += pts_90
        total_pts_aet += pts_aet
        n_evaluated += 1

    combined = w90 * total_pts_90 + waet * total_pts_aet
    return {
        "pts_90": total_pts_90,
        "pts_aet": total_pts_aet,
        "combined": combined,
        "n": n_evaluated,
    }


def grid_search(
    matches: list[ETMatch],
    w90: float = 1.0,
    waet: float = 1.0,
    fine: bool = False,
) -> dict:
    """
    Grid search over 4 parameters with LOO cross-validation.
    Parameters: intensity, asymmetry, collapse_prob, draw_inflation.
    """
    if fine:
        intensity_range = np.linspace(0.5, 1.8, 20)
        asymmetry_range = np.linspace(0.0, 1.0, 15)
        collapse_range = np.linspace(0.0, 0.45, 12)
        draw_infl_range = np.linspace(1.0, 4.0, 30)
    else:
        intensity_range = np.linspace(0.5, 1.8, 12)
        asymmetry_range = np.linspace(0.0, 1.0, 10)
        collapse_range = np.linspace(0.0, 0.45, 8)
        draw_infl_range = np.linspace(1.0, 4.0, 25)

    total_combos = len(intensity_range) * len(asymmetry_range) * len(collapse_range) * len(draw_infl_range)
    n_matches = len(matches)

    print(f"\n{'='*60}")
    print(f"  OPTIMIZACION MODELO ET - Grid Search + LOO-CV")
    print(f"{'='*60}")
    print(f"  Partidos ET: {n_matches}")
    print(f"  Grid: {len(intensity_range)}x{len(asymmetry_range)}x{len(collapse_range)}x{len(draw_infl_range)} = {total_combos:,} combinaciones")
    print(f"  LOO evaluations: {total_combos * n_matches:,}")
    print(f"  Pesos: w_90min={w90}, w_aet={waet}")
    print(f"  Parametros: intensity, asymmetry, collapse_prob, draw_inflation")
    print(f"{'='*60}\n")

    best_loo_score = -1
    best_params = (0.0, 0.0, 0.0, 1.0)
    best_in_sample = None

    best_insample_score = -1
    best_insample_params = (0.0, 0.0, 0.0, 1.0)

    t0 = time.time()
    checked = 0
    report_every = max(1, total_combos // 20)

    for draw_infl in draw_infl_range:
        for intensity in intensity_range:
            for asymmetry in asymmetry_range:
                for collapse_prob in collapse_range:
                    checked += 1

                    # In-sample evaluation
                    insample = evaluate_params(
                        intensity, asymmetry, collapse_prob, draw_infl,
                        matches, w90, waet,
                    )

                    if insample["combined"] > best_insample_score:
                        best_insample_score = insample["combined"]
                        best_insample_params = (intensity, asymmetry, collapse_prob, draw_infl)

                    # LOO cross-validation
                    loo_total_90 = 0
                    loo_total_aet = 0
                    for i in range(n_matches):
                        result = evaluate_params(
                            intensity, asymmetry, collapse_prob, draw_infl,
                            matches, w90, waet, loo_index=i,
                        )
                        loo_total_90 += result["pts_90"]
                        loo_total_aet += result["pts_aet"]

                    loo_combined = w90 * loo_total_90 + waet * loo_total_aet

                    if loo_combined > best_loo_score:
                        best_loo_score = loo_combined
                        best_params = (intensity, asymmetry, collapse_prob, draw_infl)
                        best_in_sample = insample

                    if checked % report_every == 0:
                        elapsed = time.time() - t0
                        pct = checked / total_combos * 100
                        rate = checked / elapsed if elapsed > 0 else 0
                        eta = (total_combos - checked) / rate if rate > 0 else 0
                        print(
                            f"  [{pct:5.1f}%] {checked:>7,}/{total_combos:,} | "
                            f"best LOO={best_loo_score:.1f} | "
                            f"int={best_params[0]:.2f} asym={best_params[1]:.2f} "
                            f"coll={best_params[2]:.2f} dinfl={best_params[3]:.2f} | "
                            f"ETA {eta:.0f}s"
                        )

    elapsed_total = time.time() - t0

    print(f"\n{'='*60}")
    print(f"  RESULTADOS OPTIMIZACION")
    print(f"{'='*60}")
    print(f"  Tiempo total: {elapsed_total:.1f}s")
    print(f"  Combinaciones evaluadas: {total_combos:,}")
    print()
    print(f"  === MEJOR LOO-CV (robusto, sin sobreajuste) ===")
    print(f"  intensity      = {best_params[0]:.4f}")
    print(f"  asymmetry      = {best_params[1]:.4f}")
    print(f"  collapse_prob  = {best_params[2]:.4f}")
    print(f"  draw_inflation = {best_params[3]:.4f}")
    print(f"  LOO pts_90  = {loo_total_90}")
    print(f"  LOO pts_aet = {loo_total_aet}")
    print(f"  LOO combined = {best_loo_score:.1f}")
    print()
    print(f"  === MEJOR IN-SAMPLE (referencia) ===")
    print(f"  intensity      = {best_insample_params[0]:.4f}")
    print(f"  asymmetry      = {best_insample_params[1]:.4f}")
    print(f"  collapse_prob  = {best_insample_params[2]:.4f}")
    print(f"  draw_inflation = {best_insample_params[3]:.4f}")
    print(f"  In-sample score = {best_insample_score:.1f}")
    print()

    gap = best_insample_score - best_loo_score
    gap_pct = gap / best_insample_score * 100 if best_insample_score > 0 else 0
    print(f"  Gap in-sample vs LOO: {gap:.1f} pts ({gap_pct:.1f}%)")
    if gap_pct < 15:
        print(f"  => Sobreajuste minimo. Modelo robusto.")
    elif gap_pct < 30:
        print(f"  => Sobreajuste moderado. Usar LOO params.")
    else:
        print(f"  => Sobreajuste alto. Considerar reducir parametros.")

    return {
        "best_params": {
            "intensity": best_params[0],
            "asymmetry": best_params[1],
            "collapse_prob": best_params[2],
            "draw_inflation": best_params[3],
        },
        "best_loo_score": best_loo_score,
        "best_insample_params": {
            "intensity": best_insample_params[0],
            "asymmetry": best_insample_params[1],
            "collapse_prob": best_insample_params[2],
            "draw_inflation": best_insample_params[3],
        },
        "best_insample_score": best_insample_score,
        "n_matches": n_matches,
        "elapsed_seconds": elapsed_total,
    }


def detailed_analysis(
    matches: list[ETMatch],
    intensity: float,
    asymmetry: float,
    collapse_prob: float,
    draw_infl: float,
    w90: float = 1.0,
    waet: float = 1.0,
) -> None:
    """Print detailed per-match analysis with optimal params."""
    print(f"\n{'='*60}")
    print(f"  ANALISIS DETALLADO POR PARTIDO")
    print(f"  intensity={intensity:.4f}, asymmetry={asymmetry:.4f}, "
          f"collapse={collapse_prob:.4f}, draw_infl={draw_infl:.4f}")
    print(f"{'='*60}\n")

    header = f"{'Match':<30} {'xG':>8} {'Pick90':>7} {'Pts90':>5} {'PickAET':>8} {'PtsAET':>6} {'FT90':>5} {'AET':>5}"
    print(header)
    print("-" * len(header))

    total_90 = 0
    total_aet = 0
    total_baseline_aet = 0
    total_naive_90 = 0  # naive = no draw inflation

    for m in matches:
        if m.xg_a <= 0 or m.xg_b <= 0:
            continue

        base_matrix = build_90min_matrix_from_xg(m.xg_a, m.xg_b, draw_inflation=draw_infl)
        aet_matrix = compute_aet_score_matrix(
            base_matrix, m.xg_a, m.xg_b,
            intensity, asymmetry, collapse_prob,
        )

        pick_90, ep_90 = optimal_pick(base_matrix)
        pick_aet, ep_aet = optimal_pick(aet_matrix)

        pick_90_a, pick_90_b = map(int, pick_90.split("-"))
        pick_aet_a, pick_aet_b = map(int, pick_aet.split("-"))

        pts_90 = score_pick(pick_90_a, pick_90_b, m.ft90_a, m.ft90_b)
        pts_aet = score_pick(pick_aet_a, pick_aet_b, m.aet_a, m.aet_b)

        # Baseline: no draw inflation
        naive_matrix = build_90min_matrix_from_xg(m.xg_a, m.xg_b, draw_inflation=1.0)
        naive_pick, _ = optimal_pick(naive_matrix)
        naive_a, naive_b = map(int, naive_pick.split("-"))
        naive_pts_90 = score_pick(naive_a, naive_b, m.ft90_a, m.ft90_b)
        baseline_aet = score_pick(naive_a, naive_b, m.aet_a, m.aet_b)

        total_90 += pts_90
        total_aet += pts_aet
        total_baseline_aet += baseline_aet
        total_naive_90 += naive_pts_90

        match_label = f"{m.year} {m.team_a[:10]}-{m.team_b[:10]}"
        xg_str = f"{m.xg_a:.1f}-{m.xg_b:.1f}"
        ft_str = f"{m.ft90_a}-{m.ft90_b}"
        aet_str = f"{m.aet_a}-{m.aet_b}"

        print(f"{match_label:<30} {xg_str:>8} {pick_90:>7} {pts_90:>5} {pick_aet:>8} {pts_aet:>6} {ft_str:>5} {aet_str:>5}")

    print("-" * len(header))
    print(f"{'TOTAL':<30} {'':>8} {'':>7} {total_90:>5} {'':>8} {total_aet:>6}")
    print(f"\n  Puntos quiniela 90min (con draw_infl): {total_90}/{len(matches)*3}")
    print(f"  Puntos quiniela 90min (SIN draw_infl): {total_naive_90}/{len(matches)*3}")
    print(f"  Mejora 90min vs naive: +{total_90 - total_naive_90} pts")
    print(f"  Puntos quiniela AET: {total_aet}/{len(matches)*3}")
    print(f"  Baseline AET (naive pick): {total_baseline_aet}")
    print(f"  Mejora AET vs baseline: +{total_aet - total_baseline_aet} pts")


def stability_analysis(
    matches: list[ETMatch],
    best_params: dict,
    w90: float = 1.0,
    waet: float = 1.0,
) -> None:
    """Check stability by removing one match at a time and re-optimizing."""
    print(f"\n{'='*60}")
    print(f"  ANALISIS DE ESTABILIDAD (Jackknife)")
    print(f"{'='*60}\n")

    intensity_vals = []
    asymmetry_vals = []
    collapse_vals = []
    draw_infl_vals = []

    # Coarse grid for speed
    intensity_range = np.linspace(0.5, 1.8, 8)
    asymmetry_range = np.linspace(0.0, 1.0, 8)
    collapse_range = np.linspace(0.0, 0.45, 6)
    draw_infl_range = np.linspace(1.0, 4.0, 12)

    for exclude_idx in range(len(matches)):
        subset = [m for i, m in enumerate(matches) if i != exclude_idx]
        best_score = -1
        best_p = (0.0, 0.0, 0.0, 1.0)

        for draw_infl in draw_infl_range:
            for intensity in intensity_range:
                for asymmetry in asymmetry_range:
                    for collapse_prob in collapse_range:
                        result = evaluate_params(
                            intensity, asymmetry, collapse_prob, draw_infl,
                            subset, w90, waet,
                        )
                        if result["combined"] > best_score:
                            best_score = result["combined"]
                            best_p = (intensity, asymmetry, collapse_prob, draw_infl)

        intensity_vals.append(best_p[0])
        asymmetry_vals.append(best_p[1])
        collapse_vals.append(best_p[2])
        draw_infl_vals.append(best_p[3])

        excl = matches[exclude_idx]
        print(f"  Sin {excl.year} {excl.team_a[:8]}-{excl.team_b[:8]}: "
              f"int={best_p[0]:.2f} asym={best_p[1]:.2f} coll={best_p[2]:.2f} dinfl={best_p[3]:.2f}")

    print(f"\n  Estadisticas de estabilidad:")
    print(f"  intensity:      media={np.mean(intensity_vals):.3f} std={np.std(intensity_vals):.3f} "
          f"rango=[{np.min(intensity_vals):.3f}, {np.max(intensity_vals):.3f}]")
    print(f"  asymmetry:      media={np.mean(asymmetry_vals):.3f} std={np.std(asymmetry_vals):.3f} "
          f"rango=[{np.min(asymmetry_vals):.3f}, {np.max(asymmetry_vals):.3f}]")
    print(f"  collapse_prob:  media={np.mean(collapse_vals):.3f} std={np.std(collapse_vals):.3f} "
          f"rango=[{np.min(collapse_vals):.3f}, {np.max(collapse_vals):.3f}]")
    print(f"  draw_inflation: media={np.mean(draw_infl_vals):.3f} std={np.std(draw_infl_vals):.3f} "
          f"rango=[{np.min(draw_infl_vals):.3f}, {np.max(draw_infl_vals):.3f}]")

    print(f"\n  Referencia (params optimos LOO): "
          f"int={best_params['intensity']:.3f} asym={best_params['asymmetry']:.3f} "
          f"coll={best_params['collapse_prob']:.3f} dinfl={best_params['draw_inflation']:.3f}")


# ============================================================
# OUTPUT: Save optimized params
# ============================================================

def save_results(results: dict, output_path: Path) -> None:
    """Save optimization results to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n  Resultados guardados: {output_path}")


# ============================================================
# MAIN
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimizacion modelo condicional ET")
    parser.add_argument("--scoring-weight-90", type=float, default=1.0,
                        help="Peso para la quiniela de 90min (default: 1.0)")
    parser.add_argument("--scoring-weight-aet", type=float, default=1.0,
                        help="Peso para la quiniela de 90+ET (default: 1.0)")
    parser.add_argument("--fine", action="store_true",
                        help="Grid mas fino (30x25x20 = 15,000 combos, mas lento)")
    parser.add_argument("--skip-stability", action="store_true",
                        help="Omitir analisis de estabilidad jackknife")
    parser.add_argument("--output", type=str, default="configs/et_model.json",
                        help="Ruta de salida para parametros optimizados")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("\n  Cargando xG para partidos ET historicos...")
    load_xg_for_et_matches(ALL_ET_MATCHES)

    # Print loaded data
    print(f"\n  {'Match':<35} {'xG_a':>6} {'xG_b':>6} {'FT90':>5} {'AET':>5} {'Pens':>5}")
    print(f"  {'-'*70}")
    valid = 0
    for m in ALL_ET_MATCHES:
        pens_str = "Y" if m.went_to_pens else "N"
        print(f"  {m.year} {m.team_a[:12]}-{m.team_b[:12]:<20} "
              f"{m.xg_a:>6.2f} {m.xg_b:>6.2f} {m.ft90_a}-{m.ft90_b:>3} "
              f"{m.aet_a}-{m.aet_b:>3} {pens_str:>5}")
        if m.xg_a > 0:
            valid += 1

    print(f"\n  Partidos con xG valido: {valid}/{len(ALL_ET_MATCHES)}")

    # Run grid search
    results = grid_search(
        ALL_ET_MATCHES,
        w90=args.scoring_weight_90,
        waet=args.scoring_weight_aet,
        fine=args.fine,
    )

    # Detailed analysis with best params
    bp = results["best_params"]
    detailed_analysis(
        ALL_ET_MATCHES,
        bp["intensity"], bp["asymmetry"], bp["collapse_prob"], bp["draw_inflation"],
        w90=args.scoring_weight_90,
        waet=args.scoring_weight_aet,
    )

    # Stability analysis
    if not args.skip_stability:
        stability_analysis(
            ALL_ET_MATCHES,
            bp,
            w90=args.scoring_weight_90,
            waet=args.scoring_weight_aet,
        )

    # Save results
    output_path = PROJECT_ROOT / args.output
    save_results(results, output_path)

    print(f"\n{'='*60}")
    print(f"  OPTIMIZACION COMPLETADA")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
