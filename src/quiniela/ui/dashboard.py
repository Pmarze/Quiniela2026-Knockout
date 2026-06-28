from __future__ import annotations

import html
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quiniela.models.common import load_json_config
from quiniela.scoring.quiniela import list_scoring_profiles, resolve_scoring_profile
from quiniela.storage.sqlite_store import SQLiteStore


# ─── Constantes ───────────────────────────────────────────────────────────────

_FAMILY_BY_MODEL_ID: dict[str, dict[str, str]] = {
    "baseline_poisson":               {"family": "CONTROL",          "fb": "fb-ctrl"},
    "elo_poisson":                    {"family": "FUERZA+GOLES",     "fb": "fb-fgol"},
    "elo_dixon_coles":                {"family": "MARCADORES BAJOS", "fb": "fb-mba"},
    "attack_defense_poisson":         {"family": "ATAQUE/DEFENSA",   "fb": "fb-atd"},
    "draw_specialist":                {"family": "EMPATES",          "fb": "fb-emp"},
    "bradley_terry_davidson":         {"family": "1X2+EMPATE",       "fb": "fb-1x2"},
    "neural_hybrid_v2":               {"family": "RED NEURONAL HIB.","fb": "fb-neu"},
    "neural_scoreline_mlp":           {"family": "RED NEURONAL",     "fb": "fb-neu"},
    "weighted_ensemble":              {"family": "PONDERADOR",       "fb": "fb-pond"},
    "weighted_points_ensemble":       {"family": "PONDERADOR PUNT.", "fb": "fb-pond"},
    "weighted_1x2_ensemble":          {"family": "PONDERADOR 1X2",   "fb": "fb-pond"},
    "weighted_exact_ensemble":        {"family": "PONDERADOR EXAC.", "fb": "fb-pond"},
    "calibrated_scoreline_ensemble":  {"family": "POND. CALIBRADO",  "fb": "fb-pond"},
    # Modelos adicionales presentes en prediction_overrides.json
    "bayesian_monte_carlo_scoreline": {"family": "MONTE CARLO",      "fb": "fb-mba"},
    "opta_power_poisson":             {"family": "OPTA EXTERNO",     "fb": "fb-fgol"},
    "similar_match_knn_scoreline":     {"family": "PARTIDOS SIM.",    "fb": "fb-sim"},
}

_STAGE_TO_PHASE: dict[str, str] = {
    "group":        "group",
    "round_of_32":  "r32",
    "round_of_16":  "r16",
    "quarter":      "qf",
    "quarter_final": "qf",
    "semi":         "sf",
    "semi_final":   "sf",
    "final":        "final",
    "third_place":  "3rd",
}

_FRIENDLY_PREP_WINDOW_START = "2026-06-01"
_FRIENDLY_PREP_WINDOW_END = "2026-06-09"
_FRIENDLY_GOAL_REFERENCE_MATCHES = 10
_FRIENDLY_GOAL_HALFLIFE_MATCHES = 4.0
_FRIENDLY_GOAL_SHRINK_MATCHES = 8.0
# Partidos de torneo ya jugados cuentan más que un amistoso de preparación
_TOURNAMENT_GOAL_WEIGHT_MULTIPLIER = 2.0


@dataclass(frozen=True)
class DashboardResult:
    output_path: Path
    state_id: str
    as_of_utc: str
    total_matches: int
    completed_matches: int
    pending_matches: int
    teams: int
    groups: int


