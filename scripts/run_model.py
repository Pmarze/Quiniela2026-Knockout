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

from quiniela.ensemble import build_weighted_ensemble_predictions
from quiniela.models import (
    run_attack_defense_poisson,
    run_baseline_poisson,
    run_bayesian_monte_carlo_scoreline,
    run_bradley_terry_davidson,
    run_draw_specialist,
    run_elo_dixon_coles,
    run_elo_poisson,
    run_opta_power_poisson,
    run_similar_match_knn_scoreline,
)
from quiniela.models.common import (
    ModelContext,
    ModelPrediction,
    load_json_config,
    load_model_context,
    outcome_1x2,
    parse_score,
    store_predictions_in_sqlite,
    summarize_score_matrix,
    utc_now,
    write_prediction_artifacts,
)
from quiniela.models.neural_scoreline_mlp import run_neural_scoreline_mlp
from quiniela.models.neural_hybrid_v2 import run_neural_hybrid_v2
from quiniela.models.elo_sdr_poisson import run_elo_sdr_poisson
from quiniela.knockout import (
    apply_knockout_adjustments,
    build_knockout_consensus,
    compute_et_dual_picks,
    is_knockout_match,
    KnockoutResolution,
    resolve_knockout_outcome,
)
from quiniela.scoring.quiniela import resolve_scoring_profile


MODEL_RUNNERS = {
    "attack_defense_poisson": run_attack_defense_poisson,
    "baseline_poisson": run_baseline_poisson,
    "bayesian_monte_carlo_scoreline": run_bayesian_monte_carlo_scoreline,
    "bradley_terry_davidson": run_bradley_terry_davidson,
    "draw_specialist": run_draw_specialist,
    "elo_dixon_coles": run_elo_dixon_coles,
    "elo_poisson": run_elo_poisson,
    "elo_sdr_poisson": run_elo_sdr_poisson,
    "neural_hybrid_v2": run_neural_hybrid_v2,
    "neural_scoreline_mlp": run_neural_scoreline_mlp,
    "opta_power_poisson": run_opta_power_poisson,
    "similar_match_knn_scoreline": run_similar_match_knn_scoreline,
}


MODEL_FAMILIES = {
    "baseline_poisson": "control",
    "elo_poisson": "fuerza+goles",
    "elo_sdr_poisson": "SDR-Elo Poisson",
    "elo_dixon_coles": "marcadores bajos",
    "attack_defense_poisson": "ataque/defensa",
    "bayesian_monte_carlo_scoreline": "Monte Carlo limpio",
    "draw_specialist": "empates",
    "bradley_terry_davidson": "1X2+empate",
    "neural_hybrid_v2": "red neuronal hibrida",
    "neural_scoreline_mlp": "red neuronal",
    "opta_power_poisson": "Opta externo",
    "similar_match_knn_scoreline": "partidos similares",
    "weighted_ensemble": "ponderador",
    "weighted_points_ensemble": "ponderador puntos",
    "weighted_1x2_ensemble": "ponderador 1X2",
    "weighted_exact_ensemble": "ponderador exacto",
    "calibrated_scoreline_ensemble": "ponderador calibrado",
}

ENSEMBLE_MODEL_IDS = {
    "weighted_ensemble",
    "weighted_points_ensemble",
    "weighted_1x2_ensemble",
    "weighted_exact_ensemble",
    "calibrated_scoreline_ensemble",
}

# Modelos marcados como "referencia" en backtest: incluyen data leakage de redes neuronales
# entrenadas con el dataset completo. No se usan como Tier-1 en la selección dinámica,
# pero sí como fallback (Tier-2) si no hay modelos base disponibles.
_BACKTEST_REFERENCE_MODELS: frozenset[str] = frozenset({
    "baseline_poisson",
    "neural_scoreline_mlp",
    "neural_hybrid_v2",
    "opta_power_poisson",
    "weighted_ensemble",
    "weighted_points_ensemble",
    "weighted_1x2_ensemble",
    "weighted_exact_ensemble",
    "calibrated_scoreline_ensemble",
})

