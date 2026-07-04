"""
calibrate_knockout.py — Calibración automática de parámetros knockout.

Busca los valores óptimos de goal_deflator y draw_inflation usando:
  - Partidos de eliminación directa del backtest (WC 2018/2022)
  - Partidos de eliminación ya completados en el torneo actual (WC 2026)

Actualiza configs/knockout.yaml con los valores óptimos encontrados.

Uso:
  python scripts/calibrate_knockout.py
  python scripts/calibrate_knockout.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from quiniela.models.common import (
    adjust_score_matrix_to_1x2,
    build_score_matrix,
    summarize_score_matrix,
)
from quiniela.scoring.quiniela import select_best_score


_GROUP_STAGES = {"group", "groups", "group_stage", "Group Stage"}
_ENSEMBLE_IDS = {
    "weighted_ensemble",
    "weighted_points_ensemble",
    "weighted_1x2_ensemble",
    "weighted_exact_ensemble",
    "calibrated_scoreline_ensemble",
}

SCORING = {"exact_score": 5, "same_margin_or_draw": 3, "winner": 1}


def _outcome(a: int, b: int) -> str:
    if a > b:
        return "1"
    if a < b:
        return "2"
    return "X"


def _score_prediction(pred_a: int, pred_b: int, actual_a: int, actual_b: int) -> int:
    if pred_a == actual_a and pred_b == actual_b:
        return 5
    pred_diff = pred_a - pred_b
    actual_diff = actual_a - actual_b
    pred_out = _outcome(pred_a, pred_b)
    actual_out = _outcome(actual_a, actual_b)
    if pred_out == "X" and actual_out == "X":
        return 3
    if pred_out != "X" and pred_diff == actual_diff:
        return 3
    if pred_out == actual_out:
        return 1
    return 0


def _apply_knockout_params(
    xg_a: float,
    xg_b: float,
    score_matrix: dict[str, Any],
    goal_deflator: float,
    draw_inflation: float,
) -> str:
    lambda_a = xg_a * goal_deflator
    lambda_b = xg_b * goal_deflator
    max_goals = int(score_matrix.get("max_goals", 8))
    deflated = build_score_matrix(lambda_a, lambda_b, max_goals)
    summary = summarize_score_matrix(deflated)
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
    adjusted = adjust_score_matrix_to_1x2(
        deflated,
        {"1": target_p1, "X": inflated_draw, "2": target_p2},
    )
    best = select_best_score(adjusted, SCORING)
    return best["score"]


def _load_backtest_knockout(conn: sqlite3.Connection) -> list[dict]:
    run_row = conn.execute(
        "SELECT backtest_run_id FROM backtest_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not run_row:
        return []
    run_id = run_row[0]
    rows = conn.execute(
        """
        SELECT bp.model_id, bp.match_id, bp.expected_goals_a, bp.expected_goals_b,
               bp.score_matrix_json, bp.actual_score, bp.stage
        FROM backtest_predictions bp
        WHERE bp.backtest_run_id = ?
          AND bp.stage NOT IN ('group', 'groups', 'group_stage', 'Group Stage')
          AND bp.stage IS NOT NULL AND bp.stage != ''
          AND bp.status = 'ok'
          AND bp.score_matrix_json IS NOT NULL
          AND bp.expected_goals_a IS NOT NULL
        """,
        (run_id,),
    ).fetchall()
    results = []
    for r in rows:
        model_id = r[0]
        if model_id in _ENSEMBLE_IDS:
            continue
        actual = r[5]
        if not actual or "-" not in actual:
            continue
        parts = actual.split("-")
        results.append({
            "model_id": model_id,
            "match_id": r[1],
            "xg_a": float(r[2]),
            "xg_b": float(r[3]),
            "score_matrix": json.loads(r[4]),
            "actual_a": int(parts[0]),
            "actual_b": int(parts[1]),
            "source": "backtest",
        })
    return results


def _load_live_knockout(conn: sqlite3.Connection) -> list[dict]:
    completed = {}
    for r in conn.execute(
        """
        SELECT DISTINCT canonical_match_id, home_score, away_score
        FROM state_matches
        WHERE is_completed = 1
          AND stage NOT IN ('group', 'groups', 'group_stage')
          AND stage IS NOT NULL AND stage != ''
        """
    ).fetchall():
        completed[r[0]] = (r[1], r[2])
    if not completed:
        return []

    match_ids = list(completed.keys())
    placeholders = ",".join(["?"] * len(match_ids))
    rows = conn.execute(
        f"""
        SELECT mp.model_id, mp.match_id, mp.expected_goals_a, mp.expected_goals_b,
               mp.score_matrix_json, mp.prediction_run_id
        FROM model_predictions mp
        WHERE mp.match_id IN ({placeholders})
          AND mp.status = 'ok'
          AND mp.score_matrix_json IS NOT NULL
          AND mp.expected_goals_a IS NOT NULL
        """,
        match_ids,
    ).fetchall()

    best_run: dict[tuple[str, str], tuple] = {}
    for r in rows:
        model_id, match_id = r[0], r[1]
        if model_id in _ENSEMBLE_IDS:
            continue
        key = (model_id, match_id)
        run_id = r[5]
        if key not in best_run or run_id < best_run[key][5]:
            best_run[key] = r

    results = []
    for r in best_run.values():
        match_id = r[1]
        if match_id not in completed:
            continue
        actual_a, actual_b = completed[match_id]
        results.append({
            "model_id": r[0],
            "match_id": match_id,
            "xg_a": float(r[2]),
            "xg_b": float(r[3]),
            "score_matrix": json.loads(r[4]),
            "actual_a": actual_a,
            "actual_b": actual_b,
            "source": "live",
        })
    return results


def _precompute_points(
    preds: list[dict],
    deflators: list[float],
    inflations: list[float],
) -> dict[tuple[float, float], int]:
    """Precomputa puntos totales para cada combo (d, i). O(preds × grid)."""
    grid: dict[tuple[float, float], int] = {}
    for d in deflators:
        for i in inflations:
            total = 0
            for pred in preds:
                score_str = _apply_knockout_params(
                    pred["xg_a"], pred["xg_b"], pred["score_matrix"], d, i,
                )
                if score_str and "-" in score_str:
                    pa, pb = score_str.split("-")
                    total += _score_prediction(
                        int(pa), int(pb), pred["actual_a"], pred["actual_b"],
                    )
            grid[(round(d, 3), round(i, 3))] = total
    return grid


def _best_from_grid(
    bt_grid: dict[tuple[float, float], int],
    lv_grid: dict[tuple[float, float], int],
    live_weight: float,
) -> tuple[float, float, float]:
    best_obj = -1.0
    best_d = 0.92
    best_i = 1.15
    for (d, i), bt_pts in bt_grid.items():
        lv_pts = lv_grid.get((d, i), 0)
        obj = bt_pts + live_weight * lv_pts
        if obj > best_obj:
            best_obj = obj
            best_d = d
            best_i = i
    return best_d, best_i, best_obj


def _optimize_live_weight(
    bt_grid: dict[tuple[float, float], int],
    lv_grid: dict[tuple[float, float], int],
    weight_candidates: list[float],
) -> tuple[float, float, float]:
    best_live_score = -1
    best_w = 1.0
    best_d = 0.92
    best_i = 1.15
    for w in weight_candidates:
        d, i, _ = _best_from_grid(bt_grid, lv_grid, w)
        lv_score = lv_grid.get((round(d, 3), round(i, 3)), 0)
        if lv_score > best_live_score or (
            lv_score == best_live_score and w < best_w
        ):
            best_live_score = lv_score
            best_w = w
            best_d = d
            best_i = i
    return best_w, best_d, best_i


def calibrate(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = None
    backtest_preds = _load_backtest_knockout(conn)
    live_preds = _load_live_knockout(conn)
    conn.close()

    if not backtest_preds and not live_preds:
        print("  No hay predicciones knockout para calibrar.")
        return {"goal_deflator": 0.92, "draw_inflation": 1.15, "live_weight": 1.0}

    n_bt = len(set(p["match_id"] for p in backtest_preds))
    n_lv = len(set(p["match_id"] for p in live_preds))
    print(f"  Predicciones: {len(backtest_preds)} backtest ({n_bt} partidos)"
          f" + {len(live_preds)} live ({n_lv} partidos)")

    coarse_d = [round(x * 0.05 + 0.80, 2) for x in range(5)]  # 0.80..1.00
    coarse_i = [round(x * 0.05 + 1.00, 2) for x in range(7)]  # 1.00..1.30

    if not live_preds:
        print("  Sin datos live → peso live=1.0 (solo backtest)")
        bt_coarse = _precompute_points(backtest_preds, coarse_d, coarse_i)
        best_d, best_i, _ = _best_from_grid(bt_coarse, {}, 1.0)
        fine_d = [round(best_d + (x - 4) * 0.01, 3) for x in range(9)]
        fine_d = [v for v in fine_d if 0.75 <= v <= 1.05]
        fine_i = [round(best_i + (x - 4) * 0.01, 3) for x in range(9)]
        fine_i = [v for v in fine_i if 0.95 <= v <= 1.40]
        bt_fine = _precompute_points(backtest_preds, fine_d, fine_i)
        best_d, best_i, _ = _best_from_grid(bt_fine, {}, 1.0)
        bt_pts = bt_fine.get((best_d, best_i), 0)
        no_adj = _precompute_points(backtest_preds, [1.0], [1.0]).get((1.0, 1.0), 0)
        n = len(backtest_preds)
        print(f"  Sin ajuste: {no_adj} pts ({no_adj/n:.4f} avg)")
        print(f"  Óptimo ({best_d}/{best_i}): {bt_pts} pts ({bt_pts/n:.4f} avg)")
        return {"goal_deflator": best_d, "draw_inflation": best_i, "live_weight": 1.0}

    # --- Precomputar puntos en grid gruesa (una sola vez) ---
    print("  Precomputando grid gruesa...")
    bt_coarse = _precompute_points(backtest_preds, coarse_d, coarse_i)
    lv_coarse = _precompute_points(live_preds, coarse_d, coarse_i)

    # --- Fase 1: optimizar live_weight con grid gruesa ---
    # Para cada w candidato, encontrar (d,i) óptimos con scoring ponderado,
    # luego evaluar esos (d,i) sobre datos live exclusivamente.
    # El w que produce (d,i) con mejor rendimiento live gana.
    coarse_weights = [round(1.0 + x * 0.5, 1) for x in range(15)]  # 1.0..8.0
    best_w, best_d, best_i = _optimize_live_weight(
        bt_coarse, lv_coarse, coarse_weights,
    )
    print(f"  Fase 1 (gruesa): w={best_w}, d={best_d}, i={best_i}")

    # --- Fase 2: refinar w alrededor del mejor ---
    fine_weights = [round(best_w + (x - 5) * 0.1, 2) for x in range(11)]
    fine_weights = [w for w in fine_weights if w >= 1.0]
    fine_weights = sorted(set(fine_weights))
    best_w, best_d, best_i = _optimize_live_weight(
        bt_coarse, lv_coarse, fine_weights,
    )
    print(f"  Fase 2 (w fino): w={best_w}")

    # --- Fase 3: refinar (d,i) con grid fina usando w óptimo ---
    fine_d = [round(best_d + (x - 5) * 0.01, 3) for x in range(11)]
    fine_d = [v for v in fine_d if 0.75 <= v <= 1.10]
    fine_i = [round(best_i + (x - 5) * 0.01, 3) for x in range(11)]
    fine_i = [v for v in fine_i if 0.95 <= v <= 1.40]
    print("  Precomputando grid fina...")
    bt_fine = _precompute_points(backtest_preds, fine_d, fine_i)
    lv_fine = _precompute_points(live_preds, fine_d, fine_i)
    best_d, best_i, _ = _best_from_grid(bt_fine, lv_fine, best_w)
    print(f"  Fase 3 (d,i fino): d={best_d}, i={best_i}")

    # --- Métricas finales ---
    bt_pts = bt_fine.get((best_d, best_i), bt_coarse.get((best_d, best_i), 0))
    lv_pts = lv_fine.get((best_d, best_i), lv_coarse.get((best_d, best_i), 0))
    no_adj_bt = _precompute_points(backtest_preds, [1.0], [1.0]).get((1.0, 1.0), 0)
    no_adj_lv = _precompute_points(live_preds, [1.0], [1.0]).get((1.0, 1.0), 0)
    n_bt_p = len(backtest_preds)
    n_lv_p = len(live_preds)

    print(f"\n  {'':30s} {'Backtest':>10s} {'Live':>10s}")
    print(f"  {'Sin ajuste (1.0/1.0)':30s} {no_adj_bt:>7d} pts {no_adj_lv:>7d} pts")
    print(f"  {'Óptimo':30s} {bt_pts:>7d} pts {lv_pts:>7d} pts")
    print(f"  {'Mejora':30s} {bt_pts-no_adj_bt:>+7d} pts {lv_pts-no_adj_lv:>+7d} pts")
    print(f"  {'Avg pts/pred':30s} {bt_pts/n_bt_p:>10.4f} {lv_pts/n_lv_p:>10.4f}")

    return {
        "goal_deflator": best_d,
        "draw_inflation": best_i,
        "live_weight": best_w,
        "backtest_points": bt_pts,
        "live_points": lv_pts,
        "n_backtest": n_bt_p,
        "n_live": n_lv_p,
    }


def update_config(config_path: Path, goal_deflator: float, draw_inflation: float) -> None:
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg["goal_deflator"] = goal_deflator
    cfg["draw_inflation"] = draw_inflation
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibra parámetros knockout desde datos históricos y live.")
    parser.add_argument("--db", default=str(PROJECT_ROOT / "data" / "quiniela.db"))
    parser.add_argument("--config", default=str(PROJECT_ROOT / "configs" / "knockout.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="Muestra resultado sin escribir.")
    args = parser.parse_args()

    print("\n  Calibración de parámetros knockout")
    print("  " + "=" * 40)
    result = calibrate(Path(args.db))

    d = result["goal_deflator"]
    i = result["draw_inflation"]
    w = result.get("live_weight", 1.0)
    print(f"\n  goal_deflator  = {d}")
    print(f"  draw_inflation = {i}")
    print(f"  live_weight    = {w}")

    if args.dry_run:
        print("\n  [DRY-RUN] No se actualiza configs/knockout.yaml")
    else:
        update_config(Path(args.config), d, i)
        print(f"\n  configs/knockout.yaml actualizado.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