def generate_dashboard(
    db_path: Path,
    project_root: Path,
    output_path: Path | None = None,
    predictions_path: Path | None = None,
    friends_path: Path | None = None,
    scoring_config_path: Path | None = None,
    public_mode: bool = False,
    private_access_hash: str | None = None,
) -> DashboardResult:
    store = SQLiteStore(db_path)
    store.initialize()
    conn = store.conn
    try:
        state        = _load_latest_state(conn)
        matches      = _load_matches(conn)
        group_tables = _load_group_tables(conn)
        recent_friendlies, friendly_coverage = _load_recent_friendlies(conn, matches)
        predictions  = _load_prediction_overrides(predictions_path)
        backtest     = _load_backtest_data(conn)
        friends      = [] if public_mode else _load_friends_quinielas(friends_path)
        scoring_profiles = _discover_scoring_profiles(
            project_root, scoring_config_path, predictions_path,
        )
        payload      = _build_unified_payload(
            state, matches, group_tables, predictions, backtest, friends,
            scoring_profiles=scoring_profiles,
            recent_friendlies=recent_friendlies,
            friendly_coverage=friendly_coverage,
            public_mode=public_mode,
            private_access_hash=private_access_hash,
        )
    finally:
        store.close()

    resolved_output = output_path or (project_root / "docs" / "index.html")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(_render_html(payload), encoding="utf-8")

    return DashboardResult(
        output_path=resolved_output,
        state_id=state["state_id"],
        as_of_utc=state["as_of_utc"],
        total_matches=state["total_matches"],
        completed_matches=state["completed_matches"],
        pending_matches=state["pending_matches"],
        teams=state["teams"],
        groups=state["groups"],
    )