_PREFERRED_PICK_EXCLUDED_MODELS: frozenset[str] = frozenset({
    "similar_match_knn_scoreline",
})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta modelos activos y publica predicciones de quiniela."
    )
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "data" / "quiniela.db"),
        help="Ruta de la base SQLite.",
    )
    parser.add_argument(
        "--models-config",
        default=str(PROJECT_ROOT / "configs" / "models.yaml"),
        help="Configuracion de modelos activos.",
    )
    parser.add_argument(
        "--scoring-config",
        default=str(PROJECT_ROOT / "configs" / "scoring.yaml"),
        help="Reglas de puntaje de quiniela.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="model_id especifico a ejecutar. Puede repetirse. Si se omite, usa modelos activos.",
    )
    parser.add_argument(
        "--as-of-utc",
        default=None,
        help="Corte temporal ISO-8601. Si se omite, usa el as_of_utc del ultimo estado.",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "data" / "predictions"),
        help="Carpeta raiz de artefactos de prediccion.",
    )
    parser.add_argument(
        "--ui-overrides",
        default=str(PROJECT_ROOT / "data" / "ui" / "prediction_overrides.json"),
        help="JSON que consume el dashboard local.",
    )
    parser.add_argument(
        "--scoring-profile",
        default=None,
        help="Perfil de scoring (ej: 3-1-0). Default: perfil por defecto.",
    )
    parser.add_argument(
        "--knockout-config",
        default=str(PROJECT_ROOT / "configs" / "knockout.yaml"),
        help="Configuracion de ajustes knockout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models_config = load_json_config(Path(args.models_config))
    scoring_config_raw = load_json_config(Path(args.scoring_config))
    scoring_config = resolve_scoring_profile(scoring_config_raw, args.scoring_profile)
    knockout_config_path = Path(args.knockout_config)
    knockout_config = load_json_config(knockout_config_path) if knockout_config_path.exists() else {}
    knockout_enabled = knockout_config.get("enabled", False)
    selected_models = _select_models(models_config, args.model)
    base_model_configs = [model for model in selected_models if not _is_ensemble_model(model)]
    ensemble_model_configs = [model for model in selected_models if _is_ensemble_model(model)]
    prediction_run_id = f"pred_{utc_now().replace('-', '').replace(':', '').replace('+00:00', 'Z')}_{uuid.uuid4().hex[:8]}"
    context = load_model_context(Path(args.db), prediction_run_id=prediction_run_id, as_of_utc=args.as_of_utc)
    output_dir = Path(args.output_root) / prediction_run_id

    print(f"prediction_run_id: {prediction_run_id}")
    print(f"as_of_utc: {context.as_of_utc}")
    print(f"training_data_version: {context.training_data_version}")
    print(f"tournament_state_id: {context.tournament_state_id}")
    print(f"training_matches: {len(context.training_matches)}")
    print(f"prediction_matches: {len(context.prediction_matches)}")

    predictions_by_model: dict[str, list[ModelPrediction]] = {}
    knockout_resolutions: dict[str, list[KnockoutResolution]] = {}
    et_dual_picks: dict[str, dict[str, Any]] = {}
    for model_config in base_model_configs:
        model_id = model_config["model_id"]
        runner = MODEL_RUNNERS.get(model_id)
        if runner is None:
            if model_config.get("required"):
                raise RuntimeError(f"Modelo requerido no implementado: {model_id}")
            print(f"{model_id}: skipped (no implementado)")
            continue
        predictions = runner(context, model_config, scoring_config)
        model_version = str(model_config.get("model_version", "0.1.0"))
        ok = sum(1 for prediction in predictions if prediction.status == "ok")
        masked = sum(1 for prediction in predictions if prediction.status == "masked")
        failed = len(predictions) - ok - masked
        json_path, csv_path = write_prediction_artifacts(
            output_dir=output_dir,
            model_id=model_id,
            model_version=model_version,
            context=context,
            predictions=predictions,
            notes=f"{model_id} generated by scripts/run_model.py",
        )
        store_predictions_in_sqlite(
            db_path=Path(args.db),
            model_id=model_id,
            model_version=model_version,
            context=context,
            predictions=predictions,
            json_path=json_path,
            csv_path=csv_path,
            notes=f"{model_id} generated by scripts/run_model.py",
        )
        if knockout_enabled:
            predictions, ko_res, et_picks = _apply_knockout_layer(
                predictions, context, knockout_config, scoring_config,
            )
            for sid, res in ko_res.items():
                knockout_resolutions.setdefault(sid, []).append(res)
            for sid, model_picks in et_picks.items():
                et_dual_picks.setdefault(sid, {}).update(model_picks)
        predictions_by_model[model_id] = predictions
        print(f"{model_id}: ok={ok} masked={masked} failed={failed} json={json_path}")

    for model_config in ensemble_model_configs:
        model_id = str(model_config["model_id"])
        predictions = build_weighted_ensemble_predictions(
            context=context,
            predictions_by_model=predictions_by_model,
            model_config=model_config,
            scoring_config=scoring_config,
        )
        model_version = str(model_config.get("model_version", "0.1.0"))
        ok = sum(1 for prediction in predictions if prediction.status == "ok")
        masked = sum(1 for prediction in predictions if prediction.status == "masked")
        failed = len(predictions) - ok - masked
        json_path, csv_path = write_prediction_artifacts(
            output_dir=output_dir,
            model_id=model_id,
            model_version=model_version,
            context=context,
            predictions=predictions,
            notes=f"{model_id} generated by scripts/run_model.py",
        )
        store_predictions_in_sqlite(
            db_path=Path(args.db),
            model_id=model_id,
            model_version=model_version,
            context=context,
            predictions=predictions,
            json_path=json_path,
            csv_path=csv_path,
            notes=f"{model_id} generated by scripts/run_model.py",
        )
        if knockout_enabled:
            predictions, ko_res, et_picks = _apply_knockout_layer(
                predictions, context, knockout_config, scoring_config,
            )
            for sid, res in ko_res.items():
                knockout_resolutions.setdefault(sid, []).append(res)
            for sid, model_picks in et_picks.items():
                et_dual_picks.setdefault(sid, {}).update(model_picks)
        predictions_by_model[model_id] = predictions
        print(f"{model_id}: ok={ok} masked={masked} failed={failed} json={json_path}")

    if predictions_by_model:
        ui_path = Path(args.ui_overrides)
        preferred_model_id = _select_preferred_model_id(
            db_path=Path(args.db),
            ui_path=ui_path,
            predictions_by_model=predictions_by_model,
            fallback_model_id=str(models_config.get("default_quiniela_model_id", "")),
            scoring_config=scoring_config,
        )
        write_ui_overrides(
            ui_path=ui_path,
            context=context,
            predictions_by_model=predictions_by_model,
            preferred_model_id=preferred_model_id,
            knockout_resolutions=knockout_resolutions if knockout_enabled else {},
            knockout_config=knockout_config if knockout_enabled else {},
            et_dual_picks=et_dual_picks if knockout_enabled else {},
        )
        print(f"ui_overrides: {ui_path}")
    else:
        print("no se generaron predicciones")
        return 1

    return 0


def _select_models(models_config: dict[str, Any], requested: list[str] | None) -> list[dict[str, Any]]:
    models = list(models_config.get("models", []))
    if requested:
        requested_set = set(requested)
        selected = [model for model in models if model.get("model_id") in requested_set]
        missing = requested_set - {model.get("model_id") for model in selected}
        if missing:
            raise RuntimeError(f"Modelos no definidos en configs/models.yaml: {', '.join(sorted(missing))}")
        return selected
    return [model for model in models if model.get("active")]


def _is_ensemble_model(model_config: dict[str, Any]) -> bool:
    return bool(model_config.get("ensemble")) or str(model_config.get("model_id")) in ENSEMBLE_MODEL_IDS


def _select_preferred_model_id(
    db_path: Path,
    ui_path: Path,
    predictions_by_model: dict[str, list[ModelPrediction]],
    fallback_model_id: str,
    scoring_config: dict[str, Any],
) -> str:
    """Elige dinamicamente el modelo preferido para el pick operativo.

    Cuando hay resultados reales del torneo, usa el ranking vivo 2026 calculado
    con predicciones congeladas antes de cada partido. Antes del primer resultado,
    usa el backtest mas reciente y finalmente el default del config.
    """
    import sqlite3 as _sqlite3

    available = set(predictions_by_model.keys())
    if not available:
        return fallback_model_id

    current_pick = _select_current_tournament_model_id(
        db_path=db_path,
        ui_path=ui_path,
        available_order=list(predictions_by_model.keys()),
        scoring_config=scoring_config,
    )
    if current_pick:
        return current_pick

    try:
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT model_id, total_quiniela_points, brier_1x2
            FROM v_latest_backtest_model_metrics
            WHERE year = 'all'
            ORDER BY total_quiniela_points DESC, brier_1x2 ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as exc:
        print(f"[preferred_model] backtest query failed ({exc}), usando fallback: {fallback_model_id}")
        return fallback_model_id

    if not rows:
        print(f"[preferred_model] sin métricas en backtest, usando fallback: {fallback_model_id}")
        return fallback_model_id

    tiers = [
        ("clean", [
            r for r in rows
            if r["model_id"] not in _BACKTEST_REFERENCE_MODELS
            and r["model_id"] not in _PREFERRED_PICK_EXCLUDED_MODELS
        ]),
        ("all", [r for r in rows if r["model_id"] not in _PREFERRED_PICK_EXCLUDED_MODELS]),
    ]
    for tier_label, candidates in tiers:
        for r in candidates:
            mid = r["model_id"]
            if mid in available:
                pts = r["total_quiniela_points"]
                brier = r["brier_1x2"]
                print(f"[preferred_model] elegido={mid}  pts={pts:.0f}  brier={brier:.4f}  tier={tier_label}")
                return mid

    print(f"[preferred_model] ningún modelo con backtest disponible, usando fallback: {fallback_model_id}")
    return fallback_model_id


def _select_current_tournament_model_id(
    db_path: Path,
    ui_path: Path,
    available_order: list[str],
    scoring_config: dict[str, Any],
) -> str | None:
    import sqlite3 as _sqlite3

    available = set(available_order)
    model_order = {model_id: idx for idx, model_id in enumerate(available_order)}
    if not available:
        return None

    existing = _load_existing_ui_overrides(ui_path)
    existing_matches = existing.get("matches", {})
    if not isinstance(existing_matches, dict) or not existing_matches:
        return None

    try:
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_match_id, home_score, away_score
            FROM v_latest_state_matches
            WHERE LOWER(COALESCE(status, '')) IN ('completed', 'finished')
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            ORDER BY COALESCE(match_number, CAST(source_match_id AS INTEGER))
            """
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"[preferred_model] current tournament query failed ({exc}); usando backtest")
        return None

    if not rows:
        return None

    stats: dict[str, dict[str, float | int]] = {
        model_id: {
            "pts": 0.0,
            "exact": 0,
            "hits": 0,
            "played": 0,
            "order": model_order.get(model_id, 9999),
        }
        for model_id in available_order
        if model_id not in _PREFERRED_PICK_EXCLUDED_MODELS
    }

    for row in rows:
        source_match_id = str(row["source_match_id"])
        prior = existing_matches.get(source_match_id) or {}
        model_predictions = prior.get("model_predictions") or []
        if not isinstance(model_predictions, list):
            continue
        result = f"{int(row['home_score'])}-{int(row['away_score'])}"
        for prediction in model_predictions:
            if not isinstance(prediction, dict):
                continue
            model_id = str(prediction.get("model_id") or "")
            if model_id not in stats or model_id not in available:
                continue
            score = prediction.get("score")
            if not score:
                continue
            try:
                pts, exact, hit = _current_pick_score(str(score), result, scoring_config)
            except Exception:
                continue
            stats[model_id]["pts"] = float(stats[model_id]["pts"]) + pts
            stats[model_id]["exact"] = int(stats[model_id]["exact"]) + exact
            stats[model_id]["hits"] = int(stats[model_id]["hits"]) + hit
            stats[model_id]["played"] = int(stats[model_id]["played"]) + 1

    ranked = [
        (model_id, stat)
        for model_id, stat in stats.items()
        if int(stat["played"]) > 0
    ]
    if not ranked:
        return None

    ranked.sort(
        key=lambda item: (
            -float(item[1]["pts"]),
            -int(item[1]["exact"]),
            -int(item[1]["hits"]),
            int(item[1]["order"]),
        )
    )
    best_id, best = ranked[0]
    print(
        "[preferred_model] "
        f"elegido={best_id}  pts_actuales={float(best['pts']):.0f}  "
        f"exactos={int(best['exact'])}  partidos={int(best['played'])}  "
        "tier=current_2026"
    )
    return best_id


def _current_pick_score(
    pick: str,
    result: str,
    scoring_config: dict[str, Any],
) -> tuple[float, int, int]:
    pick_a, pick_b = parse_score(pick)
    result_a, result_b = parse_score(result)
    exact_points = float(scoring_config.get("exact_score", 5))
    margin_points = float(scoring_config.get("same_margin_or_draw", scoring_config.get("margin_or_draw", 3)))
    winner_points = float(scoring_config.get("winner", 1))

    if pick_a == result_a and pick_b == result_b:
        return exact_points, 1, 1
    if (pick_a - pick_b) == (result_a - result_b):
        return margin_points, 0, 1 if margin_points > 0 else 0
    if outcome_1x2(pick_a, pick_b) == outcome_1x2(result_a, result_b):
        return winner_points, 0, 1 if winner_points > 0 else 0
    return 0.0, 0, 0


def write_ui_overrides(
    ui_path: Path,
    context: ModelContext,
    predictions_by_model: dict[str, list[ModelPrediction]],
    preferred_model_id: str,
    knockout_resolutions: dict[str, list[KnockoutResolution]] | None = None,
    knockout_config: dict[str, Any] | None = None,
    et_dual_picks: dict[str, dict[str, Any]] | None = None,
) -> None:
    existing = _load_existing_ui_overrides(ui_path)
    existing_matches = existing.get("matches", {})
    matches: dict[str, dict[str, Any]] = {}
    model_order = list(predictions_by_model)

    completed_ids = {
        str(pm.source_match_id)
        for pm in context.prediction_matches
        if pm.status in ("completed", "finished")
    }
    preferred = preferred_model_id if preferred_model_id in predictions_by_model else None

    by_source_match: dict[str, dict[str, ModelPrediction]] = {}
    for model_id, predictions in predictions_by_model.items():
        for prediction in predictions:
            by_source_match.setdefault(prediction.source_match_id, {})[model_id] = prediction

    for source_match_id, model_predictions in by_source_match.items():
        prior = existing_matches.get(source_match_id, {})
        frozen = bool(prior.get("frozen_pick"))
        is_completed = source_match_id in completed_ids

        if frozen and prior.get("model_predictions"):
            matches[source_match_id] = prior
            continue

        prior_predictions = {
            str(item.get("model_id")): item
            for item in list(prior.get("model_predictions") or [])
            if item.get("model_id")
        }
        current_predictions = {
            prediction.model_id: _dashboard_model_prediction(prediction)
            for prediction in model_predictions.values()
            if prediction.status == "ok"
        }
        merged_predictions = {**prior_predictions, **current_predictions}

        preferred_prediction = model_predictions.get(preferred) if preferred else None
        if frozen and prior.get("quiniela_pick"):
            quiniela_pick = prior["quiniela_pick"]
        elif preferred_prediction and preferred_prediction.status == "ok":
            quiniela_pick = {
                "model_id": preferred_prediction.model_id,
                "score": preferred_prediction.selected_score,
                "expected_points": preferred_prediction.selected_expected_points,
                "top_score": preferred_prediction.top_score,
                "top_score_probability": preferred_prediction.top_score_probability,
            }
        elif prior.get("quiniela_pick"):
            quiniela_pick = prior["quiniela_pick"]
        else:
            quiniela_pick = None

        should_freeze = is_completed and not frozen
        if should_freeze and prior.get("model_predictions"):
            prior["frozen_pick"] = True
            matches[source_match_id] = prior
            continue

        matches[source_match_id] = {
            "quiniela_pick": quiniela_pick,
            "frozen_pick": frozen or should_freeze,
            "model_predictions": list(merged_predictions.values()),
            "notes": f"prediction_run_id={context.prediction_run_id}",
        }

    ko_data = knockout_resolutions or {}
    ko_cfg = knockout_config or {}
    for sid, resolutions in ko_data.items():
        if sid in matches and resolutions:
            matches[sid]["knockout_resolution"] = {
                "consensus": build_knockout_consensus(resolutions, ko_cfg),
                "per_model": [r.to_dict() for r in resolutions],
            }

    et_data = et_dual_picks or {}
    for sid, model_picks in et_data.items():
        if sid in matches and not matches[sid].get("frozen_pick"):
            preferred_et = model_picks.get(preferred_model_id) or next(iter(model_picks.values()), None)
            if preferred_et:
                matches[sid]["et_dual_pick"] = {
                    "pick_90min": preferred_et["pick_90min"],
                    "ep_90min": preferred_et["ep_90min"],
                    "pick_aet": preferred_et["pick_aet"],
                    "ep_aet": preferred_et["ep_aet"],
                    "per_model": {
                        mid: {
                            "pick_90min": p["pick_90min"], "pick_aet": p["pick_aet"],
                            "score": p.get("score", p["pick_90min"]),
                            "top": p.get("top", p["pick_90min"]),
                            "ev": p.get("ev", p.get("ep_90min", 0)),
                            "p1": p.get("p1", 0), "px": p.get("px", 0), "p2": p.get("p2", 0),
                            "out": p.get("out", "X"),
                            "ko_resolution": p.get("ko_resolution"),
                            "aet_score": p.get("aet_score", p["pick_aet"]),
                            "aet_top": p.get("aet_top", p["pick_aet"]),
                            "aet_ev": p.get("aet_ev", p.get("ep_aet", 0)),
                            "aet_p1": p.get("aet_p1", 0), "aet_px": p.get("aet_px", 0),
                            "aet_p2": p.get("aet_p2", 0), "aet_out": p.get("aet_out", "1"),
                        }
                        for mid, p in model_picks.items()
                    },
                }

    for emid, edata in existing_matches.items():
        if emid not in matches:
            matches[emid] = edata

    payload = {
        "generated_at_utc": utc_now(),
        "prediction_run_id": context.prediction_run_id,
        "as_of_utc": context.as_of_utc,
        "matches": matches,
    }
    ui_path.parent.mkdir(parents=True, exist_ok=True)
    ui_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _dashboard_model_prediction(prediction: ModelPrediction) -> dict[str, Any]:
    p_values = [
        ("1", prediction.p_team_a_win or 0.0),
        ("X", prediction.p_draw or 0.0),
        ("2", prediction.p_team_b_win or 0.0),
    ]
    outcome, confidence = max(p_values, key=lambda item: item[1])
    matrix_summary = summarize_score_matrix(prediction.score_matrix or {"scores": {}})
    return {
        "model_id": prediction.model_id,
        "family": MODEL_FAMILIES.get(prediction.model_id, "modelo"),
        "score": prediction.selected_score,
        "top_score": prediction.top_score,
        "outcome": outcome,
        "confidence": round(confidence, 4),
        "expected_goals": f"{prediction.expected_goals_a:.2f}-{prediction.expected_goals_b:.2f}",
        "p_team_a_win": prediction.p_team_a_win,
        "p_draw": prediction.p_draw,
        "p_team_b_win": prediction.p_team_b_win,
        "top_score_probability": matrix_summary.get("top_score_probability"),
        "expected_points": prediction.selected_expected_points,
        "notes": "\n".join(prediction.warnings),
    }


def _apply_knockout_layer(
    predictions: list[ModelPrediction],
    context: ModelContext,
    knockout_config: dict[str, Any],
    scoring_config: dict[str, Any],
) -> tuple[list[ModelPrediction], dict[str, KnockoutResolution], dict[str, dict[str, Any]]]:
    match_by_id = {pm.match_id: pm for pm in context.prediction_matches}
    adjusted: list[ModelPrediction] = []
    resolutions: dict[str, KnockoutResolution] = {}
    et_picks: dict[str, dict[str, Any]] = {}
    for pred in predictions:
        match = match_by_id.get(pred.match_id)
        if match and is_knockout_match(match) and pred.status == "ok":
            adj = apply_knockout_adjustments(pred, match, context, knockout_config, scoring_config)
            res = resolve_knockout_outcome(adj, knockout_config)
            adjusted.append(adj)
            if res:
                resolutions[pred.source_match_id] = res
            dual = compute_et_dual_picks(pred, knockout_config, scoring_config)
            if dual:
                et_picks.setdefault(pred.source_match_id, {})[pred.model_id] = dual
        else:
            adjusted.append(pred)
    return adjusted, resolutions, et_picks


def _load_existing_ui_overrides(ui_path: Path) -> dict[str, Any]:
    if not ui_path.exists():
        return {"matches": {}}
    return json.loads(ui_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
