"""
SDR-Poisson experimental model.

Replicates the methodology from:
  Rezaei & Samadi (2026) "Predicting the 2026 FIFA World Cup with Sufficient
  Dimension Reduction of Elo Rating Histories" — arXiv:2606.24171

Key idea: instead of using only the current Elo difference as the Poisson
predictor, we build a K=6 vector of lagged monthly Elo differences and reduce
it to d=1 or d=2 informative directions via categorical SDR (SIR / SAVE).
Those reduced scores feed a Poisson double-regression model exactly like M3/M8-M11
in the paper.

This module is self-contained and does NOT modify any production model.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from quiniela.models.common import (
    ModelContext,
    ModelPrediction,
    clamp,
    failed_prediction,
    mask_reason_for_match,
    masked_prediction,
    successful_prediction,
)
from quiniela.scoring import select_best_score


MODEL_ID = "elo_sdr_poisson"

# ---------------------------------------------------------------------------
# Public entry point (matches the interface of all other model runners)
# ---------------------------------------------------------------------------

def run_elo_sdr_poisson(
    context: ModelContext,
    model_config: dict[str, Any],
    scoring_config: dict[str, Any],
) -> list[ModelPrediction]:
    model_version = str(model_config.get("model_version", "0.1.0"))
    cfg = _config(model_config)

    # 1. Build monthly Elo snapshots from the full training history
    monthly_elo = _build_monthly_elo(context.training_matches, cfg)

    # 2. Build rolling-form lookup (goals scored/conceded in last 6 matches)
    rolling_form = _build_rolling_form(context.training_matches)

    # 3. Fit the SDR + Poisson model on training data
    model = _fit_sdr_poisson(context.training_matches, monthly_elo, rolling_form, cfg)

    # 4. Predict
    predictions = []
    for match in context.prediction_matches:
        mask_reason = mask_reason_for_match(match)
        if mask_reason:
            predictions.append(masked_prediction(context, MODEL_ID, model_version, match, mask_reason))
            continue
        if not match.team_a_key or not match.team_b_key:
            predictions.append(failed_prediction(context, MODEL_ID, model_version, match, "faltan identificadores"))
            continue

        result = _predict_match(match, model, monthly_elo, rolling_form, cfg)
        if result is None:
            predictions.append(failed_prediction(context, MODEL_ID, model_version, match, "no hay historial Elo suficiente"))
            continue

        lambda_a, lambda_b, warnings = result
        preview = successful_prediction(
            context=context, model_id=MODEL_ID, model_version=model_version,
            match=match, lambda_a=lambda_a, lambda_b=lambda_b,
            max_goals=cfg["max_goals"], selected_score=None, selected_expected_points=None,
            warnings=warnings,
        )
        selected = select_best_score(preview.score_matrix or {}, scoring_config)
        predictions.append(successful_prediction(
            context=context, model_id=MODEL_ID, model_version=model_version,
            match=match, lambda_a=lambda_a, lambda_b=lambda_b,
            max_goals=cfg["max_goals"],
            selected_score=selected["score"],
            selected_expected_points=selected["expected_points"],
            warnings=warnings,
        ))
    return predictions


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _config(model_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_goals": int(model_config.get("max_goals", 8)),
        "initial_rating": float(model_config.get("initial_rating", 1500.0)),
        # κ by match importance (paper values)
        "k_wc": float(model_config.get("k_wc", 60.0)),
        "k_continental": float(model_config.get("k_continental", 35.0)),
        "k_qualifier": float(model_config.get("k_qualifier", 25.0)),
        "k_friendly": float(model_config.get("k_friendly", 20.0)),
        # SDR settings
        "n_lags": int(model_config.get("n_lags", 6)),      # K in the paper
        "sdr_dims": int(model_config.get("sdr_dims", 2)),  # d=1 or d=2
        "sdr_method": str(model_config.get("sdr_method", "sir")),  # "sir" or "save"
        # Poisson clamps
        "min_lambda": float(model_config.get("min_lambda", 0.15)),
        "max_lambda": float(model_config.get("max_lambda", 5.0)),
        # L2 penalty on SDR-score coefficients (xi) to prevent extreme lambdas
        "l2_xi": float(model_config.get("l2_xi", 0.0)),
        # Training filter: only use matches from this year onwards for SDR fit
        "sdr_min_year": int(model_config.get("sdr_min_year", 2010)),
    }


# ---------------------------------------------------------------------------
# Step 1: Monthly Elo snapshots
# ---------------------------------------------------------------------------

def _kappa(match: Any, cfg: dict[str, Any]) -> float:
    if getattr(match, "is_world_cup", 0):
        return cfg["k_wc"]
    if getattr(match, "is_qualifier", 0):
        return cfg["k_qualifier"]
    if getattr(match, "is_friendly", 0):
        return cfg["k_friendly"]
    # continental championships and other competitive matches
    tournament = str(getattr(match, "tournament", "") or "").lower()
    if any(t in tournament for t in ("euro", "copa america", "africa cup", "afcon", "gold cup", "asian cup", "nations")):
        return cfg["k_continental"]
    return cfg["k_friendly"]


def _gamma(goal_diff: int) -> float:
    """Goal-difference multiplier from the paper (eq. 2)."""
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11 + goal_diff) / 8.0


def _build_monthly_elo(
    training_matches: list[Any],
    cfg: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """
    Process matches chronologically and record end-of-month Elo for every team.

    Returns: {team_key: {year_month_str: elo_rating}}
             where year_month_str = "YYYY-MM"
    """
    initial = cfg["initial_rating"]
    ratings: dict[str, float] = defaultdict(lambda: initial)

    # monthly snapshot: {team: {month: rating}}
    monthly: dict[str, dict[str, float]] = defaultdict(dict)

    current_month = ""

    for match in training_matches:
        date = str(match.match_date or "")
        if len(date) < 7:
            continue
        month = date[:7]  # "YYYY-MM"

        # When we cross into a new month, record the current rating of every
        # team that has played at least once.
        if month != current_month:
            for team, rating in ratings.items():
                # Only write if no snapshot yet for this month
                if month not in monthly[team]:
                    monthly[team][month] = rating
            current_month = month

        a = match.team_a_key
        b = match.team_b_key
        if not a or not b:
            continue

        r_a = ratings[a]
        r_b = ratings[b]
        neutral = bool(getattr(match, "neutral", 1))
        home_adv = 0.0 if neutral else 100.0  # paper uses ζ=100 for non-neutral
        expected_a = 1.0 / (1.0 + 10.0 ** (-((r_a + home_adv) - r_b) / 400.0))
        actual_a = 1.0 if match.home_score > match.away_score else (0.5 if match.home_score == match.away_score else 0.0)
        kappa = _kappa(match, cfg)
        gd = abs(match.home_score - match.away_score)
        gamma = _gamma(gd)
        delta = kappa * gamma * (actual_a - expected_a)
        ratings[a] = r_a + delta
        ratings[b] = r_b - delta

        # Update snapshot for current month after each match
        monthly[a][month] = ratings[a]
        monthly[b][month] = ratings[b]

    return dict(monthly)


# ---------------------------------------------------------------------------
# Step 2: Rolling form (goals scored / conceded in last 6 matches per team)
# ---------------------------------------------------------------------------

def _build_rolling_form(training_matches: list[Any]) -> dict[str, list[tuple[str, int, int]]]:
    """
    Returns {team_key: [(date, goals_scored, goals_conceded), ...]} sorted by date.
    Each entry is from the perspective of that team.
    """
    form: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for m in training_matches:
        if not m.team_a_key or not m.team_b_key:
            continue
        d = str(m.match_date or "")
        form[m.team_a_key].append((d, int(m.home_score), int(m.away_score)))
        form[m.team_b_key].append((d, int(m.away_score), int(m.home_score)))
    for key in form:
        form[key].sort(key=lambda x: x[0])
    return dict(form)


def _rolling_goals(form: dict[str, list[tuple[str, int, int]]], team: str, before_date: str, n: int = 6) -> tuple[float, float]:
    """Return (mean_scored, mean_conceded) in the n matches before before_date."""
    entries = [e for e in form.get(team, []) if e[0] < before_date]
    if not entries:
        return 1.2, 1.2  # global average fallback
    recent = entries[-n:]
    scored = sum(e[1] for e in recent) / len(recent)
    conceded = sum(e[2] for e in recent) / len(recent)
    return scored, conceded


# ---------------------------------------------------------------------------
# Step 3: Build feature vectors for training the SDR+Poisson model
# ---------------------------------------------------------------------------

def _get_elo_lag_vector(
    team_a: str,
    team_b: str,
    match_month: str,
    monthly_elo: dict[str, dict[str, float]],
    cfg: dict[str, Any],
) -> np.ndarray | None:
    """Build K-dimensional vector of lagged monthly Elo differences."""
    n_lags = cfg["n_lags"]
    initial = cfg["initial_rating"]

    # Parse YYYY-MM
    try:
        year, mon = int(match_month[:4]), int(match_month[5:7])
    except (ValueError, IndexError):
        return None

    elo_a = monthly_elo.get(team_a, {})
    elo_b = monthly_elo.get(team_b, {})

    diffs = []
    for lag in range(n_lags):
        m = mon - lag
        y = year
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y:04d}-{m:02d}"
        ra = elo_a.get(key, initial)
        rb = elo_b.get(key, initial)
        diffs.append(ra - rb)

    return np.array(diffs, dtype=float)


def _build_training_features(
    training_matches: list[Any],
    monthly_elo: dict[str, dict[str, float]],
    rolling_form: dict[str, list[tuple[str, int, int]]],
    cfg: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Build arrays for SDR fitting and Poisson regression.

    Returns:
        X:   (N, K) lagged Elo-diff features
        Y:   (N,) outcome class 0=home, 1=draw, 2=away
        G:   (N, 2) [goals_home, goals_away]
        Z:   (N, 4) form features [G+h, G-a, G+a, G-h]
    """
    min_year = cfg["sdr_min_year"]
    X_list, Y_list, G_list, Z_list = [], [], [], []

    for m in training_matches:
        date = str(m.match_date or "")
        if len(date) < 7:
            continue
        try:
            year = int(date[:4])
        except ValueError:
            continue
        if year < min_year:
            continue
        if not m.team_a_key or not m.team_b_key:
            continue

        month = date[:7]
        x = _get_elo_lag_vector(m.team_a_key, m.team_b_key, month, monthly_elo, cfg)
        if x is None:
            continue

        if m.home_score > m.away_score:
            y = 0
        elif m.home_score == m.away_score:
            y = 1
        else:
            y = 2

        neutral = bool(getattr(m, "neutral", 1))
        gp_a, gc_a = _rolling_goals(rolling_form, m.team_a_key, date)
        gp_b, gc_b = _rolling_goals(rolling_form, m.team_b_key, date)
        # form: [G+home, G-home(=conceded), G+away, G-away(=conceded)]
        # For Poisson home: η1*G+h + η2*G-a  (a=away team as in the paper)
        # note: in the paper "away" denotes the second team so G-a = goals conceded by away
        z = np.array([gp_a, gc_b, gp_b, gc_a], dtype=float)  # [G+h, G-a(conceded), G+a, G-h(conceded)]

        neutral_flag = 1.0 if neutral else 0.0

        X_list.append(np.append(x, neutral_flag))  # append neutral indicator
        Y_list.append(y)
        G_list.append([int(m.home_score), int(m.away_score)])
        Z_list.append(z)

    if len(X_list) < 30:
        return np.empty((0, cfg["n_lags"] + 1)), np.empty(0), np.empty((0, 2)), np.empty((0, 4))

    X_full = np.array(X_list, dtype=float)  # (N, K+1)  last col = neutral
    X = X_full[:, : cfg["n_lags"]]           # (N, K)    only the Elo diffs for SDR
    neutral_col = X_full[:, cfg["n_lags"]]   # (N,)
    Y = np.array(Y_list, dtype=int)
    G = np.array(G_list, dtype=float)
    Z = np.array(Z_list, dtype=float)

    return X, Y, G, Z, neutral_col  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Step 4: SDR — SIR or SAVE
