"""
Grid search de hiperparámetros para elo_sdr_poisson.

Estrategia: corte único por año WC (no walk-forward) para velocidad.
Usa WC 2018 y 2022 como conjunto de evaluación, optimizando puntos
de quiniela combinados. Al final valida el top-5 con walk-forward completo.

Ejecutar:
    python scripts/tune_elo_sdr.py
    python scripts/tune_elo_sdr.py --stages r16 qf sf final third_place
    python scripts/tune_elo_sdr.py --n-random 300 --seed 42
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import uuid
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from quiniela.backtest.runner import (
    _group_matches_by_date,
    _load_training_matches,
    _load_world_cup_matches,
    _prediction_match,
)
from quiniela.models.common import ModelContext, outcome_1x2, parse_score
from quiniela.models.elo_sdr_poisson import (
    _build_monthly_elo,
    _build_rolling_form,
    _build_training_features,
    _fit_poisson,
    _predict_match,
    _sdr_projection,
    _whitening_params,
)
from quiniela.scoring.quiniela import resolve_scoring_profile
from quiniela.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Grid search hiperparámetros SDR-Elo.")
    p.add_argument("--db", default=str(PROJECT_ROOT / "data" / "quiniela.db"))
    p.add_argument("--scoring-config", default=str(PROJECT_ROOT / "configs" / "scoring.yaml"))
    p.add_argument("--scoring-profile", default=None)
    p.add_argument("--years", nargs="+", type=int, default=[2018, 2022])
    p.add_argument("--stages", nargs="*", default=None,
                   help="filtrar stages (ej: r16 qf sf). Default: todos.")
    p.add_argument("--n-random", type=int, default=200,
                   help="configs aleatorias a probar (0 = búsqueda exhaustiva)")
    p.add_argument("--seed", type=int, default=20260708)
    p.add_argument("--output", default=str(PROJECT_ROOT / "data" / "backtests" / "sdr_tuning_results.json"))
    p.add_argument("--top-n-validate", type=int, default=5,
                   help="Top-N configs a re-validar con walk-forward completo")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _load_scoring(path: Path, profile: str | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        import yaml  # type: ignore
        raw = yaml.safe_load(text)
    return resolve_scoring_profile(raw, profile)


def _score_quiniela(predicted: str | None, actual: str, scoring: dict[str, Any]) -> float:
    if not predicted:
        return 0.0
    exact = float(scoring.get("exact_score", 5))
    margin = float(scoring.get("same_margin_or_draw", scoring.get("margin_or_draw", 3)))
    winner = float(scoring.get("winner", 1))
    if predicted == actual:
        return exact
    pa, pb = parse_score(predicted)
    aa, ab = parse_score(actual)
    p_out = outcome_1x2(pa, pb)
    a_out = outcome_1x2(aa, ab)
    if p_out == "X" and a_out == "X":
        return margin
    if p_out != "X" and (pa - pb) == (aa - ab):
        return margin
    if p_out == a_out:
        return winner
    return 0.0


def _best_score_from_lambdas(
    lambda_a: float, lambda_b: float, max_goals: int, scoring: dict[str, Any]
) -> str:
    from quiniela.models.common import build_score_matrix
    from quiniela.scoring.quiniela import select_best_score
    sm = build_score_matrix(lambda_a, lambda_b, max_goals)
    return select_best_score(sm, scoring)["score"] or "1-1"


# ---------------------------------------------------------------------------
# Hyperparameter grid
# ---------------------------------------------------------------------------

PARAM_GRID = {
    "sdr_method":   ["sir", "save"],
    "sdr_dims":     [1, 2],
    "n_lags":       [3, 4, 6, 9],
    "sdr_min_year": [2006, 2010, 2014],
    "l2_xi":        [0.0, 0.5, 1.0, 2.0, 5.0, 10.0],
    "min_lambda":   [0.15, 0.25, 0.35],
    "max_lambda":   [2.0, 2.5, 3.0, 3.5, 4.0],
    # κ by match type for Elo computation
    "k_wc":         [40.0, 60.0, 80.0],
}


def _all_configs() -> list[dict[str, Any]]:
    keys = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))
    return [{k: v for k, v in zip(keys, combo)} for combo in combos]


def _random_configs(n: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    configs = []
    for _ in range(n):
        cfg = {k: rng.choice(v) for k, v in PARAM_GRID.items()}
        configs.append(cfg)
    return configs


# ---------------------------------------------------------------------------
# Fast single-cutoff evaluation (no walk-forward, for hyperparameter search)
# ---------------------------------------------------------------------------

class YearCache:
    """Pre-computed training data for a given WC year cutoff."""

    def __init__(self, year: int, wc_matches: list, conn: Any, history_run_id: str,
                 db_path: Path, stage_filter: set[str] | None):
        self.year = year
        # Use first match date as single cutoff
        self.cutoff_date = min(m.match_date for m in wc_matches)
        self.wc_matches = [m for m in wc_matches if (not stage_filter or m.stage in stage_filter)]
        self.training_matches = _load_training_matches(conn, history_run_id, self.cutoff_date)
        self.db_path = db_path
        self.history_run_id = history_run_id
        # rolling_form doesn't depend on Elo params — precompute once
        self.rolling_form = _build_rolling_form(self.training_matches)
        self.actual_scores = {m.match_id: f"{m.home_score}-{m.away_score}" for m in self.wc_matches}
        # Cache monthly Elo keyed by (k_wc,) — k_continental/qualifier/friendly fixed at defaults
        self._elo_cache: dict[tuple, dict] = {}
        # Cache feature matrices keyed by (k_wc, n_lags, sdr_min_year) — the only params that
        # change the raw feature matrix. This reduces 200-config × 3.5s calls to ~36 unique builds.
        self._feat_cache: dict[tuple, tuple | None] = {}

    def get_monthly_elo(self, cfg: dict[str, Any]) -> dict:
        key = (cfg["k_wc"], cfg.get("k_continental", 35.0), cfg.get("k_qualifier", 25.0), cfg.get("k_friendly", 20.0))
        if key not in self._elo_cache:
            self._elo_cache[key] = _build_monthly_elo(self.training_matches, cfg)
        return self._elo_cache[key]

    def get_features(self, cfg: dict[str, Any]) -> tuple | None:
        key = (cfg["k_wc"], cfg["n_lags"], cfg["sdr_min_year"])
        if key not in self._feat_cache:
            monthly_elo = self.get_monthly_elo(cfg)
            try:
                result = _build_training_features(self.training_matches, monthly_elo, self.rolling_form, cfg)
                self._feat_cache[key] = result if (len(result) == 5 and result[0].shape[0] >= 30) else None
            except Exception:
                self._feat_cache[key] = None
        return self._feat_cache[key]


def _eval_config_on_year(
    cfg_raw: dict[str, Any],
    year_cache: YearCache,
    scoring: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one hyperparameter config on one WC year (fast, single cutoff)."""
    # Build full config with defaults
    full_cfg = {
        "max_goals": 8,
        "initial_rating": 1500.0,
        "k_wc": cfg_raw.get("k_wc", 60.0),
        "k_continental": 35.0,
        "k_qualifier": 25.0,
        "k_friendly": 20.0,
        "n_lags": cfg_raw.get("n_lags", 6),
        "sdr_dims": cfg_raw.get("sdr_dims", 2),
        "sdr_method": cfg_raw.get("sdr_method", "sir"),
        "min_lambda": cfg_raw.get("min_lambda", 0.15),
        "max_lambda": cfg_raw.get("max_lambda", 5.0),
        "l2_xi": cfg_raw.get("l2_xi", 0.0),
        "sdr_min_year": cfg_raw.get("sdr_min_year", 2010),
    }

    # Features cached by (k_wc, n_lags, sdr_min_year) — skips the 3.5s build for repeated combos
    feat = year_cache.get_features(full_cfg)
    if feat is None:
        return {"ok": False}
    X, Y, G, Z, neutral_col = feat

    # SDR projection + Poisson fit (~0.35s total)
    try:
        d = min(full_cfg["sdr_dims"], 2)
        mu_sdr, inv_sqrt, B = _sdr_projection(X, Y, d=d, method=full_cfg["sdr_method"])
        X_w = (X - mu_sdr) @ inv_sqrt.T
        z = X_w @ B
        poisson_params = _fit_poisson(z, neutral_col, Z, G, d, l2_xi=full_cfg["l2_xi"], max_iter=600)
    except Exception:
        return {"ok": False}

    monthly_elo = year_cache.get_monthly_elo(full_cfg)

    model = {"ok": True, "mu_sdr": mu_sdr, "inv_sqrt": inv_sqrt, "B": B,
             "poisson_params": poisson_params, "d": d, "cfg": full_cfg}

    # Predict all WC matches using cutoff date
    cutoff_month = year_cache.cutoff_date[:7]
    total_pts = 0.0
    exact_hits = 0
    n = 0

    for wc_match in year_cache.wc_matches:
        actual = year_cache.actual_scores.get(wc_match.match_id)
        if not actual:
            continue
        pm = _prediction_match(wc_match)
        if not pm.team_a_key or not pm.team_b_key:
            continue
        # Use cutoff month for Elo lag (single cutoff approximation)
        pm_with_kickoff = type("PM", (), {
            "team_a_key": pm.team_a_key, "team_b_key": pm.team_b_key,
            "kickoff_utc": year_cache.cutoff_date + "T12:00:00Z",
        })()
        res = _predict_match(pm_with_kickoff, model, monthly_elo, year_cache.rolling_form, full_cfg)
        if res is None:
            continue
        lambda_a, lambda_b, _ = res
        predicted = _best_score_from_lambdas(lambda_a, lambda_b, 8, scoring)
        pts = _score_quiniela(predicted, actual, scoring)
        total_pts += pts
        if predicted == actual:
            exact_hits += 1
        n += 1

    if n == 0:
        return {"ok": False}

    return {"ok": True, "points": total_pts, "n": n, "exact_hits": exact_hits,
            "pts_per_match": total_pts / n}


