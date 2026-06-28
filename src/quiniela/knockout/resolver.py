from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quiniela.knockout.extra_time import simulate_extra_time
from quiniela.knockout.penalties import simulate_penalty_shootout
from quiniela.models.common import ModelPrediction


@dataclass(frozen=True)
class KnockoutResolution:
    model_id: str
    match_id: str

    p_draw_90: float
    p_a_wins_90: float
    p_b_wins_90: float

    p_reaches_et: float
    p_a_wins_et: float
    p_b_wins_et: float
    p_reaches_penalties: float

    p_a_wins_penalties: float
    p_b_wins_penalties: float

    p_a_advances: float
    p_b_advances: float

    most_likely_path: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "match_id": self.match_id,
            "p_draw_90": round(self.p_draw_90, 4),
            "p_a_wins_90": round(self.p_a_wins_90, 4),
            "p_b_wins_90": round(self.p_b_wins_90, 4),
            "p_reaches_et": round(self.p_reaches_et, 4),
            "p_a_wins_et": round(self.p_a_wins_et, 4),
            "p_b_wins_et": round(self.p_b_wins_et, 4),
            "p_reaches_penalties": round(self.p_reaches_penalties, 4),
            "p_a_wins_penalties": round(self.p_a_wins_penalties, 4),
            "p_b_wins_penalties": round(self.p_b_wins_penalties, 4),
            "p_a_advances": round(self.p_a_advances, 4),
            "p_b_advances": round(self.p_b_advances, 4),
            "most_likely_path": self.most_likely_path,
            "confidence": round(self.confidence, 4),
        }


def resolve_knockout_outcome(
    prediction: ModelPrediction,
    knockout_config: dict[str, Any],
) -> KnockoutResolution | None:
    if prediction.status != "ok" or prediction.p_draw is None:
        return None

    p_a_wins_90 = prediction.p_team_a_win or 0.0
    p_draw_90 = prediction.p_draw or 0.0
    p_b_wins_90 = prediction.p_team_b_win or 0.0

    lambda_a = prediction.expected_goals_a or 1.0
    lambda_b = prediction.expected_goals_b or 1.0

    et = simulate_extra_time(lambda_a, lambda_b, knockout_config)
    pens = simulate_penalty_shootout(knockout_config)

    p_reaches_et = p_draw_90

    p_a_wins_et_unconditional = p_reaches_et * et["p_a_wins_et"]
    p_b_wins_et_unconditional = p_reaches_et * et["p_b_wins_et"]
    p_reaches_penalties = p_reaches_et * et["p_still_tied"]

    p_a_wins_pens_unconditional = p_reaches_penalties * pens["p_a_wins_penalties"]
    p_b_wins_pens_unconditional = p_reaches_penalties * pens["p_b_wins_penalties"]

    p_a_advances = p_a_wins_90 + p_a_wins_et_unconditional + p_a_wins_pens_unconditional
    p_b_advances = p_b_wins_90 + p_b_wins_et_unconditional + p_b_wins_pens_unconditional

    total = p_a_advances + p_b_advances
    if total > 0:
        p_a_advances /= total
        p_b_advances /= total

    paths = {
        "regulation_a": p_a_wins_90,
        "regulation_b": p_b_wins_90,
        "et_a": p_a_wins_et_unconditional,
        "et_b": p_b_wins_et_unconditional,
        "penalties_a": p_a_wins_pens_unconditional,
        "penalties_b": p_b_wins_pens_unconditional,
    }
    most_likely_path = max(paths, key=paths.get)

    return KnockoutResolution(
        model_id=prediction.model_id,
        match_id=prediction.source_match_id,
        p_draw_90=p_draw_90,
        p_a_wins_90=p_a_wins_90,
        p_b_wins_90=p_b_wins_90,
        p_reaches_et=p_reaches_et,
        p_a_wins_et=et["p_a_wins_et"],
        p_b_wins_et=et["p_b_wins_et"],
        p_reaches_penalties=p_reaches_penalties,
        p_a_wins_penalties=pens["p_a_wins_penalties"],
        p_b_wins_penalties=pens["p_b_wins_penalties"],
        p_a_advances=p_a_advances,
        p_b_advances=p_b_advances,
        most_likely_path=most_likely_path,
        confidence=max(p_a_advances, p_b_advances),
    )


def build_knockout_consensus(
    resolutions: list[KnockoutResolution],
    knockout_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not resolutions:
        return {}

    config = knockout_config or {}
    et_threshold = float(config.get("et_display_threshold", 0.25))
    pens_threshold = float(config.get("penalties_display_threshold", 0.10))

    models_predict_et = []
    models_predict_penalties = []
    sum_p_a_advances = 0.0
    sum_p_b_advances = 0.0
    path_votes: dict[str, int] = {}

    for r in resolutions:
        if r.p_reaches_et >= et_threshold:
            models_predict_et.append(r.model_id)
        if r.p_reaches_penalties >= pens_threshold:
            models_predict_penalties.append(r.model_id)

        sum_p_a_advances += r.p_a_advances
        sum_p_b_advances += r.p_b_advances

        base_path = r.most_likely_path.rsplit("_", 1)[0]
        path_votes[base_path] = path_votes.get(base_path, 0) + 1

    n = len(resolutions)
    consensus_path = max(path_votes, key=path_votes.get) if path_votes else "regulation"

    avg_p_et = sum(r.p_reaches_et for r in resolutions) / n
    avg_p_pens = sum(r.p_reaches_penalties for r in resolutions) / n

    return {
        "n_models": n,
        "models_predict_et": models_predict_et,
        "n_predict_et": len(models_predict_et),
        "models_predict_penalties": models_predict_penalties,
        "n_predict_penalties": len(models_predict_penalties),
        "consensus_p_a_advances": round(sum_p_a_advances / n, 4),
        "consensus_p_b_advances": round(sum_p_b_advances / n, 4),
        "consensus_path": consensus_path,
        "avg_p_et": round(avg_p_et, 4),
        "avg_p_pens": round(avg_p_pens, 4),
    }