# ---------------------------------------------------------------------------

def _whitening_params(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (mean, Sigma^{-1/2}) for whitening X."""
    mu = X.mean(axis=0)
    Xc = X - mu
    cov = Xc.T @ Xc / max(len(X) - 1, 1)
    # Regularise slightly
    cov += np.eye(cov.shape[0]) * 1e-6
    # Eigendecomposition: cov = V D V^T, so cov^{-1/2} = V D^{-1/2} V^T
    vals, vecs = np.linalg.eigh(cov)
    vals = np.maximum(vals, 1e-10)
    inv_sqrt = vecs @ np.diag(vals ** -0.5) @ vecs.T
    return mu, inv_sqrt


def _sir_kernel(X_w: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """SIR kernel: weighted covariance of class-conditional means."""
    classes = np.unique(Y)
    n = len(Y)
    K = X_w.shape[1]
    M = np.zeros((K, K))
    for c in classes:
        idx = Y == c
        pi_c = idx.sum() / n
        mu_c = X_w[idx].mean(axis=0)
        M += pi_c * np.outer(mu_c, mu_c)
    return M


def _save_kernel(X_w: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """SAVE kernel: weighted squared deviation of within-class covariances from I."""
    classes = np.unique(Y)
    n = len(Y)
    K = X_w.shape[1]
    I_K = np.eye(K)
    M = np.zeros((K, K))
    for c in classes:
        idx = Y == c
        pi_c = idx.sum() / n
        Xc = X_w[idx]
        nc = len(Xc)
        if nc > 1:
            sigma_c = Xc.T @ Xc / (nc - 1)
        else:
            sigma_c = I_K.copy()
        diff = I_K - sigma_c
        M += pi_c * (diff @ diff)
    return M


def _sdr_projection(X: np.ndarray, Y: np.ndarray, d: int, method: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Fit SDR, return (mu, inv_sqrt, B) where B is (K, d) projection matrix.
    Scores z_m = B^T inv_sqrt (x_m - mu).
    """
    mu, inv_sqrt = _whitening_params(X)
    X_w = (X - mu) @ inv_sqrt.T

    if method == "save":
        M = _save_kernel(X_w, Y)
    else:
        M = _sir_kernel(X_w, Y)

    vals, vecs = np.linalg.eigh(M)
    # eigh returns ascending eigenvalues; take top d
    order = np.argsort(vals)[::-1]
    B = vecs[:, order[:d]]  # (K, d)
    return mu, inv_sqrt, B


# ---------------------------------------------------------------------------
# Step 5: Poisson regression via gradient descent on NLL
# ---------------------------------------------------------------------------

def _poisson_nll_grad(params: np.ndarray, z: np.ndarray, neutral: np.ndarray, form: np.ndarray, goals: np.ndarray, d: int, l2_xi: float = 0.0) -> tuple[float, np.ndarray]:
    """
    Compute Poisson NLL and gradient w.r.t. params.

    params layout (for d SDR directions):
      [mu_H, mu_A, xi_1..xi_d, delta_H, delta_A, eta_1, eta_2, eta_3, eta_4]
    = 2 + d + 2 + 4 = d + 8 parameters

    log lambda_H = mu_H + sum_j xi_j z_j + delta_H * N + eta_1 G+h + eta_2 G-a
    log lambda_A = mu_A - sum_j xi_j z_j + delta_A * N + eta_3 G+a + eta_4 G-h
    """
    n = len(goals)
    mu_H = params[0]
    mu_A = params[1]
    xi = params[2:2 + d]
    delta_H = params[2 + d]
    delta_A = params[2 + d + 1]
    eta = params[2 + d + 2:]  # [eta_1, eta_2, eta_3, eta_4]

    # z: (N, d), neutral: (N,), form: (N, 4)=[G+h, G-a, G+a, G-h]
    sdr_contribution = z @ xi                     # (N,)
    log_lH = mu_H + sdr_contribution + delta_H * neutral + eta[0] * form[:, 0] + eta[1] * form[:, 1]
    log_lA = mu_A - sdr_contribution + delta_A * neutral + eta[2] * form[:, 2] + eta[3] * form[:, 3]

    lH = np.exp(np.clip(log_lH, -5.0, 4.0))
    lA = np.exp(np.clip(log_lA, -5.0, 4.0))

    gH = goals[:, 0]
    gA = goals[:, 1]

    nll = np.sum(lH - gH * log_lH) + np.sum(lA - gA * log_lA)
    # L2 penalty on xi only (not intercepts/form/venue)
    if l2_xi > 0.0:
        nll += l2_xi * float(np.sum(xi ** 2))

    # Gradients
    rH = lH - gH  # (N,)
    rA = lA - gA  # (N,)

    g_mu_H = np.sum(rH)
    g_mu_A = np.sum(rA)
    g_xi = z.T @ rH - z.T @ rA + 2.0 * l2_xi * xi  # (d,)
    g_delta_H = np.sum(rH * neutral)
    g_delta_A = np.sum(rA * neutral)
    g_eta = np.array([
        np.sum(rH * form[:, 0]),
        np.sum(rH * form[:, 1]),
        np.sum(rA * form[:, 2]),
        np.sum(rA * form[:, 3]),
    ])

    grad = np.concatenate([[g_mu_H, g_mu_A], g_xi, [g_delta_H, g_delta_A], g_eta])
    return float(nll), grad


def _fit_poisson(z: np.ndarray, neutral: np.ndarray, form: np.ndarray, goals: np.ndarray, d: int, l2_xi: float = 0.0, max_iter: int = 3000) -> np.ndarray:
    """Minimise Poisson NLL via gradient descent (Adam)."""
    n_params = 2 + d + 2 + 4
    params = np.zeros(n_params)
    params[0] = math.log(goals[:, 0].mean() + 1e-6)
    params[1] = math.log(goals[:, 1].mean() + 1e-6)

    lr = 1e-2
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    m_adam = np.zeros(n_params)
    v_adam = np.zeros(n_params)
    for t in range(1, max_iter + 1):
        _, grad = _poisson_nll_grad(params, z, neutral, form, goals, d, l2_xi)
        m_adam = beta1 * m_adam + (1 - beta1) * grad
        v_adam = beta2 * v_adam + (1 - beta2) * grad ** 2
        m_hat = m_adam / (1 - beta1 ** t)
        v_hat = v_adam / (1 - beta2 ** t)
        params -= lr * m_hat / (np.sqrt(v_hat) + eps)
    return params


# ---------------------------------------------------------------------------
# Step 6: Assemble the fitted model
# ---------------------------------------------------------------------------

def _fit_sdr_poisson(
    training_matches: list[Any],
    monthly_elo: dict[str, dict[str, float]],
    rolling_form: dict[str, list[tuple[str, int, int]]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    result = _build_training_features(training_matches, monthly_elo, rolling_form, cfg)
    if len(result) != 5:
        return {"ok": False, "reason": "build_features returned wrong shape"}
    X, Y, G, Z, neutral_col = result

    if X.shape[0] < 30:
        return {"ok": False, "reason": f"very few training samples ({X.shape[0]})"}

    d = min(cfg["sdr_dims"], 2)
    method = cfg["sdr_method"]

    mu_sdr, inv_sqrt, B = _sdr_projection(X, Y, d=d, method=method)

    # Project training data to SDR scores
    X_w = (X - mu_sdr) @ inv_sqrt.T
    z = X_w @ B  # (N, d)

    # Fit Poisson regression
    poisson_params = _fit_poisson(z, neutral_col, Z, G, d, l2_xi=cfg.get("l2_xi", 0.0))

    return {
        "ok": True,
        "mu_sdr": mu_sdr,
        "inv_sqrt": inv_sqrt,
        "B": B,
        "poisson_params": poisson_params,
        "d": d,
        "cfg": cfg,
    }


# ---------------------------------------------------------------------------
# Step 7: Predict a single match
# ---------------------------------------------------------------------------

def _predict_match(
    match: Any,
    model: dict[str, Any],
    monthly_elo: dict[str, dict[str, float]],
    rolling_form: dict[str, list[tuple[str, int, int]]],
    cfg: dict[str, Any],
) -> tuple[float, float, list[str]] | None:
    if not model.get("ok"):
        return None

    date = str(getattr(match, "kickoff_utc", None) or "")
    if not date:
        return None
    match_month = date[:7]
    match_date_str = date[:10]

    x = _get_elo_lag_vector(match.team_a_key, match.team_b_key, match_month, monthly_elo, cfg)
    if x is None:
        return None

    mu_sdr = model["mu_sdr"]
    inv_sqrt = model["inv_sqrt"]
    B = model["B"]
    params = model["poisson_params"]
    d = model["d"]

    x_w = (x - mu_sdr) @ inv_sqrt.T
    z = B.T @ x_w  # (d,)

    neutral = 1.0  # WC matches are neutral
    gp_a, gc_a = _rolling_goals(rolling_form, match.team_a_key, match_date_str)
    gp_b, gc_b = _rolling_goals(rolling_form, match.team_b_key, match_date_str)
    form = np.array([gp_a, gc_b, gp_b, gc_a])

    mu_H = params[0]
    mu_A = params[1]
    xi = params[2:2 + d]
    delta_H = params[2 + d]
    delta_A = params[2 + d + 1]
    eta = params[2 + d + 2:]

    sdr_score = float(z @ xi)
    log_lH = mu_H + sdr_score + delta_H * neutral + eta[0] * form[0] + eta[1] * form[1]
    log_lA = mu_A - sdr_score + delta_A * neutral + eta[2] * form[2] + eta[3] * form[3]

    lambda_a = clamp(math.exp(log_lH), cfg["min_lambda"], cfg["max_lambda"])
    lambda_b = clamp(math.exp(log_lA), cfg["min_lambda"], cfg["max_lambda"])

    warnings: list[str] = []
    if match.team_a_key not in monthly_elo:
        warnings.append(f"sin historial Elo mensual: {match.team_a_key}")
    if match.team_b_key not in monthly_elo:
        warnings.append(f"sin historial Elo mensual: {match.team_b_key}")

    return lambda_a, lambda_b, warnings
