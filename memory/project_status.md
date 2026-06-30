---
name: project-status
description: "Current Quiniela2026 project state for future Codex sessions"
---

# Project Status - Quiniela2026

Current date of this status: 2026-06-13.

## Objective

Build a modular World Cup 2026 quiniela system that can be updated daily before matches.
The operational target is maximizing quiniela points, not only raw 1X2 accuracy.

Scoring priority:

- exact score
- draw or goal-difference/margin
- match winner

## Current Repository Policy

The repo is public and the dashboard is live on GitHub Pages:

```text
https://pmarze.github.io/Quiniela2026-Knockout/
```

Versioned:

- code in `src/` and `scripts/`
- configuration in `configs/`
- documentation in `docs/`
- memory handoff files in `memory/`
- curated small inputs in `curated_inputs/`
- final published models in `model_registry/`
- public dashboard output in `docs/index.html`
- public friend-pick comparison data in `data/ui/friends_quinielas.json`

Not versioned:

- downloaded/generated `data/`
- generated `outputs/`
- local training runs/checkpoints in `data/models/` and `data/models_local/`
- `.env`
- `configs/*.local.json`
- `.claude/settings.local.json`

Public/security restrictions:

- Friend picks are intentionally public through `data/ui/friends_quinielas.json`.
- Google Sheets source URL/ID for friend picks is private and must only live in `QUINIELA_FRIENDS_SHEET_*` environment variables or ignored local config.
- Do not commit credentials, local paths, tokens, private keys or generated local databases.
- Run `python scripts\security_scan_publish.py` before publication-oriented commits.

Collaborators should start with `docs/collaborator_onboarding.md`.

## Runtime

Use Conda environment:

```powershell
conda activate quiniela2026
```

Do not hardcode a Python executable path in docs or scripts. Project commands should be run from the repository root using the active environment.

## Rebuilding Local Artifacts

Base local rebuild:

```powershell
python scripts\bootstrap_data.py --preset base
```

Full rebuild with backtest, predictions and dashboards:

```powershell
python scripts\bootstrap_data.py --preset all
```

Artifact manifest:

- `configs/data_artifacts.json`

## Data Sources

Configured public/downloadable sources:

- World Cup 2026 fixture and metadata sources in `configs/sources.json`
- historical international results in `configs/history_sources.json`

Curated versioned input:

- `curated_inputs/opta/opta_power_ratings_20260607.json`

API sources that require credentials remain disabled unless configured later.

## Active Model Lineup

Configured in `configs/models.yaml`.

Main families:

- Elo Poisson
- Elo Dixon-Coles
- attack/defense Poisson
- draw specialist
- Bradley-Terry-Davidson
- Bayesian Monte Carlo scoreline
- Opta power Poisson
- neural scoreline MLP
- neural hybrid v2
- similar match KNN scoreline
- weighted ensembles
- calibrated scoreline ensemble

`baseline_poisson` is currently disabled and removed from dashboard `model_predictions`.
`similar_match_knn_scoreline` is active as an experimental standalone model, excluded from ensembles and excluded from automatic preferred-pick selection until more evidence is collected.

Current default quiniela model:

```text
weighted_points_ensemble
```

Before real 2026 results exist, the dashboard falls back to the points-oriented ensemble/default logic. Once real tournament results are present, `scripts/run_model.py` selects the operational quiniela model from the live 2026 ranking using frozen pre-match predictions. At the current handoff, after 3 completed matches, the live preferred model is `neural_scoreline_mlp`.

## Current 2026 State

Latest known state after the daily update:

- state_id: `state_20260613T051235Z_26d17845`
- as_of_utc: `2026-06-13T05:12:35Z`
- completed matches: 4
- pending matches: 100

Completed results:

- Mexico 2-0 South Africa
- South Korea 2-1 Czech Republic
- Canada 1-1 Bosnia and Herzegovina
- United States 4-1 Paraguay

Latest relevant prediction run:

- prediction_run_id: `pred_20260613T051336Z_4c5a6565`
- preferred model selected by live 2026 performance: `neural_scoreline_mlp`

## Published Models

Published neural models are stored in:

- `model_registry/neural_hybrid_v2/v2026-06-07`
- `model_registry/neural_scoreline_mlp/v2026-06-07`

Weights use Git LFS. After cloning:

```powershell
git lfs pull
```

Local training outputs should remain local until explicitly published with `scripts/publish_model.py`.

## Validation

Current backtest calibration focuses on 2018 and 2022 to avoid overfitting to older tournament behavior.

The ensemble optimizer uses saved score matrices from backtests and writes optimized weights into `configs/models.yaml`.

Key script:

```powershell
python scripts\optimize_ensemble_weights.py --iterations 8000
```

## Dashboard

The dashboard is a local offline HTML artifact generated from Python source/template files.

Important files:

- `src/quiniela/ui/dashboard.py`
- `src/quiniela/ui/dashboard_template.html`
- `docs/dashboard_reference.md`
- `memory/dashboard_status.md`

Generated files:

- `docs/index.html` (public dashboard output, includes `DATA.friends` when available)
- `outputs/dashboard/index.html`
- `outputs/validation_dashboard/index.html`

Do not edit generated HTML directly. Regenerate it from scripts.

Recent dashboard changes are summarized in `docs/knowledge/039_handoff_dashboard_y_operacion_2026_live.md`.
Publication/security policy is summarized in `docs/knowledge/040_publicacion_publica_dashboard_privado.md`.
GitHub Pages and daily automation are summarized in `docs/knowledge/041_pages_y_automatizacion_diaria.md`.

## Next Good Work Items

- Keep `docs/index.html` and `outputs/dashboard/index.html` synchronized after dashboard changes.
- Continue daily updates with `python scripts\daily_update.py --skip-git` before reviewing new results.
- For Claude Code daily automation, follow `CLAUDE.md` and `docs/daily_update_workflow.md`; push normal work to `development`, promote to `main` only when publishing live.
- Monitor the live 2026 model ranking; the preferred model can change as completed matches increase.
- Backtest and review `similar_match_knn_scoreline` before allowing it into ensembles or automatic preferred-pick selection.