# ---------------------------------------------------------------------------
# Walk-forward validation (full, same as experiment_elo_sdr.py)
# ---------------------------------------------------------------------------

def _validate_walkforward(
    cfg_raw: dict[str, Any],
    years: list[int],
    conn: Any,
    history_run_id: str,
    db_path: Path,
    scoring: dict[str, Any],
    stage_filter: set[str] | None,
) -> dict[str, Any]:
    from quiniela.models.elo_sdr_poisson import run_elo_sdr_poisson

    full_cfg = {
        "model_id": "elo_sdr_poisson", "model_version": "0.1.0", "max_goals": 8,
        **cfg_raw,
        "k_continental": 35.0, "k_qualifier": 25.0, "k_friendly": 20.0,
    }

    total_pts = 0.0
    total_n = 0
    exact_hits = 0

    for year in years:
        wc_matches = _load_world_cup_matches(conn, history_run_id, [year])
        if stage_filter:
            wc_matches = [m for m in wc_matches if m.stage in stage_filter]
        if not wc_matches:
            continue
        run_id = f"tune_wf_{year}_{uuid.uuid4().hex[:6]}"
        for match_date, day_matches in sorted(_group_matches_by_date(wc_matches).items()):
            training_matches = _load_training_matches(conn, history_run_id, match_date)
            prediction_matches = [_prediction_match(m) for m in day_matches]
            ctx = ModelContext(
                db_path=db_path, as_of_utc=f"{match_date}T00:00:00Z",
                prediction_run_id=run_id,
                tournament_state_id=f"historical_{match_date}",
                input_snapshot_id=history_run_id,
                training_data_version=history_run_id,
                training_matches=training_matches,
                prediction_matches=prediction_matches,
            )
            preds = {p.match_id: p for p in run_elo_sdr_poisson(ctx, full_cfg, scoring)}
            for m in day_matches:
                actual = f"{m.home_score}-{m.away_score}"
                p = preds.get(m.match_id)
                if not p or p.status != "ok":
                    continue
                pts = _score_quiniela(p.selected_score, actual, scoring)
                total_pts += pts
                total_n += 1
                if p.selected_score == actual:
                    exact_hits += 1

    return {"points": total_pts, "n": total_n, "exact_hits": exact_hits,
            "pts_per_match": round(total_pts / total_n, 3) if total_n else 0}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    scoring = _load_scoring(Path(args.scoring_config), args.scoring_profile)
    stage_filter = set(args.stages) if args.stages else None
    rng = random.Random(args.seed)

    print(f"\nGrid search SDR-Elo hiperparametros")
    print(f"  anos WC  : {args.years}")
    print(f"  stages   : {sorted(stage_filter) if stage_filter else 'todos'}")
    print(f"  n_random : {args.n_random if args.n_random else 'exhaustivo'}")

    # Precompute year caches
    store = SQLiteStore(db_path)
    store.initialize()
    conn = store.conn
    history_run_id = conn.execute("SELECT history_run_id FROM v_latest_history_run").fetchone()[0]

    year_caches = []
    for year in args.years:
        wc_matches = _load_world_cup_matches(conn, history_run_id, [year])
        if not wc_matches:
            continue
        print(f"  Precalculando cache año {year}...")
        yc = YearCache(year, wc_matches, conn, history_run_id, db_path, stage_filter)
        print(f"    training_matches={len(yc.training_matches)}, wc_eval={len(yc.wc_matches)}")
        year_caches.append(yc)

    if not year_caches:
        print("Sin años para evaluar.")
        store.close()
        return 1

    # Build config list
    if args.n_random and args.n_random > 0:
        configs = _random_configs(args.n_random, args.seed)
    else:
        configs = _all_configs()
    print(f"  Evaluando {len(configs)} configuraciones...\n")

    # Evaluate each config
    results = []
    t0 = time.time()
    for i, cfg in enumerate(configs):
        year_results = []
        valid = True
        for yc in year_caches:
            yr = _eval_config_on_year(cfg, yc, scoring)
            if not yr.get("ok"):
                valid = False
                break
            year_results.append(yr)

        if not valid:
            continue

        total_pts = sum(r["points"] for r in year_results)
        total_n = sum(r["n"] for r in year_results)
        total_exact = sum(r["exact_hits"] for r in year_results)
        results.append({
            "cfg": cfg,
            "points": total_pts,
            "n": total_n,
            "pts_per_match": round(total_pts / total_n, 3) if total_n else 0,
            "exact_hits": total_exact,
        })

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            best_so_far = max(results, key=lambda r: r["points"])["points"] if results else 0
            print(f"  {i+1}/{len(configs)} ({elapsed:.0f}s)  mejor hasta ahora: {best_so_far:.1f} pts")

    if not results:
        print("Ninguna configuracion valida.")
        store.close()
        return 1

    results.sort(key=lambda r: (-r["points"], -r["exact_hits"]))

    print(f"\n{'='*70}")
    print(f"  TOP 10 configuraciones (aprox. single-cutoff)")
    print(f"{'='*70}")
    print(f"  {'Rank':>4}  {'Pts':>6}  {'Pts/M':>6}  {'Exact':>5}  Config")
    print(f"  {'-'*66}")
    for rank, r in enumerate(results[:10], 1):
        cfg_str = " ".join(f"{k}={v}" for k, v in sorted(r["cfg"].items()))
        print(f"  {rank:>4}  {r['points']:>6.1f}  {r['pts_per_match']:>6.3f}  {r['exact_hits']:>5d}  {cfg_str}")

    # Validate top-N with full walk-forward
    top_n = min(args.top_n_validate, len(results))
    print(f"\n  Validando top-{top_n} con walk-forward completo...")
    print(f"  {'Rank':>4}  {'Pts(WF)':>8}  {'Pts/M':>6}  {'Exact':>5}  {'Pts(approx)':>11}  Config")
    print(f"  {'-'*75}")

    validated = []
    for rank, r in enumerate(results[:top_n], 1):
        wf = _validate_walkforward(
            r["cfg"], args.years, conn, history_run_id, db_path, scoring, stage_filter
        )
        validated.append({"rank_approx": rank, "cfg": r["cfg"],
                          "points_approx": r["points"], "walkforward": wf})
        cfg_str = " ".join(f"{k}={v}" for k, v in sorted(r["cfg"].items()))
        print(f"  {rank:>4}  {wf['points']:>8.1f}  {wf['pts_per_match']:>6.3f}  {wf['exact_hits']:>5d}  {r['points']:>11.1f}  {cfg_str}")

    store.close()

    # Best walk-forward config
    validated.sort(key=lambda x: (-x["walkforward"]["points"], -x["walkforward"]["exact_hits"]))
    best = validated[0]
    print(f"\n  Mejor config (walk-forward):")
    for k, v in sorted(best["cfg"].items()):
        print(f"    {k:20s} = {v}")
    print(f"  -> Puntos WF: {best['walkforward']['points']:.1f}  "
          f"({best['walkforward']['pts_per_match']:.3f} pts/partido)  "
          f"exactos: {best['walkforward']['exact_hits']}")

    # Save
    output = {
        "search": {
            "years": args.years,
            "stages": list(stage_filter) if stage_filter else None,
            "n_configs_evaluated": len(results),
            "seed": args.seed,
        },
        "top10_approx": results[:10],
        "top_validated": validated,
        "best_config": best["cfg"],
        "best_walkforward": best["walkforward"],
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Guardado en: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
