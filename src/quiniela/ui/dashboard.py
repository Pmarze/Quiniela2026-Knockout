from __future__ import annotations

import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
}

_STAGE_TO_PHASE: dict[str, str] = {
    "group":       "group",
    "round_of_16": "r16",
    "quarter":     "qf",
    "semi":        "sf",
    "final":       "final",
    "third_place": "3rd",
}


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
) -> DashboardResult:
    store = SQLiteStore(db_path)
    store.initialize()
    conn = store.conn
    try:
        state        = _load_latest_state(conn)
        matches      = _load_matches(conn)
        group_tables = _load_group_tables(conn)
        predictions  = _load_prediction_overrides(predictions_path)
        backtest     = _load_backtest_data(conn)
        friends      = _load_friends_quinielas(friends_path)
        payload      = _build_unified_payload(state, matches, group_tables, predictions, backtest, friends)
    finally:
        store.close()

    resolved_output = output_path or (project_root / "outputs" / "dashboard" / "index.html")
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


def _build_unified_payload(
    state: dict[str, Any],
    matches: list[dict[str, Any]],
    group_tables: list[dict[str, Any]],
    predictions_overrides: dict[str, Any],
    backtest_data: dict[str, Any] | None,
    friends: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pred_matches = predictions_overrides.get("matches", {})

    # ── Sección 1: Grupos ──────────────────────────────────────────────────────
    groups_map: dict[str, dict[str, Any]] = {}
    for row in group_tables:
        gname = row.get("group_name", "?")
        if gname not in groups_map:
            groups_map[gname] = {"id": gname, "teams": []}
        tname = row.get("team_name", "?")
        if not any(t["t"] == tname for t in groups_map[gname]["teams"]):
            groups_map[gname]["teams"].append({"t": tname})
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
            "models": models_out,
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

    return {
        "meta": {
            "generated_at": generated_at,
            "run_id":       state.get("state_id", ""),
            "phase":        phase_label,
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
