"""
Experimento: SDR-Elo Poisson vs. elo_poisson baseline.

Evalúa elo_sdr_poisson sobre WC 2018 y 2022 con el mismo backtest
walk-forward del pipeline (corte por fecha de partido). Compara
quiniela-points, exact hits, 1X2 accuracy y RPS.

Ejecutar:
    python scripts/experiment_elo_sdr.py
    python scripts/experiment_elo_sdr.py --sdr-method save --sdr-dims 1
    python scripts/experiment_elo_sdr.py --verbose
    python scripts/experiment_elo_sdr.py --output data/backtests/sdr_experiment.json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any


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
from quiniela.models.common import (
    ModelContext,
    outcome_1x2,
    parse_score,
)
from quiniela.models.elo_poisson import run_elo_poisson
from quiniela.models.elo_sdr_poisson import run_elo_sdr_poisson
from quiniela.scoring.quiniela import resolve_scoring_profile, select_best_score
from quiniela.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Experimento SDR-Elo Poisson.")
    p.add_argument("--db", default=str(PROJECT_ROOT / "data" / "quiniela.db"))
    p.add_argument("--scoring-config", default=str(PROJECT_ROOT / "configs" / "scoring.yaml"))
    p.add_argument("--scoring-profile", default=None, help="perfil de scoring (ej: 5-3-1)")
    p.add_argument("--sdr-method", choices=["sir", "save"], default="sir")
    p.add_argument("--sdr-dims", type=int, choices=[1, 2], default=2)
    p.add_argument("--n-lags", type=int, default=6)
    p.add_argument("--sdr-min-year", type=int, default=2010)
    p.add_argument("--years", nargs="+", type=int, default=[2018, 2022])
    p.add_argument("--stages", nargs="*", default=None,
                   help="filtrar por stage (ej: r16 qf sf final). Default: todos.")
    p.add_argument("--output", default=None, help="guardar resultados JSON")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_scoring_config(path: Path, profile: str | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
            raw = yaml.safe_load(text)
        except ImportError:
            raise RuntimeError(f"PyYAML no instalado y '{path}' no es JSON. Instala pyyaml.")
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


def _rps(ph: float, pd: float, pa: float, outcome: str) -> float:
    oh = 1.0 if outcome == "1" else 0.0
    ox = 1.0 if outcome == "X" else 0.0
    return 0.5 * ((ph - oh) ** 2 + (ph + pd - oh - ox) ** 2)


# ---------------------------------------------------------------------------
# Backtest one year
# ---------------------------------------------------------------------------

def _run_year(
    year: int,
    conn: Any,
    history_run_id: str,
    db_path: Path,
    scoring: dict[str, Any],
    elo_config: dict[str, Any],
    sdr_config: dict[str, Any],
    verbose: bool,
    stage_filter: set[str] | None = None,
) -> dict[str, Any]:
    print(f"\n{'='*62}")
    print(f"  WC {year}")
    print(f"{'='*62}")

    run_id = f"sdr_exp_{year}_{uuid.uuid4().hex[:6]}"
    wc_matches = _load_world_cup_matches(conn, history_run_id, [year])
    if not wc_matches:
        print(f"  Sin partidos para WC {year}")
        return {"year": year, "n": 0}

    results: list[dict[str, Any]] = []

    if stage_filter:
        wc_matches = [m for m in wc_matches if m.stage in stage_filter]
    if not wc_matches:
        print(f"  Sin partidos para WC {year} con stages={stage_filter}")
        return {"year": year, "n": 0}

    for match_date, day_matches in sorted(_group_matches_by_date(wc_matches).items()):
        training_matches = _load_training_matches(conn, history_run_id, match_date)
        prediction_matches = [_prediction_match(m) for m in day_matches]
        context = ModelContext(
            db_path=db_path,
            as_of_utc=f"{match_date}T00:00:00Z",
            prediction_run_id=run_id,
            tournament_state_id=f"historical_wc_{match_date}",
            input_snapshot_id=history_run_id,
            training_data_version=history_run_id,
            training_matches=training_matches,
            prediction_matches=prediction_matches,
        )

        sdr_preds = {p.match_id: p for p in run_elo_sdr_poisson(context, sdr_config, scoring)}
        elo_preds = {p.match_id: p for p in run_elo_poisson(context, elo_config, scoring)}
        actual_by_id = {m.match_id: f"{m.home_score}-{m.away_score}" for m in day_matches}

        for match_id, actual_score in actual_by_id.items():
            sdr_p = sdr_preds.get(match_id)
            elo_p = elo_preds.get(match_id)
            if not sdr_p or sdr_p.status != "ok":
                continue

            actual_out = outcome_1x2(*parse_score(actual_score))
            sdr_pts = _score_quiniela(sdr_p.selected_score, actual_score, scoring)
            elo_pts = _score_quiniela(elo_p.selected_score if elo_p and elo_p.status == "ok" else None, actual_score, scoring)

            sdr_rps = _rps(sdr_p.p_team_a_win or 0, sdr_p.p_draw or 0, sdr_p.p_team_b_win or 0, actual_out)
            elo_rps = _rps(elo_p.p_team_a_win or 0, elo_p.p_draw or 0, elo_p.p_team_b_win or 0, actual_out) if elo_p and elo_p.status == "ok" else 0.0

            row = {
                "match_id": match_id,
                "match_date": match_date,
                "team_a": sdr_p.team_a,
                "team_b": sdr_p.team_b,
                "actual": actual_score,
                "actual_outcome": actual_out,
                "sdr_score": sdr_p.selected_score,
                "sdr_lambda_a": sdr_p.expected_goals_a,
                "sdr_lambda_b": sdr_p.expected_goals_b,
                "sdr_points": sdr_pts,
                "sdr_rps": sdr_rps,
                "elo_score": elo_p.selected_score if elo_p else None,
                "elo_lambda_a": elo_p.expected_goals_a if elo_p else None,
                "elo_lambda_b": elo_p.expected_goals_b if elo_p else None,
                "elo_points": elo_pts,
                "elo_rps": elo_rps,
            }
            results.append(row)

            if verbose:
                sdr_mark = "*" if sdr_p.selected_score == actual_score else ("+" if sdr_pts > 0 else " ")
                elo_mark = "*" if elo_p and elo_p.selected_score == actual_score else ("+" if elo_pts > 0 else " ")
                print(
                    f"  {(sdr_p.team_a or '?'):16s} vs {(sdr_p.team_b or '?'):16s} "
                    f"real={actual_score:5s}  "
                    f"sdr={sdr_p.selected_score or '?':5s}{sdr_mark}({sdr_pts:.0f}pt "
                    f"lg={sdr_p.expected_goals_a:.2f}-{sdr_p.expected_goals_b:.2f})  "
                    f"elo={elo_p.selected_score if elo_p else '?':5s}{elo_mark}({elo_pts:.0f}pt)"
                )

    n = len(results)
    if n == 0:
        print("  Sin partidos evaluados.")
        return {"year": year, "n": 0}

    sdr_pts_total = sum(r["sdr_points"] for r in results)
    elo_pts_total = sum(r["elo_points"] for r in results)
    sdr_exact = sum(1 for r in results if r["sdr_score"] == r["actual"])
    elo_exact = sum(1 for r in results if r["elo_score"] == r["actual"])
    sdr_margin = sum(1 for r in results if r["sdr_points"] >= float(scoring.get("same_margin_or_draw", scoring.get("margin_or_draw", 3))))
    elo_margin = sum(1 for r in results if r["elo_points"] >= float(scoring.get("same_margin_or_draw", scoring.get("margin_or_draw", 3))))
    sdr_win = sum(1 for r in results if r["sdr_score"] and outcome_1x2(*parse_score(r["sdr_score"])) == r["actual_outcome"])
    elo_win = sum(1 for r in results if r["elo_score"] and outcome_1x2(*parse_score(r["elo_score"])) == r["actual_outcome"])
    sdr_rps_mean = sum(r["sdr_rps"] for r in results) / n
    elo_rps_mean = sum(r["elo_rps"] for r in results) / n

    _print_table(n, sdr_pts_total, elo_pts_total, sdr_exact, elo_exact,
                 sdr_margin, elo_margin, sdr_win, elo_win, sdr_rps_mean, elo_rps_mean)

    return {
        "year": year,
        "n": n,
        "sdr": {
            "points": sdr_pts_total, "pts_per_match": round(sdr_pts_total / n, 3),
            "exact_hits": sdr_exact, "margin_hits": sdr_margin,
            "winner_hits": sdr_win, "rps": round(sdr_rps_mean, 5),
        },
        "elo_baseline": {
            "points": elo_pts_total, "pts_per_match": round(elo_pts_total / n, 3),
            "exact_hits": elo_exact, "margin_hits": elo_margin,
            "winner_hits": elo_win, "rps": round(elo_rps_mean, 5),
        },
        "delta_points": round(sdr_pts_total - elo_pts_total, 1),
        "matches": results,
    }


def _print_table(n, sp, ep, se, ee, sm, em, sw, ew, sr, er) -> None:
    print(f"\n  n = {n} partidos evaluados")
    print(f"  {'Métrica':<32s} {'SDR-Elo':>9s} {'Elo-base':>9s} {'Δ':>8s}")
    print(f"  {'-'*60}")
    print(f"  {'Puntos totales':<32s} {sp:>9.1f} {ep:>9.1f} {sp-ep:>+8.1f}")
    print(f"  {'Pts / partido':<32s} {sp/n:>9.3f} {ep/n:>9.3f} {(sp-ep)/n:>+8.3f}")
    print(f"  {'Marcadores exactos':<32s} {se:>9d} {ee:>9d} {se-ee:>+8d}")
    print(f"  {'Margen / empate':<32s} {sm:>9d} {em:>9d} {sm-em:>+8d}")
    print(f"  {'Aciertos 1X2':<32s} {sw:>9d} {ew:>9d} {sw-ew:>+8d}")
    print(f"  {'RPS (↓ mejor)':<32s} {sr:>9.4f} {er:>9.4f} {sr-er:>+8.4f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    scoring = _load_scoring_config(Path(args.scoring_config), args.scoring_profile)

    elo_config = {
        "model_id": "elo_poisson", "model_version": "0.2.2", "max_goals": 8,
        "goal_scale": 0.35, "home_advantage": 80.0, "initial_rating": 1500.0,
        "k_factor": 32.0, "max_expected_goals": 4.5, "min_expected_goals": 0.2,
        "min_importance_for_rating": 0.0,
    }
    sdr_config = {
        "model_id": "elo_sdr_poisson", "model_version": "0.1.0", "max_goals": 8,
        "sdr_method": args.sdr_method, "sdr_dims": args.sdr_dims,
        "n_lags": args.n_lags, "sdr_min_year": args.sdr_min_year,
        "min_lambda": 0.15, "max_lambda": 5.0,
    }

    print(f"\nExperimento SDR-Elo Poisson")
    print(f"  método  : {args.sdr_method.upper()}")
    print(f"  dims SDR: {args.sdr_dims}")
    print(f"  lags Elo: {args.n_lags} meses")
    print(f"  años WC : {args.years}")

    store = SQLiteStore(db_path)
    store.initialize()
    conn = store.conn
    history_run_id = conn.execute("SELECT history_run_id FROM v_latest_history_run").fetchone()[0]

    stage_filter = set(args.stages) if args.stages else None
    if stage_filter:
        print(f"  stages    : {sorted(stage_filter)}")

    all_results = []
    try:
        for year in args.years:
            yr = _run_year(
                year=year, conn=conn, history_run_id=history_run_id,
                db_path=db_path, scoring=scoring,
                elo_config=elo_config, sdr_config=sdr_config,
                verbose=args.verbose,
                stage_filter=stage_filter,
            )
            all_results.append(yr)
    finally:
        store.close()

    # Combined summary
    valid = [r for r in all_results if r.get("n", 0) > 0]
    if len(valid) > 1:
        total_n = sum(r["n"] for r in valid)
        sp = sum(r["sdr"]["points"] for r in valid)
        ep = sum(r["elo_baseline"]["points"] for r in valid)
        se = sum(r["sdr"]["exact_hits"] for r in valid)
        ee = sum(r["elo_baseline"]["exact_hits"] for r in valid)
        sm = sum(r["sdr"]["margin_hits"] for r in valid)
        em = sum(r["elo_baseline"]["margin_hits"] for r in valid)
        sw = sum(r["sdr"]["winner_hits"] for r in valid)
        ew = sum(r["elo_baseline"]["winner_hits"] for r in valid)
        sr = sum(r["sdr"]["rps"] * r["n"] for r in valid) / total_n
        er = sum(r["elo_baseline"]["rps"] * r["n"] for r in valid) / total_n

        print(f"\n{'='*62}")
        print(f"  COMBINADO {[r['year'] for r in valid]}  ({total_n} partidos)")
        print(f"{'='*62}")
        _print_table(total_n, sp, ep, se, ee, sm, em, sw, ew, sr, er)

        verdict = "OK SDR MEJORA" if sp > ep else "-- SDR no mejora"
        diff = sp - ep
        print(f"\n  Veredicto: {verdict} al baseline Elo-Poisson ({diff:+.1f} pts, {diff/total_n:+.3f} pts/partido)")

    output = {
        "experiment": {
            "sdr_method": args.sdr_method, "sdr_dims": args.sdr_dims,
            "n_lags": args.n_lags, "years": args.years,
        },
        "results_by_year": [{k: v for k, v in r.items() if k != "matches"} for r in all_results],
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  Guardado en: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