def _load_latest_state(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM v_latest_tournament_state").fetchone()
    if row is None:
        raise RuntimeError("No hay estado vigente. Ejecuta scripts/build_state.py primero.")
    return dict(row)


def _load_matches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM v_latest_state_matches
        ORDER BY COALESCE(match_number, CAST(source_match_id AS INTEGER))
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _load_group_tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM v_latest_state_group_tables
        ORDER BY group_name, rank_sort
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _load_team_form(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM v_latest_state_team_form
        ORDER BY group_name, team_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _load_prediction_overrides(predictions_path: Path | None) -> dict[str, Any]:
    if predictions_path is None or not predictions_path.exists():
        return {"matches": {}}
    return json.loads(predictions_path.read_text(encoding="utf-8"))


def _load_friends_quinielas(friends_path: Path | None) -> list[dict[str, Any]]:
    if friends_path is None or not friends_path.exists():
        return []
    try:
        data = json.loads(friends_path.read_text(encoding="utf-8"))
        return data.get("friends", [])
    except Exception:
        return []


def _load_recent_friendlies(
    conn: sqlite3.Connection,
    matches: list[dict[str, Any]],
    limit: int = 5,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    coverage: dict[str, Any] = {
        "max_date": None,
        "total": 0,
        "prep_window_start": _FRIENDLY_PREP_WINDOW_START,
        "prep_window_end": _FRIENDLY_PREP_WINDOW_END,
        "prep_window_count": 0,
        "goal_reference_matches": _FRIENDLY_GOAL_REFERENCE_MATCHES,
        "goal_reference_halflife": _FRIENDLY_GOAL_HALFLIFE_MATCHES,
    }
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                MAX(match_date) AS max_date,
                SUM(CASE WHEN match_date BETWEEN ? AND ? THEN 1 ELSE 0 END) AS prep_window_count,
                AVG((home_score + away_score) / 2.0) AS avg_goals_per_team
            FROM v_canonical_historical_matches
            WHERE is_friendly = 1
            """,
            (_FRIENDLY_PREP_WINDOW_START, _FRIENDLY_PREP_WINDOW_END),
        ).fetchone()
    except sqlite3.OperationalError:
        return {}, coverage

    if row is not None:
        coverage["total"] = int(row["total"] or 0)
        coverage["max_date"] = row["max_date"]
        coverage["prep_window_count"] = int(row["prep_window_count"] or 0)
        coverage["avg_goals_per_team"] = round(float(row["avg_goals_per_team"] or 1.35), 3)

    team_ids = sorted({
        str(team_id)
        for match in matches
        for team_id in (match.get("team_a_canonical_id"), match.get("team_b_canonical_id"))
        if team_id
    })
    if not team_ids:
        return {}, coverage

    placeholders = ",".join("?" for _ in team_ids)
    rows = conn.execute(
        f"""
        SELECT
            historical_match_id,
            match_date,
            team_a_name,
            team_b_name,
            team_a_canonical_id,
            team_b_canonical_id,
            home_score,
            away_score,
            city,
            country,
            neutral
        FROM v_canonical_historical_matches
        WHERE is_friendly = 1
          AND (
              team_a_canonical_id IN ({placeholders})
              OR team_b_canonical_id IN ({placeholders})
          )
        ORDER BY match_date DESC, historical_match_id DESC
        """,
        (*team_ids, *team_ids),
    ).fetchall()

    # ── Partidos del torneo ya jugados, por equipo (más recientes primero) ──────
    # Se usan para el cálculo de goal_ref ANTES de los amistosos, con mayor peso,
    # porque representan el rendimiento real en la competición actual.
    tournament_goal_samples: dict[str, list[dict[str, float]]] = {tid: [] for tid in team_ids}
    for m in matches:
        if m.get("status") not in ("finished", "completed"):
            continue
        hs = m.get("home_score")
        aws = m.get("away_score")
        if hs is None or aws is None:
            continue
        hs, aws = int(hs), int(aws)
        perspectives = (
            (str(m.get("team_a_canonical_id") or ""), hs, aws),
            (str(m.get("team_b_canonical_id") or ""), aws, hs),
        )
        for tid, gf, ga in perspectives:
            if tid and tid in tournament_goal_samples:
                tournament_goal_samples[tid].append({
                    "goals_for":    float(gf),
                    "goals_against": float(ga),
                    "scored":    1.0 if gf > 0 else 0.0,
                    "conceded":  1.0 if ga > 0 else 0.0,
                })

    friendlies: dict[str, dict[str, Any]] = {
        team_id: {"matches": [], "goal_ref": _empty_goal_reference(coverage)}
        for team_id in team_ids
    }
    seen: dict[str, set[str]] = {team_id: set() for team_id in team_ids}
    # goal_samples ahora se llena DESPUÉS de insertar los del torneo
    # Invertir para que el partido más reciente quede en índice 0 (mayor peso en decay)
    goal_samples: dict[str, list[dict[str, float]]] = {
        team_id: list(reversed(tournament_goal_samples[team_id]))
        for team_id in team_ids
    }
    goal_multipliers: dict[str, list[float]] = {
        team_id: [_TOURNAMENT_GOAL_WEIGHT_MULTIPLIER] * len(tournament_goal_samples[team_id])
        for team_id in team_ids
    }

    for row in rows:
        row_id = row["historical_match_id"]
        perspectives = (
            (True, row["team_a_canonical_id"], row["team_b_name"], row["home_score"], row["away_score"]),
            (False, row["team_b_canonical_id"], row["team_a_name"], row["away_score"], row["home_score"]),
        )
        for is_home, team_id, opponent, goals_for, goals_against in perspectives:
            if not team_id or team_id not in friendlies:
                continue
            if row_id in seen[team_id]:
                continue
            goals_for = int(goals_for)
            goals_against = int(goals_against)
            # Rellenar hasta el cap con amistosos (el torneo ya ocupa las primeras posiciones)
            if len(goal_samples[team_id]) < _FRIENDLY_GOAL_REFERENCE_MATCHES:
                goal_samples[team_id].append({
                    "goals_for": float(goals_for),
                    "goals_against": float(goals_against),
                    "scored": 1.0 if goals_for > 0 else 0.0,
                    "conceded": 1.0 if goals_against > 0 else 0.0,
                })
                goal_multipliers[team_id].append(1.0)  # amistoso: peso estándar
            if len(friendlies[team_id]["matches"]) >= limit:
                seen[team_id].add(row_id)
                continue
            if goals_for > goals_against:
                result = "G"
            elif goals_for < goals_against:
                result = "P"
            else:
                result = "E"
            venue = ", ".join(part for part in (row["city"], row["country"]) if part)
            side = "N" if row["neutral"] == 1 else ("L" if is_home else "V")
            friendlies[team_id]["matches"].append({
                "date": row["match_date"],
                "opponent": opponent,
                "score": f"{goals_for}-{goals_against}",
                "result": result,
                "side": side,
                "venue": venue,
                "home": row["team_a_name"],
                "away": row["team_b_name"],
                "actual_score": f"{row['home_score']}-{row['away_score']}",
            })
            seen[team_id].add(row_id)

    for team_id, samples in goal_samples.items():
        mults = goal_multipliers[team_id]
        friendlies[team_id]["goal_ref"] = _weighted_goal_reference(samples, coverage, mults)

    return friendlies, coverage


def _empty_goal_reference(coverage: dict[str, Any]) -> dict[str, Any]:
    baseline = float(coverage.get("avg_goals_per_team") or 1.35)
    return {
        "n": 0,
        "weight": 0.0,
        "confidence": 0.0,
        "gf": round(baseline, 3),
        "ga": round(baseline, 3),
        "p_scored_recent": round(1.0 - math.exp(-baseline), 4),
        "p_conceded_recent": round(1.0 - math.exp(-baseline), 4),
    }


def _weighted_goal_reference(
    samples: list[dict[str, float]],
    coverage: dict[str, Any],
    multipliers: list[float] | None = None,
) -> dict[str, Any]:
    baseline = float(coverage.get("avg_goals_per_team") or 1.35)
    if not samples:
        return _empty_goal_reference(coverage)

    if multipliers is None:
        multipliers = [1.0] * len(samples)
    decay = math.log(2.0) / _FRIENDLY_GOAL_HALFLIFE_MATCHES
    # Decay by position, then scale by per-sample multiplier (tournament matches get higher base)
    weights = [mult * math.exp(-decay * idx) for idx, mult in enumerate(multipliers)]
    total_weight = sum(weights)
    weighted_gf = sum(w * sample["goals_for"] for w, sample in zip(weights, samples)) / total_weight
    weighted_ga = sum(w * sample["goals_against"] for w, sample in zip(weights, samples)) / total_weight
    p_scored = sum(w * sample["scored"] for w, sample in zip(weights, samples)) / total_weight
    p_conceded = sum(w * sample["conceded"] for w, sample in zip(weights, samples)) / total_weight
    # Confidence based on effective sample count (tournament matches count double)
    eff_n = sum(multipliers)
    confidence = min(1.0, eff_n / _FRIENDLY_GOAL_SHRINK_MATCHES)
    gf = confidence * weighted_gf + (1.0 - confidence) * baseline
    ga = confidence * weighted_ga + (1.0 - confidence) * baseline
    p_scored_adj = confidence * p_scored + (1.0 - confidence) * (1.0 - math.exp(-baseline))
    p_conceded_adj = confidence * p_conceded + (1.0 - confidence) * (1.0 - math.exp(-baseline))
    return {
        "n": len(samples),
        "weight": round(total_weight, 3),
        "confidence": round(confidence, 3),
        "gf": round(gf, 3),
        "ga": round(ga, 3),
        "p_scored_recent": round(p_scored_adj, 4),
        "p_conceded_recent": round(p_conceded_adj, 4),
    }


def _matchup_goal_reference(
    team_ref: dict[str, Any] | None,
    opponent_ref: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline = float((coverage or {}).get("avg_goals_per_team") or 1.35)
    team_gf = float((team_ref or {}).get("gf", baseline))
    opponent_ga = float((opponent_ref or {}).get("ga", baseline))
    lam = max(0.05, min(4.0, (team_gf + opponent_ga) / 2.0))
    return {
        "lambda": round(lam, 3),
        "p_goal": round(1.0 - math.exp(-lam), 4),
        "gf": round(team_gf, 3),
        "opp_ga": round(opponent_ga, 3),
        "n": int((team_ref or {}).get("n") or 0),
    }


def _load_backtest_data(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Carga métricas y predicciones del backtest más reciente. Retorna None si no hay datos."""
    try:
        run_row = conn.execute("SELECT * FROM v_latest_backtest_run").fetchone()
    except Exception:
        return None
    if run_row is None:
        return None
    run = dict(run_row)

    # Métricas agregadas (solo year = 'all')
    metrics_rows = conn.execute("""
        SELECT *
        FROM v_latest_backtest_model_metrics
        WHERE year = 'all'
        ORDER BY total_quiniela_points DESC, exact_hits DESC, model_id
    """).fetchall()
    metrics_all = [dict(r) for r in metrics_rows]

    # Todas las predicciones individuales
    pred_rows = conn.execute("""
        SELECT *
        FROM v_latest_backtest_predictions
        ORDER BY year, match_number, model_id
    """).fetchall()
    predictions_raw = [dict(r) for r in pred_rows]

    # Calcular agregados por modelo que no están en la tabla de métricas
    prob_pts: dict[str, int] = {}
    exact_prob: dict[str, int] = {}
    winner_hits: dict[str, int] = {}
    draw_picks: dict[str, int] = {}

    for p in predictions_raw:
        mid = p["model_id"]
        prob_pts[mid]    = prob_pts.get(mid, 0)    + (p.get("top_actual_points") or 0)
        exact_prob[mid]  = exact_prob.get(mid, 0)  + (1 if p.get("top_exact_hit") else 0)
        winner_hits[mid] = winner_hits.get(mid, 0) + (1 if p.get("winner_hit") else 0)
        draw_picks[mid]  = draw_picks.get(mid, 0)  + (1 if p.get("selected_outcome") == "X" else 0)

    # Modelos de referencia (entrenados con datos del torneo evaluado)
    run_config: dict[str, Any] = json.loads(run.get("config_json") or "{}")
    reference_models: set[str] = set(run_config.get("reference_models") or [])

    # Lista de años únicos
    years = sorted({int(p["year"]) for p in predictions_raw} if predictions_raw else [])

    # Construir lista metrics en formato del diseño
    metrics = []
    for m in metrics_all:
        mid     = m["model_id"]
        fi      = _FAMILY_BY_MODEL_ID.get(mid, {"family": mid.upper()[:12], "fb": "fb-ctrl"})
        pts     = m.get("total_quiniela_points") or 0
        max_pts = m.get("max_possible_points") or 1
        pp      = prob_pts.get(mid, 0)
        ex      = m.get("exact_hits") or 0
        metrics.append({
            "model":      mid,
            "label":      fi["family"],
            "fb":         fi["fb"],
            "pts":        pts,
            "pts_prob":   pp,
            "max":        max_pts,
            "eff":        round(pts / max_pts, 4) if max_pts else 0.0,
            "eff_prob":   round(pp  / max_pts, 4) if max_pts else 0.0,
            "exact":      ex,
            "exact_prob": exact_prob.get(mid, 0),
            "winner":     winner_hits.get(mid, 0),
            "draws":      draw_picks.get(mid, 0),
            "is_ref":     mid in reference_models,
        })

    # Construir lista predictions en formato del diseño
    bt_predictions = []
    for p in predictions_raw:
        xg_a = p.get("expected_goals_a") or 0.0
        xg_b = p.get("expected_goals_b") or 0.0
        bt_predictions.append({
            "match":  f"{p.get('team_a_name', '?')} vs {p.get('team_b_name', '?')}",
            "model":  p["model_id"],
            "year":   p["year"],
            "phase":  p.get("stage", "group"),
            "result": p.get("actual_score") or "?",
            "smp":    p.get("selected_score") or "—",
            "pmp":    p.get("actual_points") or 0,
            "spr":    p.get("top_score") or "—",
            "ppr":    p.get("top_actual_points") or 0,
            "x1":     p.get("selected_outcome") or "—",
            "xp":     p.get("actual_outcome") or "—",
            "xg":     f"{float(xg_a):.2f}-{float(xg_b):.2f}",
        })

    ref_note = (
        run.get("notes")
        or "Los modelos marcados como Referencia fueron entrenados con datos que incluyen "
           "los torneos evaluados. Su rendimiento en backtest es optimista."
    )

    return {
        "run_id":      run.get("backtest_run_id", "—"),
        "years":       years,
        "ref_note":    ref_note,
        "metrics":     metrics,
        "predictions": bt_predictions,
    }


def _discover_scoring_profiles(
    project_root: Path,
    scoring_config_path: Path | None,
    default_predictions_path: Path | None,
) -> dict[str, Any]:
    config_path = scoring_config_path or project_root / "configs" / "scoring.yaml"
    if not config_path.exists():
        return {}
    raw = load_json_config(config_path)
    profiles = list_scoring_profiles(raw)
    default_name = raw.get("default_profile", next(iter(profiles), "default"))
    result: dict[str, Any] = {"default": default_name, "profiles": {}}
    for name, profile in profiles.items():
        entry: dict[str, Any] = {
            "label": profile.get("label", name),
            "exact_score": profile.get("exact_score", 5),
            "same_margin_or_draw": profile.get("same_margin_or_draw", 3),
            "winner": profile.get("winner", 1),
        }
        if name == default_name:
            entry["available"] = True
        else:
            alt_ui_dir = project_root / "data" / "ui" / f"scoring_{name}"
            alt_overrides = alt_ui_dir / "prediction_overrides.json"
            if alt_overrides.exists():
                entry["available"] = True
                entry["overrides"] = json.loads(alt_overrides.read_text(encoding="utf-8"))
            else:
                entry["available"] = False
        result["profiles"][name] = entry
    return result


def _build_unified_payload(
    state: dict[str, Any],
    matches: list[dict[str, Any]],
    group_tables: list[dict[str, Any]],
    predictions_overrides: dict[str, Any],
    backtest_data: dict[str, Any] | None,
    friends: list[dict[str, Any]] | None = None,
    scoring_profiles: dict[str, Any] | None = None,
    recent_friendlies: dict[str, dict[str, Any]] | None = None,
    friendly_coverage: dict[str, Any] | None = None,
    public_mode: bool = True,
    private_access_hash: str | None = None,
) -> dict[str, Any]:
    pred_matches = predictions_overrides.get("matches", {})
    recent_friendlies = recent_friendlies or {}

    # ── Sección 1: Grupos ──────────────────────────────────────────────────────
    groups_map: dict[str, dict[str, Any]] = {}
    for row in group_tables:
        gname = row.get("group_name", "?")
        if gname not in groups_map:
            groups_map[gname] = {"id": gname, "teams": []}
        tname = row.get("team_name", "?")
        if not any(t["t"] == tname for t in groups_map[gname]["teams"]):
            goals_for = int(row.get("goals_for") or 0)
            goals_against = int(row.get("goals_against") or 0)
            groups_map[gname]["teams"].append({
                "t": tname,
                "j": int(row.get("played") or 0),
                "g": int(row.get("wins") or 0),
                "e": int(row.get("draws") or 0),
                "p": int(row.get("losses") or 0),
                "gf": goals_for,
                "ga": goals_against,
                "gd": int(row.get("goal_difference") or (goals_for - goals_against)),
                "pts": int(row.get("points") or 0),
            })
    groups = [groups_map[k] for k in sorted(groups_map)]

    # ── Sección 2: Partidos ────────────────────────────────────────────────────
    unified_matches = []
    for seq, match in enumerate(matches, start=1):
        source_id = str(match.get("source_match_id", seq))
        overrides  = pred_matches.get(source_id, {})

        # 2a — Fecha y hora desde kickoff_local_iso ("2026-06-11T13:00:00-06:00")
        iso      = match.get("kickoff_local_iso") or ""
        date_str = iso[:10]
        time_str = iso[11:16]

        # 2b — Resultado (solo si el partido terminó)
        result: str | None = None
        hs  = match.get("home_score")
        aws = match.get("away_score")
        if match.get("status") in ("finished", "completed") and hs is not None and aws is not None:
            result = f"{hs}-{aws}"

        # 2c — Modelos
        model_preds = overrides.get("model_predictions", [])
        models_out = []
        for mp in model_preds:
            mid = mp.get("model_id", "")
            fi  = _FAMILY_BY_MODEL_ID.get(mid, {"family": mid.upper()[:12], "fb": "fb-ctrl"})
            models_out.append({
                "id":     mid,
                "family": fi["family"],
                "fb":     fi["fb"],
                "top":    mp.get("top_score") or "—",
                "score":  mp.get("score") or "—",
                "xg":     mp.get("expected_goals") or "0.00-0.00",
                "out":    mp.get("outcome") or "—",
                "conf":   float(mp.get("confidence") or 0),
                "p1":     float(mp.get("p_team_a_win") or 0),
                "px":     float(mp.get("p_draw") or 0),
                "p2":     float(mp.get("p_team_b_win") or 0),
                "ev":     float(mp.get("expected_points") or 0),
                "notes":  mp.get("notes") or "",
            })

        # 2d — Quiniela pick
        qpick  = overrides.get("quiniela_pick") or {}
        qmodel = qpick.get("model_id") or "weighted_points_ensemble"
        qscore = qpick.get("score") or "—"
        qtop   = qpick.get("top_score") or qscore
        qev    = float(qpick.get("expected_points") or 0)
        qprob  = float(qpick.get("top_score_probability") or 0)

        # 2e — Ensamble del partido
        stage = match.get("stage") or "group"
        home_friendlies = recent_friendlies.get(str(match.get("team_a_canonical_id") or ""), {})
        away_friendlies = recent_friendlies.get(str(match.get("team_b_canonical_id") or ""), {})
        home_goal_ref = home_friendlies.get("goal_ref")
        away_goal_ref = away_friendlies.get("goal_ref")
        unified_matches.append({
            "id":     seq,
            "num":    match.get("match_number") or seq,
            "home":   match.get("team_a_name") or "",
            "away":   match.get("team_b_name") or "",
            "date":   date_str,
            "time":   time_str,
            "venue":  match.get("stadium_name") or "",
            "city":   match.get("stadium_city") or "",
            "group":  match.get("group_name") or "",
            "phase":  _STAGE_TO_PHASE.get(stage, stage),
            "status": match.get("status") or "scheduled",
            "result": result,
            "quiniela": {
                "model": qmodel,
                "score": qscore,
                "top":   qtop,
                "ev":    qev,
                "prob":  qprob,
            },
            "frozen": bool(overrides.get("frozen_pick", False)),
            "knockout": overrides.get("knockout_resolution"),
            "models": models_out,
            "friendlies": {
                "home": home_friendlies.get("matches", []),
                "away": away_friendlies.get("matches", []),
            },
            "goal_ref": {
                "home": _matchup_goal_reference(home_goal_ref, away_goal_ref, friendly_coverage),
                "away": _matchup_goal_reference(away_goal_ref, home_goal_ref, friendly_coverage),
            },
        })

    # ── Sección 3: KPIs ───────────────────────────────────────────────────────
    played    = sum(1 for m in matches if m.get("status") in ("finished", "completed"))
    live      = sum(1 for m in matches if m.get("status") == "live")
    scheduled = sum(1 for m in matches if m.get("status") == "scheduled")
    locked    = sum(1 for v in pred_matches.values() if v.get("frozen_pick", False))

    # ── Sección 4: Meta ───────────────────────────────────────────────────────
    generated_at = _utc_now()
    phase_label  = state.get("phase_label") or state.get("current_phase") or "FIFA Mundial 2026"

    # ── Sección 5: Backtest ───────────────────────────────────────────────────
    backtest = backtest_data or {
        "run_id":      "—",
        "years":       [],
        "ref_note":    "Sin datos de backtest disponibles.",
        "metrics":     [],
        "predictions": [],
    }

    # ── Sección 6: Scoring Profiles ─────────────────────────────────────────
    sp = scoring_profiles or {}
    sp_payload: dict[str, Any] = {}
    default_profile = sp.get("default", "5-3-1")
    for pname, pinfo in sp.get("profiles", {}).items():
        entry: dict[str, Any] = {
            "label": pinfo["label"],
            "exact_score": pinfo.get("exact_score", 5),
            "same_margin_or_draw": pinfo.get("same_margin_or_draw", 3),
            "winner": pinfo.get("winner", 1),
            "ready": bool(pinfo.get("available")),
        }
        if pname != default_profile and "overrides" in pinfo:
            alt_matches = pinfo["overrides"].get("matches", {})
            alt_models: dict[str, Any] = {}
            for sid, ov in alt_matches.items():
                alt_qpick = ov.get("quiniela_pick") or {}
                alt_model_preds = []
                for amp in ov.get("model_predictions", []):
                    alt_model_preds.append({
                        "id":    amp.get("model_id", ""),
                        "score": amp.get("score") or "—",
                        "ev":    float(amp.get("expected_points") or 0),
                    })
                alt_models[sid] = {
                    "q": {
                        "model": alt_qpick.get("model_id") or "",
                        "score": alt_qpick.get("score") or "—",
                        "ev":    float(alt_qpick.get("expected_points") or 0),
                    },
                    "m": alt_model_preds,
                }
            entry["matches"] = alt_models
        sp_payload[pname] = entry

    return {
        "meta": {
            "generated_at": generated_at,
            "run_id":       state.get("state_id", ""),
            "phase":        phase_label,
            "friendlies":   friendly_coverage or {},
        },
        "kpis": {
            "total":     len(matches),
            "played":    played,
            "live":      live,
            "scheduled": scheduled,
            "locked":    locked,
        },
        "groups":   groups,
        "matches":  unified_matches,
        "backtest": backtest,
        "friends":  friends or [],
        "access": {
            "public_mode": bool(public_mode),
            "private_sections": ["amigos"],
            "private_hash": (private_access_hash or "").strip(),
        },
        "scoring":  {
            "active":   default_profile,
            "profiles": sp_payload,
        },
    }


def _build_payload(
    state: dict[str, Any],
    matches: list[dict[str, Any]],
    group_tables: list[dict[str, Any]],
    team_form: list[dict[str, Any]],
    predictions: dict[str, Any],
) -> dict[str, Any]:
    prediction_matches = predictions.get("matches", {})
    matches_by_group: dict[str, list[dict[str, Any]]] = {}
    knockout_matches: list[dict[str, Any]] = []
    enriched_matches = []

    for match in matches:
        match_id = str(match["source_match_id"])
        overrides = prediction_matches.get(match_id, {})
        enriched = {
            **match,
            "match_id": match_id,
            "result_label": _result_label(match),
            "score_label": _score_label(match),
            "model_predictions": overrides.get("model_predictions", []),
            "quiniela_pick": overrides.get("quiniela_pick"),
            "frozen_pick": bool(overrides.get("frozen_pick", False)),
            "notes": overrides.get("notes", ""),
        }
        enriched_matches.append(enriched)
        if _is_group_stage(match.get("stage")):
            matches_by_group.setdefault(match.get("group_name") or "?", []).append(enriched)
        else:
            knockout_matches.append(enriched)

    groups: dict[str, dict[str, Any]] = {}
    for row in group_tables:
        group_name = row["group_name"]
        groups.setdefault(group_name, {"group_name": group_name, "teams": [], "matches": []})
        groups[group_name]["teams"].append(row)

    for group_name, rows in matches_by_group.items():
        groups.setdefault(group_name, {"group_name": group_name, "teams": [], "matches": []})
        groups[group_name]["matches"] = rows

    return {
        "generated_at_utc": _utc_now(),
        "state": state,
        "groups": [groups[name] for name in sorted(groups)],
        "matches": enriched_matches,
        "knockout_matches": knockout_matches,
        "team_form": team_form,
        "has_predictions": any(
            match.get("model_predictions") or match.get("quiniela_pick") for match in enriched_matches
        ),
    }


def _render_html(payload: dict[str, Any]) -> str:
    template_path = Path(__file__).parent / "dashboard_template.html"
    template = template_path.read_text(encoding="utf-8")
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("const DATA = __DATA_JSON__;", f"const DATA = {safe_json};")


def _result_label(match: dict[str, Any]) -> str:
    if match.get("status") == "completed":
        return f"Final {match.get('home_score')} - {match.get('away_score')}"
    return "Programado"


def _score_label(match: dict[str, Any]) -> str:
    if match.get("status") == "completed":
        return f"{match.get('home_score')} - {match.get('away_score')}"
    return "vs"


def _is_group_stage(stage: Any) -> bool:
    return str(stage or "").strip().lower() in {"group", "groups", "group_stage", "group stage"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
